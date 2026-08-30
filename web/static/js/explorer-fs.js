    /* ─────────────────────────────────────────────
       Explorer create / copy / cut / paste / move / rename / delete controller.

       Clipboard, confirmations, requests, busy state, and refreshes are bound
       to an immutable session id + pane reference + root revision. Nothing is
       persisted and no operation can cross Explorer sessions.
    ───────────────────────────────────────────── */

    const explorerFilesystemClipboards = new Map();
    const explorerFilesystemActionTokens = new Map();
    const explorerFilesystemInFlightSessions = new Set();
    let explorerFilesystemTokenCounter = 0;
    let explorerFilesystemMenuSessionId = '';
    let explorerNameDialogState = null;

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
        const cutPaths = new Set(clipboard.entries.map(entry => entry.path));
        card?.querySelectorAll('[data-explorer-context-path]').forEach(node => {
            if (cutPaths.has(node.dataset.explorerContextPath)) {
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

    function clearExplorerFilesystemClipboardForPath(sessionId, path) {
        const clipboard = explorerFilesystemClipboards.get(String(sessionId || ''));
        if (!clipboard || !path) {
            return;
        }
        // One removed entry invalidates the whole clipboard rather than
        // shrinking it: the remaining entries still hold revisions captured
        // before this mutation, and a partly-stale clipboard is the thing that
        // would send one to a paste endpoint.
        const affected = clipboard.entries.some(
            entry => explorerFilesystemPathContains(path, entry.path)
        );
        if (affected) {
            clearExplorerFilesystemClipboard(sessionId);
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
        if (explorerNameDialogState && !explorerNameDialogState.requestStarted) {
            closeExplorerNameDialog();
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

    function setExplorerFilesystemClipboard(context, mode, targets) {
        // Ancestor pruning happens at capture time so the clipboard can never
        // hold a pair that would fail each other's revision check on paste.
        const entries = GridVibeExplorerSelection.topmostTargets(targets);
        if (!isExplorerFsActionContextCurrent(context) || !entries.length) {
            return;
        }
        const clipboardMode = mode === 'cut' ? 'cut' : 'copy';
        explorerFilesystemClipboards.set(context.sessionId, {
            sessionId: context.sessionId,
            rootRevision: context.rootRevision,
            entries,
            mode: clipboardMode,
            copiedAt: Date.now()
        });
        refreshExplorerFilesystemCutSource(context.index);
        showTerminalToast(
            `${clipboardMode === 'cut' ? 'Cut' : 'Copied'} ${GridVibeExplorerSelection.targetsLabel(entries)}`,
            'success'
        );
    }

    function copyExplorerFilesystemEntry(context, targets) {
        setExplorerFilesystemClipboard(context, 'copy', targets);
    }

    function cutExplorerFilesystemEntry(context, targets) {
        setExplorerFilesystemClipboard(context, 'cut', targets);
    }

    /* `targets` is what the click resolved to: the whole live selection when the
       row is part of it, otherwise just that row. Omitting it keeps the
       single-entry behaviour every existing caller relies on. */
    function explorerFilesystemMenuItems(index, rowContext, targets = null) {
        const context = explorerFilesystemActionContext(index, rowContext);
        if (!context) {
            return [];
        }
        const entryActionable = Boolean(context.path && context.revision);
        const resolvedTargets = GridVibeExplorerSelection.topmostTargets(
            targets && targets.length
                ? targets
                : [{ path: context.path, kind: context.kind, revision: context.revision }]
        );
        const targetCount = resolvedTargets.length;
        const multi = targetCount > 1;
        const targetsLabel = GridVibeExplorerSelection.targetsLabel(resolvedTargets);
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
        const clipboardLabel = clipboard
            ? GridVibeExplorerSelection.targetsLabel(clipboard.entries)
            : '';
        // Every entry already sitting in the destination means the move is a
        // no-op; a mixed clipboard still has work to do, so it stays enabled.
        const moveWithinSameFolder = Boolean(
            clipboard
            && clipboardMode === 'cut'
            && clipboard.entries.every(
                entry => explorerFilesystemParentPath(entry.path) === destination
            )
        );
        // One folder swallowing the destination is enough to refuse: the batch
        // would have to skip that entry, and "moved 2 of 3" is a worse answer
        // than not offering the action.
        const moveIntoItself = Boolean(
            clipboard
            && clipboardMode === 'cut'
            && clipboard.entries.some(entry => (
                entry.kind === 'directory'
                && explorerFilesystemPathContains(entry.path, destination)
            ))
        );
        const pasteDisabled = !clipboard || moveWithinSameFolder || moveIntoItself;
        const pasteVerb = clipboardMode === 'cut' ? 'Move' : 'Paste';
        const pasteLabel = clipboard
            ? (
                context.kind === 'file'
                    ? `${pasteVerb} ${clipboardLabel} in containing folder`
                    : `${pasteVerb} ${clipboardLabel} into "${destinationName || 'root'}"`
            )
            : 'Paste — nothing copied';
        let pasteTitle = clipboard
            ? `${clipboardMode === 'cut' ? 'Move' : 'Copy'} ${clipboardLabel} into ${destination || 'the explorer root'}`
            : 'Copy or cut a file or folder in this Explorer session first';
        if (moveWithinSameFolder) {
            pasteTitle = `${clipboardLabel} is already in this folder`;
        } else if (moveIntoItself) {
            pasteTitle = 'A folder cannot be moved into itself';
        }
        const items = [
            {
                label: 'New file…',
                action: () => openExplorerNameDialog(context, {
                    mode: 'create',
                    entryKind: 'file',
                    destination
                })
            },
            {
                label: 'New folder…',
                action: () => openExplorerNameDialog(context, {
                    mode: 'create',
                    entryKind: 'directory',
                    destination
                })
            }
        ];
        if (copyable) {
            items.push({
                label: multi ? `Copy ${targetsLabel}` : 'Copy',
                separatorBefore: true,
                action: () => copyExplorerFilesystemEntry(context, resolvedTargets)
            });
            items.push({
                label: multi ? `Cut ${targetsLabel}` : 'Cut',
                action: () => cutExplorerFilesystemEntry(context, resolvedTargets)
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
                        ? () => moveExplorerFilesystemEntries(context, clipboard, destination)
                        : () => pasteExplorerFilesystemEntries(context, clipboard, destination)
                )
                : null
        });
        if (copyable) {
            /* Rename takes one exact leaf in the entry's own parent, so it has
               no meaning for a multi-entry target. It stays visible and
               disabled rather than vanishing, so the menu does not reshuffle
               under the pointer as the selection grows. */
            items.push({
                label: 'Rename…',
                title: multi
                    ? 'Rename works on one entry at a time'
                    : `Rename ${context.path} in its own folder`,
                placement: 'after-path',
                disabled: multi,
                action: multi ? null : () => openExplorerNameDialog(context, {
                    mode: 'rename',
                    entryKind: context.kind,
                    currentName: explorerFilesystemBaseName(context.path)
                })
            });
        }
        if (entryActionable) {
            items.push({
                label: multi ? `Delete ${targetsLabel}…` : 'Delete…',
                title: multi
                    ? `Permanently delete ${targetCount} selected entries`
                    : `Permanently delete ${context.path}`,
                danger: true,
                placement: 'after-path',
                action: () => deleteExplorerFilesystemEntries(context, resolvedTargets)
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

    function setExplorerNameDialogBusy(busy) {
        const modal = document.getElementById('explorerNameModal');
        const controls = modal?.querySelectorAll('input, button') || [];
        modal?.classList.toggle('is-busy', Boolean(busy));
        controls.forEach(control => {
            control.disabled = Boolean(busy);
        });
    }

    function setExplorerNameDialogError(message) {
        const error = document.getElementById('explorerNameError');
        if (error) {
            error.textContent = String(message || '');
            error.hidden = !message;
        }
    }

    function explorerEntryNameError(name) {
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
            return 'Naming an entry .git is not allowed.';
        }
        return '';
    }

    function closeExplorerNameDialog(force = false) {
        const state = explorerNameDialogState;
        if (state?.requestStarted && !force) {
            return false;
        }
        const modal = document.getElementById('explorerNameModal');
        modal?.classList.remove('visible', 'is-busy');
        modal?.setAttribute('aria-hidden', 'true');
        setExplorerNameDialogBusy(false);
        setExplorerNameDialogError('');
        explorerNameDialogState = null;
        return true;
    }

    function explorerNameDialogCopy(options) {
        const isDirectory = options.entryKind === 'directory';
        if (options.mode === 'rename') {
            return {
                title: isDirectory ? 'Rename folder' : 'Rename file',
                context: `Rename ${options.sourcePath} in its own folder`,
                acceptLabel: 'Rename',
                inputLabel: isDirectory ? 'New folder name' : 'New file name'
            };
        }
        return {
            title: isDirectory ? 'New folder' : 'New file',
            context: `Create in ${options.destination || 'the explorer root'}`,
            acceptLabel: 'Create',
            inputLabel: isDirectory ? 'New folder name' : 'New file name'
        };
    }

    function openExplorerNameDialog(context, options) {
        if (!isExplorerFsActionContextCurrent(context)) {
            return;
        }
        closeExplorerNameDialog(true);
        const isRename = options.mode === 'rename';
        if (isRename && (!context.path || !context.revision)) {
            return;
        }
        const state = {
            context,
            mode: isRename ? 'rename' : 'create',
            entryKind: options.entryKind === 'directory' ? 'directory' : 'file',
            destination: String(options.destination || ''),
            sourcePath: context.path,
            sourceRevision: context.revision,
            currentName: String(options.currentName || ''),
            requestStarted: false
        };
        const copy = explorerNameDialogCopy(state);
        state.acceptLabel = copy.acceptLabel;
        const modal = document.getElementById('explorerNameModal');
        const title = document.getElementById('explorerNameTitle');
        const contextCopy = document.getElementById('explorerNameContext');
        const input = document.getElementById('explorerNameInput');
        const accept = document.getElementById('explorerNameAccept');
        if (!modal || !title || !contextCopy || !input || !accept) {
            return;
        }
        explorerNameDialogState = state;
        title.textContent = copy.title;
        contextCopy.textContent = copy.context;
        accept.textContent = copy.acceptLabel;
        input.value = isRename ? state.currentName : '';
        input.setAttribute('aria-label', copy.inputLabel);
        setExplorerNameDialogError('');
        setExplorerNameDialogBusy(false);
        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');
        window.setTimeout(() => {
            input.focus();
            input.select();
        }, 0);
    }

    function explorerNameDialogValidationError(state, name) {
        const nameError = explorerEntryNameError(name);
        if (nameError) {
            return nameError;
        }
        if (state.mode === 'rename' && name === state.currentName) {
            return 'Enter a different name.';
        }
        return '';
    }

    function explorerNameDialogRequest(state, name) {
        if (state.mode === 'rename') {
            return explorerFilesystemRequest(state.context, 'rename', {
                root_revision: state.context.rootRevision,
                source_path: state.sourcePath,
                source_revision: state.sourceRevision,
                name
            });
        }
        return explorerFilesystemRequest(state.context, 'create', {
            root_revision: state.context.rootRevision,
            destination_directory: state.destination,
            name,
            entry_kind: state.entryKind
        });
    }

    async function submitExplorerNameDialog() {
        const state = explorerNameDialogState;
        const input = document.getElementById('explorerNameInput');
        if (
            !state
            || !input
            || state.requestStarted
            || !isExplorerFsActionContextCurrent(state.context)
        ) {
            return;
        }
        const name = input.value;
        const validationError = explorerNameDialogValidationError(state, name);
        if (validationError) {
            setExplorerNameDialogError(validationError);
            input.focus();
            input.select();
            return;
        }
        const context = state.context;
        const isRename = state.mode === 'rename';
        const isDirectory = state.entryKind === 'directory';
        const label = isRename
            ? (isDirectory ? 'Renaming folder…' : 'Renaming file…')
            : (isDirectory ? 'Creating folder…' : 'Creating file…');
        if (!setExplorerFilesystemBusy(context, label)) {
            return;
        }
        state.requestStarted = true;
        setExplorerNameDialogBusy(true);
        setExplorerNameDialogError('');
        clearExplorerFilesystemError(context.index);
        try {
            const result = await explorerNameDialogRequest(state, name);
            if (!result || !isExplorerFsActionContextCurrent(context)) {
                return;
            }
            if (!result.response?.ok) {
                const code = result.data?.code || '';
                const closeForRefresh = (
                    result.data?.mutated !== false
                    || result.response?.status === 404
                    || code === 'root_changed'
                    || code === 'entry_changed'
                );
                if (closeForRefresh) {
                    closeExplorerNameDialog(true);
                    if (isRename) {
                        clearExplorerFilesystemClipboardForPath(
                            context.sessionId, state.sourcePath
                        );
                    }
                    showExplorerFilesystemError(context, result.data);
                } else {
                    state.requestStarted = false;
                    setExplorerNameDialogBusy(false);
                    setExplorerNameDialogError(
                        result.data?.error
                        || (isRename
                            ? 'The entry could not be renamed.'
                            : 'The entry could not be created.')
                    );
                    const accept = document.getElementById('explorerNameAccept');
                    if (accept) {
                        accept.textContent = (
                            code === 'invalid_request'
                            || code === 'invalid_destination'
                            || code === 'protected_path'
                            || code === 'destination_exists'
                            || code === 'unsupported_entry_type'
                        ) ? state.acceptLabel : 'Retry';
                    }
                    input.focus();
                    input.select();
                }
                return;
            }
            closeExplorerNameDialog(true);
            if (isRename) {
                clearExplorerFilesystemClipboardForPath(
                    context.sessionId, result.data.source_path
                );
            }
            await refreshExplorerAfterFilesystemMutation(context, result.data);
            if (isExplorerFsActionContextCurrent(context)) {
                const resultName = explorerFilesystemBaseName(
                    result.data.destination_path
                );
                showTerminalToast(
                    isRename
                        ? `Renamed ${state.currentName} to ${resultName}`
                        : `Created ${resultName}`,
                    'success'
                );
            }
        } finally {
            if (explorerNameDialogState === state) {
                state.requestStarted = false;
                setExplorerNameDialogBusy(false);
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

    /* A batch is N atomic requests, so its reloads have to be recorded and run
       once at the end rather than after each one.

       That is a correctness requirement, not an optimisation: `loadExplorerPane`
       re-reads the root revision through `updateExplorerFilesystemRootRevision`,
       which drops the session's action token — reloading between two requests
       would make `isExplorerFsActionContextCurrent` false and silently abandon
       the rest of the batch. It also keeps a 20-entry delete to one directory
       read, one tree read, and one Git invalidation instead of 20 of each.

       Callers that pass no plan get today's behaviour unchanged: the plan is
       created and applied inside the same call. */
    function explorerFilesystemMutationPlan() {
        return {
            directoryReload: false,
            // null means "reload wherever the pane already is".
            directoryTarget: null,
            breadcrumb: false,
            tabStrip: false,
            created: []
        };
    }

    async function applyExplorerFilesystemMutationPlan(context, plan) {
        if (!isExplorerFsActionContextCurrent(context)) {
            return;
        }
        const pane = context.paneRef;
        if (plan.tabStrip) {
            renderExplorerTabStrip(context.index);
        }
        if (plan.directoryReload) {
            await loadExplorerPane(
                context.index,
                plan.directoryTarget,
                { force: true, showLoading: false }
            );
        } else if (plan.breadcrumb && pane._explorerFilePath) {
            renderExplorerPathBreadcrumb(
                context.index,
                pane._explorerFilePath,
                {
                    root: explorerRootDirectory(context.index),
                    fallbackText: pane._explorerFileName || ''
                }
            );
        }
        if (pane._explorerTreeSidebarOpen) {
            await reloadExplorerTree(context.index);
        }
        await invalidateExplorerFilesystemGit(context.index);
        plan.created.forEach(path => {
            highlightExplorerFilesystemPath(context.index, path);
        });
        refreshExplorerFilesystemCutSource(context.index);
        refreshExplorerSelectionHighlight(context.index);
    }

    async function refreshExplorerAfterFilesystemMutation(context, result, plan = null) {
        if (!isExplorerFsActionContextCurrent(context)) {
            return;
        }
        const deferred = plan || explorerFilesystemMutationPlan();
        reconcileExplorerAfterFilesystemMutation(context, result, deferred);
        if (!plan) {
            await applyExplorerFilesystemMutationPlan(context, deferred);
        }
    }

    function reconcileExplorerAfterFilesystemMutation(context, result, plan) {
        const pane = context.paneRef;
        const moved = Boolean(result.moved && result.source_path && result.destination_path);
        const removedPath = result.deleted_path || (moved ? result.source_path : '');
        const createdPath = result.destination_path || '';
        if (moved) {
            const previousDirectoryPath = pane._explorerPath || '';
            const previousFilePath = pane._explorerFilePath || '';
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
            if (pane._explorerFilePath && pane._explorerFilePath !== previousFilePath) {
                // A rename changes the leaf, so the open file's display name moves with it.
                pane._explorerFileName = explorerFilesystemBaseName(
                    pane._explorerFilePath
                );
            }
            if (pane._explorerTreeExpanded instanceof Set) {
                pane._explorerTreeExpanded = new Set(
                    [...pane._explorerTreeExpanded].map(path => (
                        explorerFilesystemRetargetPath(path, removedPath, createdPath)
                    ))
                );
            }
            /* The Git pin follows the path it names, exactly as the tabs, the
               browsed folder and the open file above it do. A rename relocates
               the thing the pin points at rather than removing it, so a pin
               left on the old spelling reports "no longer exists" about a path
               the user has just watched move — and the pane is holding the new
               one everywhere else.

               `typeof` is the pin's existence, so this must never turn a
               missing pin into `''` (a root pin) — hence the guard rather than
               a bare assignment. A root pin is outside every source path and
               `explorerFilesystemRetargetPath` hands it straight back.

               Deliberately only on the move branch. A *deleted* pin is not
               relocatable, and dropping it there would silently widen the
               scope back to the explorer root; the sidebar reports it by name
               with Clear pin beside it instead, which is the documented
               behaviour for a pin whose path has gone. */
            if (typeof pane._explorerGitPinnedPath === 'string') {
                pane._explorerGitPinnedPath = explorerFilesystemRetargetPath(
                    pane._explorerGitPinnedPath, removedPath, createdPath
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
            plan.tabStrip = true;

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
                plan.directoryReload = true;
            } else if (
                pane._explorerMode === 'file'
                && pane._explorerFilePath
            ) {
                plan.breadcrumb = true;
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
            clearExplorerFilesystemClipboardForPath(context.sessionId, removedPath);
            /* Across a batch the pane has not navigated yet, so "where the
               listing will end up" is the plan's target once one is set — a
               second removal that swallows that target has to retarget it
               again, or the deferred load would open a directory this batch
               just deleted. */
            const pendingDirectory = plan.directoryTarget === null
                ? (pane._explorerPath || '')
                : plan.directoryTarget;
            if (
                pane._explorerMode === 'directory'
                && explorerFilesystemPathContains(removedPath, pendingDirectory)
            ) {
                plan.directoryReload = true;
                plan.directoryTarget = survivingParent;
            } else if (pane._explorerMode === 'directory') {
                plan.directoryReload = true;
            } else if (
                pane._explorerFilePath
                && explorerFilesystemPathContains(removedPath, pane._explorerFilePath)
            ) {
                plan.directoryReload = true;
                plan.directoryTarget = survivingParent;
            }
            plan.tabStrip = true;
        } else if (createdPath) {
            const destinationParent = explorerFilesystemParentPath(createdPath);
            if (
                pane._explorerMode === 'directory'
                && (pane._explorerPath || '') === destinationParent
            ) {
                plan.directoryReload = true;
            }
        }
        if (removedPath) {
            // An entry that no longer exists must leave the selection, or the
            // next menu action would send its dead path and revision.
            dropExplorerSelectionPaths(context.index, [removedPath]);
        }
        if (createdPath) {
            plan.created.push(createdPath);
        }
    }

    /* Run one atomic request per entry under a single busy hold, recording each
       success into a shared plan so the surfaces refresh once at the end.

       Sequential rather than parallel: the mutation endpoints coordinate with
       editor saves through ancestor-aware per-path claims, so overlapping
       requests inside one root would contend for them, and a stable order makes
       "3 of 5" reportable. The loop stops early if the pane, session, or root
       revision changed under it — the same guard every single-entry action
       already used, just checked per request. */
    async function runExplorerFilesystemBatch(context, entries, buildRequest, plan) {
        const results = [];
        for (const entry of entries) {
            if (!isExplorerFsActionContextCurrent(context)) {
                break;
            }
            const { route, body } = buildRequest(entry);
            const result = await explorerFilesystemRequest(context, route, body);
            if (!result) {
                break;
            }
            if (result.response?.ok) {
                results.push({ ok: true, entry, data: result.data });
                if (isExplorerFsActionContextCurrent(context)) {
                    reconcileExplorerAfterFilesystemMutation(context, result.data, plan);
                }
            } else {
                results.push({
                    ok: false,
                    entry,
                    error: result.data?.error,
                    mutated: result.data?.mutated,
                    status: result.response?.status || 0,
                    data: result.data
                });
            }
        }
        return results;
    }

    /* Report an N-request batch through the existing error bar — one message,
       one surface — synthesising the `mutated` flag the bar uses to decide
       between Retry and Refresh. A retry replays only the entries that failed. */
    function reportExplorerFilesystemBatch(context, verb, results, retryEntries) {
        const outcome = GridVibeExplorerSelection.batchOutcome(verb, results);
        if (!isExplorerFsActionContextCurrent(context)) {
            return outcome;
        }
        if (outcome.ok) {
            showTerminalToast(outcome.toast, 'success');
            return outcome;
        }
        showExplorerFilesystemError(
            context,
            { error: outcome.message, mutated: !outcome.retryable },
            outcome.retryable && typeof retryEntries === 'function'
                ? retryEntries(results.filter(row => !row.ok).map(row => row.entry))
                : null
        );
        return outcome;
    }

    async function pasteExplorerFilesystemEntries(context, clipboard, destination, only = null) {
        const entries = only || clipboard.entries;
        if (!entries.length) {
            return;
        }
        const label = entries.length > 1
            ? `Copying ${entries.length}…`
            : (entries[0].kind === 'directory' ? 'Copying folder…' : 'Copying file…');
        if (!setExplorerFilesystemBusy(context, label)) {
            return;
        }
        clearExplorerFilesystemError(context.index);
        const plan = explorerFilesystemMutationPlan();
        try {
            if (!isExplorerFsActionContextCurrent(context)) {
                return;
            }
            const results = await runExplorerFilesystemBatch(
                context,
                entries,
                entry => ({
                    route: 'paste',
                    body: {
                        root_revision: context.rootRevision,
                        source_path: entry.path,
                        source_revision: entry.revision,
                        destination_directory: destination
                    }
                }),
                plan
            );
            // A source that vanished or changed invalidates the clipboard's
            // captured revisions, exactly as it did for a single paste.
            if (results.some(row => !row.ok && (row.status === 404 || row.status === 409))) {
                clearExplorerFilesystemClipboard(context.sessionId);
            }
            await applyExplorerFilesystemMutationPlan(context, plan);
            reportExplorerFilesystemBatch(
                context,
                'Copied',
                results,
                failed => () => pasteExplorerFilesystemEntries(
                    context, clipboard, destination, failed
                )
            );
        } finally {
            clearExplorerFilesystemBusy(context);
        }
    }

    async function moveExplorerFilesystemEntries(context, clipboard, destination, only = null) {
        const entries = only || clipboard.entries;
        if (!entries.length) {
            return;
        }
        const label = entries.length > 1
            ? `Moving ${entries.length}…`
            : (entries[0].kind === 'directory' ? 'Moving folder…' : 'Moving file…');
        if (!setExplorerFilesystemBusy(context, label)) {
            return;
        }
        clearExplorerFilesystemError(context.index);
        const plan = explorerFilesystemMutationPlan();
        try {
            if (!isExplorerFsActionContextCurrent(context)) {
                return;
            }
            const results = await runExplorerFilesystemBatch(
                context,
                entries,
                entry => ({
                    route: 'move',
                    body: {
                        root_revision: context.rootRevision,
                        source_path: entry.path,
                        source_revision: entry.revision,
                        destination_directory: destination
                    }
                }),
                plan
            );
            const retryable = results.filter(row => !row.ok && row.mutated === false);
            /* A cut is consumed by its move. The clipboard is kept only when
               every failure left the filesystem untouched *and* nothing moved,
               so the user can retry the same cut; any other outcome has
               partially spent it and it must not be pasted again. */
            const spent = results.some(row => row.ok)
                || retryable.length !== results.filter(row => !row.ok).length;
            if (spent) {
                clearExplorerFilesystemClipboard(context.sessionId);
            }
            await applyExplorerFilesystemMutationPlan(context, plan);
            reportExplorerFilesystemBatch(
                context,
                'Moved',
                results,
                failed => () => moveExplorerFilesystemEntries(
                    context, clipboard, destination, failed
                )
            );
        } finally {
            clearExplorerFilesystemBusy(context);
        }
    }

    async function deleteExplorerFilesystemEntries(context, targets) {
        const entries = GridVibeExplorerSelection.topmostTargets(targets);
        const confirmCopy = GridVibeExplorerSelection.deleteConfirmCopy(entries);
        if (!entries.length || !confirmCopy) {
            return;
        }
        const label = entries.length > 1
            ? `Deleting ${entries.length}…`
            : (entries[0].kind === 'directory' ? 'Deleting folder…' : 'Deleting…');
        if (!setExplorerFilesystemBusy(context, label)) {
            return;
        }
        clearExplorerFilesystemError(context.index);
        const plan = explorerFilesystemMutationPlan();
        try {
            const edit = explorerEditState(context.paneRef);
            const hitsOpenEdit = Boolean(edit?.dirty) && entries.some(
                entry => explorerFilesystemPathContains(entry.path, edit.path || '')
            );
            if (
                hitsOpenEdit
                && !(await confirmDiscardExplorerEdit(
                    context.index,
                    entries.length > 1 ? 'Deleting these entries' : 'Deleting this folder'
                ))
            ) {
                return;
            }
            if (!isExplorerFsActionContextCurrent(context)) {
                return;
            }
            const owner = `explorer-fs:${context.sessionId}:${context.token}`;
            context.paneRef._explorerFsBusy.owner = owner;
            // One confirmation for the whole batch, never one per entry.
            const confirmed = await openGenericConfirmModal({
                title: confirmCopy.title,
                copy: GridVibeExplorerSelection.targetsCopyLine(entries),
                note: 'GridVibe cannot undo this action.',
                confirmLabel: confirmCopy.confirmLabel,
                danger: true,
                owner
            });
            if (!confirmed || !isExplorerFsActionContextCurrent(context)) {
                return;
            }
            const results = await runExplorerFilesystemBatch(
                context,
                entries,
                entry => ({
                    route: 'delete',
                    body: {
                        root_revision: context.rootRevision,
                        path: entry.path,
                        base_revision: entry.revision,
                        recursive: entry.kind === 'directory'
                    }
                }),
                plan
            );
            await applyExplorerFilesystemMutationPlan(context, plan);
            reportExplorerFilesystemBatch(
                context,
                'Deleted',
                results,
                failed => () => deleteExplorerFilesystemEntries(context, failed)
            );
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
        if (explorerNameDialogState?.context?.sessionId === key) {
            closeExplorerNameDialog();
        }
        clearExplorerFilesystemClipboard(key);
        clearExplorerSelection(key);
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

    document.getElementById('explorerNameForm')?.addEventListener('submit', event => {
        event.preventDefault();
        submitExplorerNameDialog();
    });
    document.getElementById('explorerNameCancel')?.addEventListener('click', () => {
        closeExplorerNameDialog();
    });
    document.getElementById('explorerNameInput')?.addEventListener('input', () => {
        setExplorerNameDialogError('');
        const accept = document.getElementById('explorerNameAccept');
        if (accept) {
            accept.textContent = explorerNameDialogState?.acceptLabel || 'Create';
        }
    });
    document.getElementById('explorerNameModal')?.addEventListener('click', event => {
        if (event.target.id === 'explorerNameModal') {
            closeExplorerNameDialog();
        }
    });
    document.getElementById('explorerNameModal')?.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeExplorerNameDialog();
        }
    });
