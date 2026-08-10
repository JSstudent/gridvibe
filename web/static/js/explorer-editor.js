    /* ─────────────────────────────────────────────
       Explorer in-app text editor.
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
       confirmed (or the buffer was clean). Sync the tab marker immediately:
       some exits refresh the file in place and do not rebuild the tab strip.

       This is also the one choke point that stops dictation: every deliberate
       teardown route (cancel, discard, save success, reload from disk, tab or
       pane close) already funnels here, so no exit path needs its own stop
       call. `_stopVoice` clears `state.recording` before its first await, so
       the microphone goes quiet in this tick even though the promise is not
       awaited — this function is synchronous and several callers depend on
       that. The expected-transcript epoch is deliberately *not* cleared, so a
       transcript that lands after the editor is gone is reported instead of
       silently vanishing. */
    function clearExplorerEditState(index) {
        const pane = terminals[index];
        if (pane) {
            const state = pane._explorerEdit;
            const capturedEpoch = state && state.voice ? state.voice.epoch : null;
            explorerEditorCancelVoiceSettle(state);
            pane._explorerEdit = null;
            if (capturedEpoch !== null) {
                explorerEditorExpireOrphanedDictation(index, capturedEpoch);
                Promise.resolve(_stopVoice(index)).catch(() => {});
            }
            updateExplorerEditTabDirty(index);
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
       edit, and on confirmation *every* open editor in the group leaves edit
       mode — not only the dirty ones.

       Both halves matter because this guard's callers (group switch, group
       close, session move) do not rebuild these cards: `cacheVisibleGroupView`
       detaches them into a fragment exactly as they stand. Dropping the state
       without rebuilding the view cached a read-only Source panel still
       wearing Save/Cancel and a locked file chrome, and leaving a clean editor
       untouched contradicted the promise this very dialog makes. Both come
       back on the next visit with no way out but Cancel. */
    async function confirmDiscardAllExplorerEdits(actionLabel = '') {
        if (hasAnyDirtyExplorerEdit()) {
            const dirtyCount = terminals.filter(
                (_, index) => hasDirtyExplorerEdit(index)
            ).length;
            const confirmed = await openGenericConfirmModal({
                title: 'Discard unsaved changes?',
                copy: dirtyCount > 1
                    ? `${dirtyCount} open files have unsaved changes.`
                    : 'An open file has unsaved changes.',
                note: actionLabel ? `${actionLabel} will discard them.` : '',
                confirmLabel: 'Discard changes',
                danger: true
            });
            if (!confirmed) {
                return false;
            }
        }
        exitAllExplorerEditModes();
        return true;
    }

    /* Leave edit mode on every pane that has one, restoring each pane's
       read-only Source view and file chrome. No focus is moved: the caller is
       about to replace or detach this grid. */
    function exitAllExplorerEditModes() {
        terminals.forEach((pane, index) => {
            if (explorerEditState(pane)) {
                exitExplorerEditMode(index, { focusEditButton: false });
            }
        });
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
            // Dictation and Save are mutually exclusive on the same buffer: a
            // transcript landing across a save would be written into a draft
            // that is already being discarded.
            const dictating = Boolean(state.voice);
            const canSave = state.dirty && !state.saving && !dictating;
            const saveTitle = dictating ? 'Stop dictation to save' : 'Save (Ctrl+S)';
            host.innerHTML = `
                ${explorerEditorVoiceHtml(index)}
                <button type="button" class="explorer-editor-action-btn explorer-edit-save-btn${state.saving ? ' is-busy' : ''}" data-explorer-edit-save="${index}" ${canSave ? '' : 'disabled'} title="${escHtml(saveTitle)}" aria-label="Save file">${EXPLORER_SAVE_ICON}<span class="explorer-editor-action-label">Save</span></button>
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
        // The mic is rebuilt with the rest of the group on every refresh, so
        // its listeners and its live recording state are re-established here.
        wireExplorerEditorVoice(index);
        syncExplorerEditorVoiceButton(index);
    }

    /* While editing, the non-editor file chrome is disabled so a stray click
       cannot swap views, search, or download the old disk copy. Zoom, line
       wrapping and the appearance menu stay live — like the wrap toggle they
       only restyle the surface (CSS custom properties on panels that are not
       rebuilt), and the Source font they set is the one being typed into. */
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

    function restoreExplorerEditViewport(element, viewport) {
        if (!element || !viewport) {
            return;
        }
        const apply = () => applyScrollMetrics(element, viewport);
        apply();
        requestAnimationFrame(apply);
    }

    function enterExplorerEditMode(index) {
        const pane = terminals[index];
        if (!pane || !pane._explorerFileEditable || explorerEditState(pane) || pane._explorerMode !== 'file') {
            return;
        }
        // Source only, diff split closed (2 in §5.2).
        setExplorerFileView(index, 'source');
        clearExplorerEditBar(index);

        const sourcePanel = document.getElementById(`explorer-code-${index}`);
        const sourceViewport = captureScrollMetrics(sourcePanel);
        const normalized = explorerNormalizeEditNewlines(pane._explorerFileContent || '');
        pane._explorerEdit = {
            tabId: pane._explorerActiveTabId,
            path: pane._explorerFilePath,
            originalContent: normalized,
            draft: normalized,
            baseRevision: pane._explorerFileRevision || '',
            conflictRevision: '',
            dirty: false,
            saving: false,
            sourceViewport,
            voice: null   // { epoch, phase, settleTimer } while dictating
        };

        renderExplorerEditTextarea(index);
        setExplorerEditChromeDisabled(index, true);
        refreshExplorerEditControls(index);
        applyExplorerEditorFontSize(index);

        const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
        if (textarea) {
            textarea.setSelectionRange(0, 0);
            textarea.focus({ preventScroll: true });
            restoreExplorerEditViewport(textarea, sourceViewport);
        }
    }

    function renderExplorerEditTextarea(index) {
        const pane = terminals[index];
        const code = document.getElementById(`explorer-code-${index}`);
        const state = explorerEditState(pane);
        if (!pane || !code || !state) {
            return;
        }
        // The editor honours the tab's Source line-wrap flag; `soft` never
        // rewrites the value, so the saved bytes are the same either way.
        const wrap = explorerLineWrapPreference(index, 'source') ? 'soft' : 'off';
        code.innerHTML = `<textarea id="explorer-edit-textarea-${index}" class="explorer-source-editor" spellcheck="false" wrap="${wrap}" aria-label="Edit ${escHtml(pane._explorerFileName || 'file')}"></textarea>`;
        const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
        if (!textarea) {
            return;
        }
        textarea.value = state.draft;
        textarea.addEventListener('input', () => handleExplorerEditInput(index));
        textarea.addEventListener('keydown', event => handleExplorerEditKeydown(index, event));
        /* The panel now holds a textarea instead of numbered rows, so the
           change marks have nothing to sit on and the overview has nothing to
           survey. Re-applying them is what stands the overview column down
           (and exitExplorerEditMode's renderExplorerSource brings it back);
           the cached model itself is kept, so leaving the editor costs no
           refetch. */
        applyExplorerChangeMarks(index);
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
                saveBtn.disabled = !(dirty && !state.saving && !state.voice);
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
    function exitExplorerEditMode(index, { focusEditButton = true } = {}) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
        const editViewport = captureScrollMetrics(textarea);
        clearExplorerEditState(index);
        clearExplorerEditBar(index);
        renderExplorerSource(index);
        restoreExplorerEditViewport(
            document.getElementById(`explorer-code-${index}`),
            editViewport
        );
        setExplorerEditChromeDisabled(index, false);
        refreshExplorerEditControls(index);
        applyExplorerSearch(index);
        if (focusEditButton) {
            document.querySelector(`[data-explorer-edit="${index}"]`)?.focus();
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
        /* Defence in depth behind the disabled Save button: Ctrl+S and the
           conflict bar's Overwrite reach this function directly. Saving now
           would post a draft the settling transcript is about to be appended
           to, against a revision that has already moved on. */
        if (state.voice) {
            const settling = state.voice.phase === 'settling';
            explorerEditorStopDictation(index);
            showTerminalToast(settling
                ? 'Waiting for the transcript — press Ctrl+S again in a moment.'
                : 'Dictation stopped — press Ctrl+S again to save.');
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
        clearExplorerEditState(index);
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
        clearExplorerEditState(index);
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

    /* ── Editor dictation ─────────────────────────────────────────────────────
       The editor's own mic. It exists only while an edit session is open — no
       editor, no mic — which is why there is no disabled-with-a-tooltip state
       and why the pane-header mic stays hidden on explorer panes.

       Capture itself is unchanged: one recorder per page, driven by the same
       _voiceState / _voiceActiveIndex machinery as a terminal, entered through
       the same _startVoice/_stopVoice. What is added here is a binding between
       a running capture and the buffer that started it:

           (none) → recording → settling → (none)

       `settling` is the window between "the user let go" and "the words
       exist" — with the shipped Whisper engine the transcript is produced
       after voice_stop, and a save in that window would lose it. Save stays
       unavailable for the whole binding, bounded by a settle timer so a
       transcript that never arrives cannot hold the editor hostage.

       Nothing here is persisted: the binding lives on pane._explorerEdit,
       which is transient in-memory state by design. */
    const EXPLORER_VOICE_SETTLE_MS = 5000;
    let _explorerVoiceEpochCounter = 0;
    /* The epoch of the capture each pane still owes a transcript for. Kept
       past the edit session's teardown so a late transcript can be reported
       rather than silently dropped; keyed by pane index, so it is bounded by
       the grid. */
    const _explorerDictationEpochs = {};

    /* Hidden rather than disabled when voice is off in App Settings, mirroring
       the pane-header .voice-control wrapper: a feature the user turned off
       should not leave a dead control in the editor's button group. */
    function explorerEditorVoiceHtml(index) {
        const hidden = _voiceServiceStatus.enabled === false ? ' hidden' : '';
        return `<span class="explorer-editor-voice" data-explorer-editor-voice-control="${index}"${hidden}>`
            + `<button type="button" class="explorer-editor-action-btn voice-btn explorer-editor-voice-btn"`
            + ` id="explorer-voice-${index}" data-terminal-voice="${index}"`
            + ` title="Voice input (click to start recording)" aria-label="Dictate into this file">${VOICE_MIC_ICON}</button>`
            + `</span>`;
    }

    function wireExplorerEditorVoice(index) {
        const control = document.querySelector(`[data-explorer-editor-voice-control="${index}"]`);
        const button = document.getElementById(`explorer-voice-${index}`);
        if (!control || !button) {
            return;
        }
        button.addEventListener('click', () => {
            const state = explorerEditState(terminals[index]);
            if (state && state.saving) {
                showTerminalToast('Saving — start dictation again once the save finishes.', 'error');
                return;
            }
            _toggleVoice(index);
        });
        _wireVoiceHoldToTalkElements(button, control, index);
    }

    /* A refresh rebuilds the button, so the live capture state has to be put
       back on it. _setVoiceBtnsDisabled owns the cross-pane "another pane is
       recording" rule; the two editor-only states are layered on top. */
    function syncExplorerEditorVoiceButton(index) {
        const control = document.querySelector(`[data-explorer-editor-voice-control="${index}"]`);
        const button = document.getElementById(`explorer-voice-${index}`);
        if (!control || !button) {
            return;
        }
        control.hidden = _voiceServiceStatus.enabled === false;
        _updateVoiceBtn(index, Boolean(_voiceState[index]?.recording));
        _setVoiceBtnsDisabled(_voiceActiveIndex);
        const state = explorerEditState(terminals[index]);
        if (state && state.saving) {
            button.disabled = true;
            button.title = 'Saving — dictation is unavailable';
            return;
        }
        if (state && state.voice && state.voice.phase === 'settling') {
            button.disabled = true;
            button.title = 'Waiting for the transcript…';
        }
    }

    /* Push-to-talk only reaches the editor when the keybind carries a real
       modifier. _matchesPttKeybind matches a bare printable key exactly and the
       handler calls preventDefault(), so a keybind of `V` would start a
       recording and swallow every `v` the user typed into the file. A terminal
       can afford that; a text buffer cannot. Without a modifier the editor
       simply has no push-to-talk and the mic button still works. */
    function explorerEditorPttModifierPresent(keybind) {
        const parts = String(keybind || '').split('+');
        return parts.includes('Ctrl') || parts.includes('Alt') || parts.includes('Cmd');
    }

    function explorerEditorVoiceTargetIndex() {
        if (!explorerEditorPttModifierPresent(_voicePrefs.pttKeybind)) {
            return -1;
        }
        const active = document.activeElement;
        const match = /^explorer-edit-textarea-(\d+)$/.exec((active && active.id) || '');
        if (!match) {
            return -1;
        }
        const index = Number.parseInt(match[1], 10);
        if (!sessionIds[index] || !explorerEditState(terminals[index])) {
            return -1;
        }
        return index;
    }

    function explorerEditorCancelVoiceSettle(state) {
        if (state && state.voice && state.voice.settleTimer) {
            window.clearTimeout(state.voice.settleTimer);
            state.voice.settleTimer = null;
        }
    }

    /* The edit session that owned this capture is gone, so there is no state
       left to hang the settle timer on — but the pane still owes a transcript
       and a late one must be reported rather than routed somewhere new. Bound
       that expectation on the same timer budget, so a pane index later reused
       by a terminal cannot inherit a stale one. Epochs are monotonic, so the
       guard can never delete a newer capture's entry. */
    function explorerEditorExpireOrphanedDictation(index, epoch) {
        window.setTimeout(() => {
            if (_explorerDictationEpochs[index] === epoch) {
                delete _explorerDictationEpochs[index];
            }
        }, EXPLORER_VOICE_SETTLE_MS);
    }

    function explorerEditorClearVoiceBinding(index, state) {
        explorerEditorCancelVoiceSettle(state);
        if (state) {
            state.voice = null;
        }
        delete _explorerDictationEpochs[index];
    }

    /* Called from the tail of _startVoice, for every pane. A pane with no open
       editor is left exactly as it is today. */
    function explorerEditorNoteVoiceStarted(index) {
        const state = explorerEditState(terminals[index]);
        if (!state || state.saving || state.voice) {
            return;
        }
        _explorerVoiceEpochCounter += 1;
        state.voice = {
            epoch: _explorerVoiceEpochCounter,
            phase: 'recording',
            settleTimer: null
        };
        _explorerDictationEpochs[index] = state.voice.epoch;
        refreshExplorerEditControls(index);
    }

    /* Called from the tail of _stopVoice, for every pane and every stop route:
       mic click, hold release, push-to-talk keyup, a backend error, a group
       switch, teardown. */
    function explorerEditorNoteVoiceStopped(index) {
        const state = explorerEditState(terminals[index]);
        if (!state || !state.voice || state.voice.phase !== 'recording') {
            return;
        }
        const epoch = state.voice.epoch;
        state.voice.phase = 'settling';
        state.voice.settleTimer = window.setTimeout(
            () => explorerEditorVoiceSettleExpired(index, epoch),
            EXPLORER_VOICE_SETTLE_MS
        );
        refreshExplorerEditControls(index);
    }

    /* The transcript never came. Release the buffer so the editor is savable
       again, and say so once — silence here would look like lost words. */
    function explorerEditorVoiceSettleExpired(index, epoch) {
        const state = explorerEditState(terminals[index]);
        if (!state || !state.voice || state.voice.epoch !== epoch) {
            return;
        }
        explorerEditorClearVoiceBinding(index, state);
        refreshExplorerEditControls(index);
        showTerminalToast('No transcript arrived — dictation ended.', 'error');
    }

    /* Stop a bound capture without leaving the editor. A capture already in its
       settling window is left to the timer: the words may still be on the way. */
    function explorerEditorStopDictation(index) {
        const state = explorerEditState(terminals[index]);
        if (!state || !state.voice || state.voice.phase !== 'recording') {
            return;
        }
        Promise.resolve(_stopVoice(index)).catch(() => {});
    }

    /* The two inputs terminals.js hands to resolveVoiceDelivery. */
    function explorerDictationBinding(index) {
        const state = explorerEditState(terminals[index]);
        if (!state || !state.voice) {
            return null;
        }
        return { epoch: state.voice.epoch, saving: Boolean(state.saving) };
    }

    function explorerDictationExpectedEpoch(index) {
        return Object.prototype.hasOwnProperty.call(_explorerDictationEpochs, index)
            ? _explorerDictationEpochs[index]
            : null;
    }

    /* Insert a final transcript at the caret of the edit session that started
       the capture. execCommand('insertText') is tried first because it is the
       only insertion that feeds the native undo stack — setRangeText mutates
       the value without pushing an undo entry, so a Ctrl+Z after dictation
       would jump straight past the dictated block. It requires focus, returns
       false when unavailable, and is deprecated, hence the fallback. */
    function deliverExplorerDictation(index, text) {
        const state = explorerEditState(terminals[index]);
        const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
        if (!state || !textarea) {
            explorerEditorClearVoiceBinding(index, state);
            showTerminalToast('Dictation was discarded — the editor is no longer open.', 'error');
            return;
        }
        const start = Number.isInteger(textarea.selectionStart)
            ? textarea.selectionStart
            : textarea.value.length;
        const end = Number.isInteger(textarea.selectionEnd) ? textarea.selectionEnd : start;
        const composed = GridVibeVoiceDictation.composeDictationInsert({
            before: textarea.value.slice(0, start),
            text
        }).text;

        let inserted = false;
        if (composed && document.activeElement === textarea) {
            try {
                inserted = Boolean(document.execCommand?.('insertText', false, composed));
            } catch (_) {
                inserted = false;
            }
        }
        if (composed && !inserted) {
            // The selection survives blur, so text still lands at the caret the
            // user left behind; focus is never stolen back.
            textarea.setRangeText(composed, start, end, 'end');
            handleExplorerEditInput(index);
        }
        explorerEditorClearVoiceBinding(index, state);
        refreshExplorerEditControls(index);
    }

    /* A cached group view is detached from the document while another group is
       shown, and every control lookup here goes through document.getElementById.
       So a capture that _stopAllVoice() ended during the switch could not clear
       this pane's mic or re-render its Save/Cancel group — the card came back
       with a stale recording ring and a Save button disabled by a binding that
       has since expired. Re-derive both from live state once the card is back
       in the document. */
    function resyncExplorerEditorOnAttach(index) {
        if (!explorerEditState(terminals[index])) {
            return;
        }
        // The Source-view restore ran the search machinery, which re-derives
        // the prev/next buttons edit mode had locked down.
        setExplorerEditChromeDisabled(index, true);
        refreshExplorerEditControls(index);
    }

    /* One toast per dropped transcript, and only for the reasons a user could
       mistake for lost words. A pane that was never dictating says nothing. */
    function noteExplorerDictationDropped(index, reason) {
        const messages = {
            'no-edit': 'Dictation was discarded — the editor is no longer open.',
            'epoch-mismatch': 'Dictation was discarded — it belonged to an earlier recording.',
            saving: 'Dictation was discarded — the file was being saved.'
        };
        const message = Object.prototype.hasOwnProperty.call(messages, reason)
            ? messages[reason]
            : '';
        if (!message) {
            return;
        }
        explorerEditorClearVoiceBinding(index, explorerEditState(terminals[index]));
        refreshExplorerEditControls(index);
        showTerminalToast(message, 'error');
    }
