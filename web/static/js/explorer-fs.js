    /* ─────────────────────────────────────────────
       Explorer create / copy / cut / paste / move / delete controller.

       Clipboard, confirmations, requests, busy state, and refreshes are bound
       to an immutable session id + pane reference + root revision. Nothing is
       persisted and no operation can cross Explorer sessions.
    ───────────────────────────────────────────── */

    const explorerFilesystemClipboards = new Map();
    const explorerFilesystemActionTokens = new Map();
    const explorerFilesystemInFlightSessions = new Set();
    let explorerFilesystemTokenCounter = 0;
    let explorerFilesystemMenuSessionId = '';
    let explorerCreateDialogState = null;

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

    function refreshExplorerFilesystemCutSource(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const card = document.getElementById(`tc-${index}`);
        card?.querySelectorAll('.explorer-fs-cut-source')
            .forEach(node => node.classList.remove('explorer-fs-cut-source'));
        const clipboard = explorerFilesystemClipboards.get(sessionId);
        if (
            !pane
            || !clipboard
            || clipboard.mode !== 'cut'
            || clipboard.rootRevision !== pane._explorerRootRevision
        ) {
            return;
        }
        card?.querySelectorAll('[data-explorer-context-path]').forEach(node => {
            if (node.dataset.explorerContextPath === clipboard.path) {
                node.classList.add('explorer-fs-cut-source');
            }
        });
    }

    function clearExplorerFilesystemClipboard(sessionId) {
        const key = String(sessionId || '');
        explorerFilesystemClipboards.delete(key);
        const index = sessionIds.indexOf(key);
        if (index !== -1) {
            refreshExplorerFilesystemCutSource(index);
        }
    }

    function updateExplorerFilesystemRootRevision(index, revision) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        if (!pane || !sessionId) {
            return;
        }
        const nextRevision = String(revision || '');
        if (pane._explorerRootRevision && pane._explorerRootRevision !== nextRevision) {
            clearExplorerFilesystemClipboard(sessionId);
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
        if (explorerCreateDialogState && !explorerCreateDialogState.requestStarted) {
            closeExplorerCreateModal();
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

    function explorerFilesystemDestination(context) {
        if (context.kind === 'directory') {
            return context.path;
        }
        if (context.surface === 'preview') {
            return context.paneRef._explorerPath || '';
        }
        return explorerFilesystemParentPath(context.path);
    }

    function setExplorerFilesystemClipboard(context, mode) {
        if (!isExplorerFsActionContextCurrent(context) || !context.revision) {
            return;
        }
        const clipboardMode = mode === 'cut' ? 'cut' : 'copy';
        explorerFilesystemClipboards.set(context.sessionId, {
            sessionId: context.sessionId,
            rootRevision: context.rootRevision,
            path: context.path,
            kind: context.kind,
            name: explorerFilesystemBaseName(context.path),
            revision: context.revision,
            mode: clipboardMode,
            copiedAt: Date.now()
        });
        refreshExplorerFilesystemCutSource(context.index);
        showTerminalToast(
            `${clipboardMode === 'cut' ? 'Cut' : 'Copied'} ${explorerFilesystemBaseName(context.path)}`,
            'success'
        );
    }

    function copyExplorerFilesystemEntry(context) {
        setExplorerFilesystemClipboard(context, 'copy');
    }

    function cutExplorerFilesystemEntry(context) {
        setExplorerFilesystemClipboard(context, 'cut');
    }

    function explorerFilesystemMenuItems(index, rowContext) {
        const context = explorerFilesystemActionContext(index, rowContext);
        if (!context) {
            return [];
        }
        const entryActionable = Boolean(context.path && context.revision);
        const copyable = entryActionable
            && (context.kind === 'file' || context.kind === 'directory');
        let clipboard = explorerFilesystemClipboards.get(context.sessionId) || null;
        if (clipboard && clipboard.rootRevision !== context.rootRevision) {
            clearExplorerFilesystemClipboard(context.sessionId);
            clipboard = null;
        }
        const destination = explorerFilesystemDestination(context);
        const destinationName = explorerFilesystemBaseName(destination);
        const clipboardMode = clipboard?.mode === 'cut' ? 'cut' : 'copy';
        const moveWithinSameFolder = Boolean(
            clipboard
            && clipboardMode === 'cut'
            && explorerFilesystemParentPath(clipboard.path) === destination
        );
        const moveIntoItself = Boolean(
            clipboard
            && clipboardMode === 'cut'
            && clipboard.kind === 'directory'
            && explorerFilesystemPathContains(clipboard.path, destination)
        );
        const pasteDisabled = !clipboard || moveWithinSameFolder || moveIntoItself;
        const pasteVerb = clipboardMode === 'cut' ? 'Move' : 'Paste';
        const pasteLabel = clipboard
            ? (
                context.kind === 'file'
                    ? `${pasteVerb} "${clipboard.name}" in containing folder`
                    : `${pasteVerb} "${clipboard.name}" into "${destinationName || 'root'}"`
            )
            : 'Paste — nothing copied';
        let pasteTitle = clipboard
            ? `${clipboardMode === 'cut' ? 'Move' : 'Copy'} ${clipboard.path} into ${destination || 'the explorer root'}`
            : 'Copy or cut a file or folder in this Explorer session first';
        if (moveWithinSameFolder) {
            pasteTitle = `"${clipboard.name}" is already in this folder`;
        } else if (moveIntoItself) {
            pasteTitle = 'A folder cannot be moved into itself';
        }
        const items = [
            {
                label: 'New file…',
                action: () => openExplorerCreateModal(context, 'file', destination)
            },
            {
                label: 'New folder…',
                action: () => openExplorerCreateModal(context, 'directory', destination)
            }
        ];
        if (copyable) {
            items.push({
                label: 'Copy',
                separatorBefore: true,
                action: () => copyExplorerFilesystemEntry(context)
            });
            items.push({
                label: 'Cut',
                action: () => cutExplorerFilesystemEntry(context)
            });
        }
        items.push({
            label: pasteLabel,
            title: pasteTitle,
            separatorBefore: !copyable,
            disabled: pasteDisabled,
            action: clipboard && !pasteDisabled
                ? (
                    clipboardMode === 'cut'
                        ? () => moveExplorerFilesystemEntry(context, clipboard, destination)
                        : () => pasteExplorerFilesystemEntry(context, clipboard, destination)
                )
                : null
        });
        if (entryActionable) {
            items.push({
                label: 'Delete…',
                title: `Permanently delete ${context.path}`,
                danger: true,
                placement: 'after-path',
                action: () => deleteExplorerFilesystemEntry(context)
            });
        }
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

    function setExplorerCreateModalBusy(busy) {
        const modal = document.getElementById('explorerCreateModal');
        const controls = modal?.querySelectorAll('input, button') || [];
        modal?.classList.toggle('is-busy', Boolean(busy));
        controls.forEach(control => {
            control.disabled = Boolean(busy);
        });
    }

    function setExplorerCreateModalError(message) {
        const error = document.getElementById('explorerCreateError');
        if (error) {
            error.textContent = String(message || '');
            error.hidden = !message;
        }
    }

    function explorerCreateNameError(name) {
        if (!name) {
            return 'Enter a name.';
        }
        if (name.includes('\0')) {
            return 'Names cannot contain NUL.';
        }
        if (name.includes('/') || name.includes('\\')) {
            return 'Enter one file or folder name, without path separators.';
        }
        if (name === '.' || name === '..') {
            return 'Dot and parent names are not allowed.';
        }
        if (/^[A-Za-z]:/.test(name)) {
            return 'Drive-qualified names are not allowed.';
        }
        if (name.length > 255) {
            return 'Names cannot exceed 255 characters.';
        }
        if (name.toLowerCase() === '.git') {
            return 'Creating .git entries is not allowed.';
        }
        return '';
    }

    function closeExplorerCreateModal(force = false) {
        const state = explorerCreateDialogState;
        if (state?.requestStarted && !force) {
            return false;
        }
        const modal = document.getElementById('explorerCreateModal');
        modal?.classList.remove('visible', 'is-busy');
        modal?.setAttribute('aria-hidden', 'true');
        setExplorerCreateModalBusy(false);
        setExplorerCreateModalError('');
        explorerCreateDialogState = null;
        return true;
    }

    function openExplorerCreateModal(context, entryKind, destination) {
        if (!isExplorerFsActionContextCurrent(context)) {
            return;
        }
        closeExplorerCreateModal(true);
        explorerCreateDialogState = {
            context,
            entryKind,
            destination,
            requestStarted: false
        };
        const modal = document.getElementById('explorerCreateModal');
        const title = document.getElementById('explorerCreateTitle');
        const copy = document.getElementById('explorerCreateDestination');
        const input = document.getElementById('explorerCreateName');
        const accept = document.getElementById('explorerCreateAccept');
        if (!modal || !title || !copy || !input || !accept) {
            explorerCreateDialogState = null;
            return;
        }
        const isDirectory = entryKind === 'directory';
        title.textContent = isDirectory ? 'New folder' : 'New file';
        copy.textContent = `Create in ${destination || 'the explorer root'}`;
        accept.textContent = 'Create';
        input.value = '';
        input.setAttribute(
            'aria-label',
            isDirectory ? 'New folder name' : 'New file name'
        );
        setExplorerCreateModalError('');
        setExplorerCreateModalBusy(false);
        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');
        window.setTimeout(() => {
            input.focus();
            input.select();
        }, 0);
    }

    async function submitExplorerCreateModal() {
        const state = explorerCreateDialogState;
        const input = document.getElementById('explorerCreateName');
        if (
            !state
            || !input
            || state.requestStarted
            || !isExplorerFsActionContextCurrent(state.context)
        ) {
            return;
        }
        const name = input.value;
        const validationError = explorerCreateNameError(name);
        if (validationError) {
            setExplorerCreateModalError(validationError);
            input.focus();
            input.select();
            return;
        }
        const context = state.context;
        const label = state.entryKind === 'directory'
            ? 'Creating folder…'
            : 'Creating file…';
        if (!setExplorerFilesystemBusy(context, label)) {
            return;
        }
        state.requestStarted = true;
        setExplorerCreateModalBusy(true);
        setExplorerCreateModalError('');
        clearExplorerFilesystemError(context.index);
        try {
            const result = await explorerFilesystemRequest(context, 'create', {
                root_revision: context.rootRevision,
                destination_directory: state.destination,
                name,
                entry_kind: state.entryKind
            });
            if (!result || !isExplorerFsActionContextCurrent(context)) {
                return;
            }
            if (!result.response?.ok) {
                const code = result.data?.code || '';
                const closeForRefresh = (
                    result.data?.mutated !== false
                    || result.response?.status === 404
                    || code === 'root_changed'
                );
                if (closeForRefresh) {
                    closeExplorerCreateModal(true);
                    showExplorerFilesystemError(context, result.data);
                } else {
                    state.requestStarted = false;
                    setExplorerCreateModalBusy(false);
                    setExplorerCreateModalError(
                        result.data?.error || 'The entry could not be created.'
                    );
                    const accept = document.getElementById('explorerCreateAccept');
                    if (accept) {
                        accept.textContent = (
                            code === 'invalid_request'
                            || code === 'protected_path'
                            || code === 'destination_exists'
                        ) ? 'Create' : 'Retry';
                    }
                    input.focus();
                    input.select();
                }
                return;
            }
            closeExplorerCreateModal(true);
            await refreshExplorerAfterFilesystemMutation(context, result.data);
            if (isExplorerFsActionContextCurrent(context)) {
                showTerminalToast(
                    `Created ${explorerFilesystemBaseName(result.data.destination_path)}`,
                    'success'
                );
            }
        } finally {
            if (explorerCreateDialogState === state) {
                state.requestStarted = false;
                setExplorerCreateModalBusy(false);
            }
            clearExplorerFilesystemBusy(context);
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

    function explorerFilesystemRetargetPath(path, sourcePath, destinationPath) {
        const value = String(path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        const source = String(sourcePath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        const destination = String(destinationPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        if (!value || !source || !explorerFilesystemPathContains(source, value)) {
            return value;
        }
        if (value === source) {
            return destination;
        }
        return `${destination}/${value.slice(source.length + 1)}`;
    }

    async function refreshExplorerAfterFilesystemMutation(context, result) {
        if (!isExplorerFsActionContextCurrent(context)) {
            return;
        }
        const pane = context.paneRef;
        const moved = Boolean(result.moved && result.source_path && result.destination_path);
        const removedPath = result.deleted_path || (moved ? result.source_path : '');
        const createdPath = result.destination_path || '';
        if (moved) {
            const previousDirectoryPath = pane._explorerPath || '';
            const sourceParent = explorerFilesystemParentPath(removedPath);
            const destinationParent = explorerFilesystemParentPath(createdPath);
            ensureExplorerTabState(pane);
            pane._explorerTabs.forEach(tab => {
                if (tab.path) {
                    tab.path = explorerFilesystemRetargetPath(
                        tab.path, removedPath, createdPath
                    );
                    tab.name = explorerFilesystemBaseName(tab.path);
                }
                if (tab.dirPath) {
                    tab.dirPath = explorerFilesystemRetargetPath(
                        tab.dirPath, removedPath, createdPath
                    );
                }
            });
            pane._explorerPath = explorerFilesystemRetargetPath(
                pane._explorerPath, removedPath, createdPath
            );
            pane._explorerFilePath = explorerFilesystemRetargetPath(
                pane._explorerFilePath, removedPath, createdPath
            );
            if (pane._explorerTreeExpanded instanceof Set) {
                pane._explorerTreeExpanded = new Set(
                    [...pane._explorerTreeExpanded].map(path => (
                        explorerFilesystemRetargetPath(path, removedPath, createdPath)
                    ))
                );
            }
            const edit = explorerEditState(pane);
            if (edit?.path) {
                edit.path = explorerFilesystemRetargetPath(
                    edit.path, removedPath, createdPath
                );
            }
            pane._explorerTreeChildren?.clear();
            pane._explorerTreeErrors?.clear();
            persistExplorerTabsToSession(context.index);
            renderExplorerTabStrip(context.index);

            const refreshDirectory = (
                pane._explorerMode === 'directory'
                && (
                    explorerFilesystemPathContains(
                        removedPath, previousDirectoryPath
                    )
                    || previousDirectoryPath === sourceParent
                    || previousDirectoryPath === destinationParent
                )
            );
            if (refreshDirectory) {
                await loadExplorerPane(
                    context.index,
                    null,
                    { force: true, showLoading: false }
                );
            } else if (
                pane._explorerMode === 'file'
                && pane._explorerFilePath
            ) {
                renderExplorerPathBreadcrumb(
                    context.index,
                    pane._explorerFilePath,
                    {
                        root: explorerRootDirectory(context.index),
                        fallbackText: pane._explorerFileName || ''
                    }
                );
            }
        } else if (removedPath) {
            const survivingParent = explorerFilesystemParentPath(removedPath);
            ensureExplorerTabState(pane);
            const activeTab = explorerActiveTab(pane);
            pane._explorerTabs = pane._explorerTabs.filter(tab => (
                tab.id === EXPLORER_PREVIEW_TAB_ID
                || !tab.path
                || !explorerFilesystemPathContains(removedPath, tab.path)
            ));
            if (activeTab?.path && explorerFilesystemPathContains(removedPath, activeTab.path)) {
                pane._explorerActiveTabId = EXPLORER_PREVIEW_TAB_ID;
            }
            const preview = explorerPreviewTab(pane);
            if (preview.dirPath && explorerFilesystemPathContains(removedPath, preview.dirPath)) {
                preview.dirPath = survivingParent;
            }
            if (pane._explorerTreeExpanded instanceof Set) {
                pane._explorerTreeExpanded = new Set(
                    [...pane._explorerTreeExpanded].filter(
                        path => !explorerFilesystemPathContains(removedPath, path)
                    )
                );
            }
            pane._explorerTreeChildren?.clear();
            pane._explorerTreeErrors?.clear();
            const clipboard = explorerFilesystemClipboards.get(context.sessionId);
            if (clipboard && explorerFilesystemPathContains(removedPath, clipboard.path)) {
                clearExplorerFilesystemClipboard(context.sessionId);
            }
            if (
                pane._explorerMode === 'directory'
                && explorerFilesystemPathContains(removedPath, pane._explorerPath || '')
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
                && explorerFilesystemPathContains(removedPath, pane._explorerFilePath)
            ) {
                await loadExplorerPane(
                    context.index,
                    survivingParent,
                    { force: true, showLoading: false }
                );
            }
            renderExplorerTabStrip(context.index);
        } else if (createdPath) {
            const destinationParent = explorerFilesystemParentPath(createdPath);
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
        if (createdPath) {
            highlightExplorerFilesystemPath(context.index, createdPath);
        }
        refreshExplorerFilesystemCutSource(context.index);
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
                    clearExplorerFilesystemClipboard(context.sessionId);
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

    async function moveExplorerFilesystemEntry(context, clipboard, destination) {
        if (!setExplorerFilesystemBusy(
            context,
            clipboard.kind === 'directory' ? 'Moving folder…' : 'Moving file…'
        )) {
            return;
        }
        clearExplorerFilesystemError(context.index);
        try {
            if (!isExplorerFsActionContextCurrent(context)) {
                return;
            }
            const result = await explorerFilesystemRequest(context, 'move', {
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
                    clearExplorerFilesystemClipboard(context.sessionId);
                }
                showExplorerFilesystemError(
                    context,
                    result.data,
                    result.data?.mutated === false
                        ? () => moveExplorerFilesystemEntry(
                            context, clipboard, destination
                        )
                        : null
                );
                return;
            }
            clearExplorerFilesystemClipboard(context.sessionId);
            await refreshExplorerAfterFilesystemMutation(context, result.data);
            if (isExplorerFsActionContextCurrent(context)) {
                showTerminalToast(`Moved ${clipboard.name}`, 'success');
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
        if (explorerCreateDialogState?.context?.sessionId === key) {
            closeExplorerCreateModal();
        }
        clearExplorerFilesystemClipboard(key);
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
            ?.querySelectorAll(
                '.explorer-context-target, .explorer-fs-highlight, .explorer-fs-cut-source'
            )
            .forEach(node => node.classList.remove(
                'explorer-context-target',
                'explorer-fs-highlight',
                'explorer-fs-cut-source'
            ));
        if (busy && !busy.requestStarted) {
            busy.card?.classList.remove('explorer-fs-busy');
            busy.status?.remove();
            pane._explorerFsBusy = null;
        }
        clearExplorerFilesystemError(index);
        return true;
    }

    document.getElementById('explorerCreateForm')?.addEventListener('submit', event => {
        event.preventDefault();
        submitExplorerCreateModal();
    });
    document.getElementById('explorerCreateCancel')?.addEventListener('click', () => {
        closeExplorerCreateModal();
    });
    document.getElementById('explorerCreateName')?.addEventListener('input', () => {
        setExplorerCreateModalError('');
        const accept = document.getElementById('explorerCreateAccept');
        if (accept) {
            accept.textContent = 'Create';
        }
    });
    document.getElementById('explorerCreateModal')?.addEventListener('click', event => {
        if (event.target.id === 'explorerCreateModal') {
            closeExplorerCreateModal();
        }
    });
    document.getElementById('explorerCreateModal')?.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeExplorerCreateModal();
        }
    });
