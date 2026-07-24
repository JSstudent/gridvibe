    /* ─────────────────────────────────────────────
       Explorer in-app text editor — docs/text_editor_2026-07-20.md.
       Owns edit state, the Source-view textarea, save/conflict flows, and the
       unsaved-work discard guards. Loaded after explorer-viewer.js and before
       terminals.js so it can reuse both files' render/navigation hooks and the
       shared genericConfirmModal / toast helpers at interaction time.

       Editing is deliberately kept off the tab record and out of saved sessions
       / runtime_state.json (pane._explorerEdit is transient in-memory only).
    ───────────────────────────────────────────── */

    function explorerEditState(pane) {
        return pane && pane._explorerEdit ? pane._explorerEdit : null;
    }

    function hasDirtyExplorerEdit(index) {
        const state = explorerEditState(terminals[index]);
        return Boolean(state && state.dirty);
    }

    function hasAnyDirtyExplorerEdit() {
        return terminals.some((_, index) => hasDirtyExplorerEdit(index));
    }

    /* Drop a pane's edit without a prompt. Callers that reach here have already
       confirmed (or the buffer was clean). Never touches the DOM — the caller
       re-renders. */
    function clearExplorerEditState(index) {
        const pane = terminals[index];
        if (pane) {
            pane._explorerEdit = null;
        }
    }

    /* Returns immediately when clean; otherwise asks once and clears the edit
       only after confirmation. Used before every deliberate teardown that would
       replace or discard the editor. No window.confirm/alert/prompt (WebView2). */
    async function confirmDiscardExplorerEdit(index, actionLabel = '') {
        if (!hasDirtyExplorerEdit(index)) {
            return true;
        }
        const pane = terminals[index];
        const name = (pane && pane._explorerFileName) || 'this file';
        const confirmed = await openGenericConfirmModal({
            title: 'Discard unsaved changes?',
            copy: `You have unsaved changes to ${name}.`,
            note: actionLabel ? `${actionLabel} will discard them.` : '',
            confirmLabel: 'Discard changes',
            danger: true
        });
        if (confirmed) {
            clearExplorerEditState(index);
        }
        return confirmed;
    }

    /* Group-level guard: one prompt covers every rendered pane with a dirty
       edit, clearing them all on confirmation. */
    async function confirmDiscardAllExplorerEdits(actionLabel = '') {
        if (!hasAnyDirtyExplorerEdit()) {
            return true;
        }
        const dirtyIndexes = terminals
            .map((_, index) => index)
            .filter(index => hasDirtyExplorerEdit(index));
        const confirmed = await openGenericConfirmModal({
            title: 'Discard unsaved changes?',
            copy: dirtyIndexes.length > 1
                ? `${dirtyIndexes.length} open files have unsaved changes.`
                : 'An open file has unsaved changes.',
            note: actionLabel ? `${actionLabel} will discard them.` : '',
            confirmLabel: 'Discard changes',
            danger: true
        });
        if (confirmed) {
            dirtyIndexes.forEach(clearExplorerEditState);
        }
        return confirmed;
    }

    function explorerEditDisabledTooltip(reason) {
        if (reason === 'truncated') {
            return 'File exceeds the 10 MiB in-place edit limit';
        }
        if (reason === 'mixed_line_endings') {
            return 'Mixed line endings are view-only in this version';
        }
        return 'This file cannot be edited in place';
    }

    /* Static host for the Edit / Save+Cancel button group, injected into the
       file header by renderExplorerFile just before Download. Filled by
       refreshExplorerEditControls from the pane's current edit state. */
    function explorerEditorControlsHtml(index) {
        return `<div class="explorer-editor-actions" data-explorer-editor-actions="${index}"></div>`;
    }

    function refreshExplorerEditControls(index) {
        const pane = terminals[index];
        const host = document.querySelector(`[data-explorer-editor-actions="${index}"]`);
        if (!pane || !host) {
            return;
        }
        const state = explorerEditState(pane);
        if (state) {
            const canSave = state.dirty && !state.saving;
            host.innerHTML = `
                <button type="button" class="explorer-editor-action-btn explorer-edit-save-btn${state.saving ? ' is-busy' : ''}" data-explorer-edit-save="${index}" ${canSave ? '' : 'disabled'} title="Save (Ctrl+S)" aria-label="Save file">${EXPLORER_SAVE_ICON}<span class="explorer-editor-action-label">Save</span></button>
                <button type="button" class="explorer-editor-action-btn explorer-edit-cancel-btn" data-explorer-edit-cancel="${index}" ${state.saving ? 'disabled' : ''} title="Cancel (Esc)" aria-label="Cancel editing">${EXPLORER_CANCEL_ICON}<span class="explorer-editor-action-label">Cancel</span></button>
            `;
        } else {
            const editable = Boolean(pane._explorerFileEditable);
            const title = editable ? 'Edit file' : explorerEditDisabledTooltip(pane._explorerFileEditBlockReason || '');
            host.innerHTML = `<button type="button" class="explorer-editor-action-btn explorer-edit-btn" data-explorer-edit="${index}" ${editable ? '' : 'disabled'} title="${escHtml(title)}" aria-label="${escHtml(title)}">${EXPLORER_EDIT_ICON}<span class="explorer-editor-action-label">Edit</span></button>`;
        }
        wireExplorerEditControls(index);
    }

    function wireExplorerEditControls(index) {
        const host = document.querySelector(`[data-explorer-editor-actions="${index}"]`);
        if (!host) {
            return;
        }
        host.querySelector(`[data-explorer-edit="${index}"]`)
            ?.addEventListener('click', () => enterExplorerEditMode(index));
        host.querySelector(`[data-explorer-edit-save="${index}"]`)
            ?.addEventListener('click', () => saveExplorerEdit(index));
        host.querySelector(`[data-explorer-edit-cancel="${index}"]`)
            ?.addEventListener('click', () => cancelExplorerEdit(index));
    }

    /* While editing, the non-editor file chrome is disabled so a stray click
       cannot swap views, search, download the old disk copy, or restyle the
       preview. Zoom stays live (it only sets the shared font-size variable). */
    function setExplorerEditChromeDisabled(index, disabled) {
        const list = document.getElementById(`explorer-list-${index}`);
        if (!list) {
            return;
        }
        const editor = list.querySelector('.explorer-editor');
        if (editor) {
            editor.classList.toggle('is-editing', Boolean(disabled));
        }
        const selectors = [
            '[data-explorer-file-view]',
            `[data-explorer-download="${index}"]`,
            `[data-explorer-md-appearance="${index}"]`,
            `[data-explorer-search-input="${index}"]`,
            `[data-explorer-search-prev="${index}"]`,
            `[data-explorer-search-next="${index}"]`,
            `[data-explorer-search-clear="${index}"]`
        ];
        selectors.forEach(selector => {
            list.querySelectorAll(selector).forEach(element => {
                element.disabled = Boolean(disabled);
            });
        });
        if (!disabled) {
            // Let the search machinery re-derive prev/next button states.
            applyExplorerSearch(index);
        }
    }

    function explorerNormalizeEditNewlines(content) {
        return String(content == null ? '' : content).replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    }

    function enterExplorerEditMode(index) {
        const pane = terminals[index];
        if (!pane || !pane._explorerFileEditable || explorerEditState(pane) || pane._explorerMode !== 'file') {
            return;
        }
        // Source only, diff split closed (2 in §5.2).
        setExplorerFileView(index, 'source');
        clearExplorerEditBar(index);

        const normalized = explorerNormalizeEditNewlines(pane._explorerFileContent || '');
        pane._explorerEdit = {
            tabId: pane._explorerActiveTabId,
            path: pane._explorerFilePath,
            originalContent: normalized,
            draft: normalized,
            baseRevision: pane._explorerFileRevision || '',
            conflictRevision: '',
            dirty: false,
            saving: false
        };

        renderExplorerEditTextarea(index);
        setExplorerEditChromeDisabled(index, true);
        refreshExplorerEditControls(index);
        applyExplorerEditorFontSize(index);

        const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
        if (textarea) {
            textarea.focus();
            textarea.setSelectionRange(0, 0);
        }
    }

    function renderExplorerEditTextarea(index) {
        const pane = terminals[index];
        const code = document.getElementById(`explorer-code-${index}`);
        const state = explorerEditState(pane);
        if (!pane || !code || !state) {
            return;
        }
        code.innerHTML = `<textarea id="explorer-edit-textarea-${index}" class="explorer-source-editor" spellcheck="false" wrap="off" aria-label="Edit ${escHtml(pane._explorerFileName || 'file')}"></textarea>`;
        const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
        if (!textarea) {
            return;
        }
        textarea.value = state.draft;
        textarea.addEventListener('input', () => handleExplorerEditInput(index));
        textarea.addEventListener('keydown', event => handleExplorerEditKeydown(index, event));
    }

    function handleExplorerEditInput(index) {
        const pane = terminals[index];
        const state = explorerEditState(pane);
        const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
        if (!state || !textarea) {
            return;
        }
        state.draft = textarea.value;
        const dirty = state.draft !== state.originalContent;
        if (dirty !== state.dirty) {
            state.dirty = dirty;
            const saveBtn = document.querySelector(`[data-explorer-edit-save="${index}"]`);
            if (saveBtn) {
                saveBtn.disabled = !(dirty && !state.saving);
            }
            updateExplorerEditTabDirty(index);
        }
    }

    function updateExplorerEditTabDirty(index) {
        const pane = terminals[index];
        const strip = document.getElementById(`explorer-tabs-${index}`);
        if (!pane || !strip) {
            return;
        }
        const activeId = pane._explorerActiveTabId || '';
        const tabEl = strip.querySelector(`[data-explorer-tab="${window.CSS && CSS.escape ? CSS.escape(activeId) : activeId}"]`);
        if (tabEl) {
            tabEl.classList.toggle('is-dirty', hasDirtyExplorerEdit(index));
        }
    }

    function handleExplorerEditKeydown(index, event) {
        const textarea = event.target;
        if (event.key === 'Tab' && !event.ctrlKey && !event.metaKey && !event.altKey) {
            event.preventDefault();
            const start = textarea.selectionStart;
            const end = textarea.selectionEnd;
            textarea.setRangeText('\t', start, end, 'end');
            handleExplorerEditInput(index);
            return;
        }
        if ((event.ctrlKey || event.metaKey) && (event.key === 's' || event.key === 'S')) {
            event.preventDefault();
            saveExplorerEdit(index);
            return;
        }
        if (event.key === 'Escape') {
            event.preventDefault();
            cancelExplorerEdit(index);
        }
    }

    /* Leave edit mode for the same file (Cancel or after a discarded conflict).
       Rebuilds the read-only highlighted Source view from the unchanged buffer
       and restores the file chrome + Edit button. */
    function exitExplorerEditMode(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerEdit = null;
        clearExplorerEditBar(index);
        renderExplorerSource(index);
        setExplorerEditChromeDisabled(index, false);
        refreshExplorerEditControls(index);
        applyExplorerSearch(index);
        const editButton = document.querySelector(`[data-explorer-edit="${index}"]`);
        if (editButton) {
            editButton.focus();
        }
    }

    async function cancelExplorerEdit(index) {
        const pane = terminals[index];
        if (!explorerEditState(pane)) {
            return;
        }
        if (hasDirtyExplorerEdit(index)) {
            const confirmed = await openGenericConfirmModal({
                title: 'Discard unsaved changes?',
                copy: `You have unsaved changes to ${(pane && pane._explorerFileName) || 'this file'}.`,
                note: 'Cancelling will discard them.',
                confirmLabel: 'Discard changes',
                danger: true
            });
            if (!confirmed) {
                const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
                textarea?.focus();
                return;
            }
        }
        exitExplorerEditMode(index);
    }

    // ── Save ────────────────────────────────────────────────────────────────
    async function saveExplorerEdit(index) {
        const pane = terminals[index];
        const state = explorerEditState(pane);
        const sessionId = sessionIds[index];
        if (!state || !sessionId || state.saving || !state.dirty) {
            return;
        }
        state.saving = true;
        refreshExplorerEditControls(index);
        clearExplorerEditBar(index);

        const baseRevision = state.conflictRevision || state.baseRevision;
        try {
            const response = await fetch(`/api/explorer/${encodeURIComponent(sessionId)}/file`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: state.path,
                    content: state.draft,
                    base_revision: baseRevision
                })
            });
            const data = await response.json().catch(() => ({}));
            if (response.ok) {
                onExplorerSaveSuccess(index, data);
                return;
            }
            onExplorerSaveError(index, data);
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer save failed:', error);
            onExplorerSaveError(index, { error: 'Save request failed. Check your connection and try Save again.' });
        }
    }

    function onExplorerSaveSuccess(index, data) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerEdit = null;
        const scrollState = captureExplorerFileScroll(index);
        // Prefer the in-place refresh; fall back to a full render when the
        // available panels changed (a clean file commonly gains a Diff panel
        // after its first edit).
        const applied = updateExplorerFileInPlace(index, data, scrollState);
        if (!applied) {
            renderExplorerFile(index, data, { tab: pane._explorerActiveTabId });
        } else {
            setExplorerEditChromeDisabled(index, false);
            refreshExplorerEditControls(index);
        }
        // Diff cache is invalidated by both refresh paths (they reset
        // _explorerDiffLoaded / _explorerDiffCacheKey).
        if (pane._explorerGitSidebarOpen) {
            invalidateExplorerGitRepo(index);
            loadExplorerGitRepo(index);
        }
        if (pane._explorerTreeSidebarOpen) {
            reloadExplorerTree(index);
        }
        showTerminalToast(`Saved ${data.name || pane._explorerFileName || 'file'}`, 'success');
    }

    function onExplorerSaveError(index, data) {
        const pane = terminals[index];
        const state = explorerEditState(pane);
        if (!state) {
            return;
        }
        state.saving = false;
        const code = data && data.code;
        if (code === 'file_conflict') {
            state.conflictRevision = (data && data.current_revision) || '';
            refreshExplorerEditControls(index);
            renderExplorerConflictBar(index);
            return;
        }
        refreshExplorerEditControls(index);
        let message = (data && data.error) || 'Save failed. Try Save again.';
        if (code === 'save_in_progress') {
            message = 'Another save is already in progress. Try Save again.';
        } else if (code === 'file_too_large') {
            message = 'This file no longer fits the 10 MiB in-place edit limit.';
        }
        showExplorerEditError(index, message);
    }

    // ── Inline conflict / error bar ──────────────────────────────────────────
    function explorerEditBarHost(index) {
        const list = document.getElementById(`explorer-list-${index}`);
        const editor = list ? list.querySelector('.explorer-editor') : null;
        if (!editor) {
            return null;
        }
        let bar = document.getElementById(`explorer-edit-bar-${index}`);
        if (!bar) {
            bar = document.createElement('div');
            bar.id = `explorer-edit-bar-${index}`;
            const body = editor.querySelector('.explorer-editor-body');
            editor.insertBefore(bar, body);
        }
        return bar;
    }

    function clearExplorerEditBar(index) {
        document.getElementById(`explorer-edit-bar-${index}`)?.remove();
    }

    function showExplorerEditError(index, message) {
        const bar = explorerEditBarHost(index);
        if (!bar) {
            return;
        }
        bar.className = 'explorer-edit-bar explorer-edit-error';
        bar.innerHTML = `
            <span class="explorer-edit-bar-message" role="alert">${escHtml(message)}</span>
            <button type="button" class="explorer-edit-bar-dismiss" data-explorer-edit-dismiss="${index}" title="Dismiss" aria-label="Dismiss">×</button>
        `;
        bar.querySelector(`[data-explorer-edit-dismiss="${index}"]`)
            ?.addEventListener('click', () => {
                clearExplorerEditBar(index);
                document.getElementById(`explorer-edit-textarea-${index}`)?.focus();
            });
    }

    function renderExplorerConflictBar(index) {
        const bar = explorerEditBarHost(index);
        if (!bar) {
            return;
        }
        bar.className = 'explorer-edit-bar explorer-edit-conflict';
        bar.innerHTML = `
            <span class="explorer-edit-bar-message" role="alert">This file changed on disk since you opened it.</span>
            <span class="explorer-edit-bar-actions">
                <button type="button" class="explorer-editor-action-btn" data-explorer-edit-reload="${index}">Reload from disk</button>
                <button type="button" class="explorer-editor-action-btn" data-explorer-edit-overwrite="${index}">Overwrite current version</button>
                <button type="button" class="explorer-edit-bar-dismiss" data-explorer-edit-dismiss="${index}" title="Dismiss" aria-label="Dismiss">×</button>
            </span>
        `;
        bar.querySelector(`[data-explorer-edit-reload="${index}"]`)
            ?.addEventListener('click', () => explorerReloadEditedFile(index));
        bar.querySelector(`[data-explorer-edit-overwrite="${index}"]`)
            ?.addEventListener('click', () => explorerOverwriteConflict(index));
        bar.querySelector(`[data-explorer-edit-dismiss="${index}"]`)
            ?.addEventListener('click', () => {
                clearExplorerEditBar(index);
                document.getElementById(`explorer-edit-textarea-${index}`)?.focus();
            });
    }

    async function explorerReloadEditedFile(index) {
        const pane = terminals[index];
        const state = explorerEditState(pane);
        if (!pane || !state) {
            return;
        }
        const confirmed = await openGenericConfirmModal({
            title: 'Reload from disk?',
            copy: 'This discards your unsaved changes and loads the current file from disk.',
            confirmLabel: 'Reload',
            danger: true
        });
        if (!confirmed) {
            return;
        }
        const path = state.path;
        pane._explorerEdit = null;
        clearExplorerEditBar(index);
        setExplorerEditChromeDisabled(index, false);
        await openExplorerFile(index, path, { showLoading: false, tab: pane._explorerActiveTabId });
    }

    async function explorerOverwriteConflict(index) {
        const pane = terminals[index];
        const state = explorerEditState(pane);
        if (!state) {
            return;
        }
        const confirmed = await openGenericConfirmModal({
            title: 'Overwrite the newer version?',
            copy: 'This replaces the current on-disk file with your version. The other change will be lost.',
            confirmLabel: 'Overwrite',
            danger: true
        });
        if (!confirmed) {
            return;
        }
        clearExplorerEditBar(index);
        // The retry still performs a server-side revision check against
        // current_revision; a further change conflicts again.
        saveExplorerEdit(index);
    }

    /* One page-close guard for every dirty Explorer pane: an in-page modal
       cannot run during unload, so this triggers the browser's own warning. */
    function installExplorerEditBeforeUnload() {
        window.addEventListener('beforeunload', event => {
            if (hasAnyDirtyExplorerEdit()) {
                event.preventDefault();
                event.returnValue = '';
                return '';
            }
            return undefined;
        });
    }

    installExplorerEditBeforeUnload();
