(function lifecycleModule(root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) {
        module.exports = api;
    }
    if (root) {
        root.GridVibeLifecycle = api;
        root.openGridVibeLifecycleModal = api.openModal;
        root.gridvibeRequestLifecycleClose = api.requestNativeClose;
    }
})(typeof window !== 'undefined' ? window : globalThis, function buildLifecycleModule() {
    const SAVE_NONE = 'none';
    const SAVE_WORKSPACES = 'workspaces';
    const SAVE_SESSIONS_AND_WORKSPACES = 'sessions+workspaces';
    const SAVE_CHOICES = new Set([
        SAVE_NONE,
        SAVE_WORKSPACES,
        SAVE_SESSIONS_AND_WORKSPACES
    ]);
    let modalState = null;
    let modalWired = false;

    async function prepare(fetchImpl, action, save) {
        if (!['close', 'restart'].includes(action) || !SAVE_CHOICES.has(save)) {
            throw new Error('Unknown lifecycle action');
        }
        const response = await fetchImpl('/api/lifecycle/prepare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, save })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ready_to_exit) {
            const error = new Error(data.error || 'GridVibe is still open because saving did not finish.');
            error.result = data;
            throw error;
        }
        return data;
    }

    function actionWords(action) {
        const close = action === 'close';
        return {
            title: close ? 'Close GridVibe?' : 'Restart GridVibe?',
            verb: close ? 'close' : 'restart',
            suffix: close ? 'close' : 'restart'
        };
    }

    function elements() {
        if (typeof document === 'undefined') return {};
        return {
            modal: document.getElementById('lifecycleModal'),
            card: document.querySelector('#lifecycleModal .lifecycle-card'),
            title: document.getElementById('lifecycleTitle'),
            copy: document.getElementById('lifecycleCopy'),
            status: document.getElementById('lifecycleStatus'),
            choices: document.getElementById('lifecycleChoices'),
            cancel: document.getElementById('lifecycleCancel')
        };
    }

    function setBusy(busy) {
        const { card, modal } = elements();
        card?.classList.toggle('is-busy', Boolean(busy));
        modal?.setAttribute('aria-busy', busy ? 'true' : 'false');
    }

    function setStatus(message, error = false) {
        const { status } = elements();
        if (!status) return;
        status.textContent = message || '';
        status.classList.toggle('is-error', Boolean(error));
    }

    function closeModal() {
        const { modal } = elements();
        if (modal) {
            modal.classList.remove('visible');
            modal.setAttribute('aria-hidden', 'true');
        }
        setBusy(false);
        modalState = null;
    }

    function cancelModal() {
        const onCancel = modalState?.onCancel;
        closeModal();
        onCancel?.();
    }

    function errorSummary(result) {
        const errors = Array.isArray(result?.errors) ? result.errors : [];
        const messages = errors
            .map(item => String(item?.error || '').trim())
            .filter(Boolean);
        return messages.join(' ') || 'Saving did not finish. GridVibe remains open.';
    }

    function showChoices() {
        setStatus('Choose what GridVibe should do before continuing.');
    }

    function showFailure(result) {
        setStatus(`${errorSummary(result)} Choose an option to try again or continue without saving.`, true);
    }

    async function runChoice(save) {
        if (!modalState || modalState.busy) return;
        modalState.busy = true;
        setBusy(true);
        setStatus(save === SAVE_NONE ? 'Preparing to continue without saving…' : 'Saving current state…');
        try {
            const result = await prepare(modalState.fetchImpl, modalState.action, save);
            const onReady = modalState.onReady;
            const onError = modalState.onError;
            closeModal();
            try {
                await onReady(result.decision_token, result);
            } catch (error) {
                onError?.(error);
            }
        } catch (error) {
            if (modalState) {
                modalState.busy = false;
                setBusy(false);
                showFailure(error.result || { errors: [{ error: error.message }] });
                modalState.onError?.(error);
            }
        }
    }

    function wireModal() {
        if (modalWired || typeof document === 'undefined') return;
        const { choices, cancel, modal } = elements();
        if (!modal) return;
        modalWired = true;
        choices?.querySelectorAll('[data-lifecycle-save]').forEach(button => {
            button.addEventListener('click', () => runChoice(button.dataset.lifecycleSave));
        });
        cancel?.addEventListener('click', cancelModal);
    }

    function openModal({ action, onReady, onError, onCancel, fetchImpl } = {}) {
        if (!['close', 'restart'].includes(action) || typeof onReady !== 'function') {
            throw new Error('A lifecycle action and completion callback are required');
        }
        wireModal();
        const { modal, title, copy, choices } = elements();
        if (!modal) throw new Error('Lifecycle dialog is unavailable');
        const words = actionWords(action);
        modalState = {
            action,
            onReady,
            onError,
            onCancel,
            fetchImpl: fetchImpl || fetch.bind(window),
            busy: false
        };
        if (title) title.textContent = words.title;
        if (copy) copy.textContent = `Choose whether to save current changes before GridVibe ${words.suffix}s.`;
        choices?.querySelector(`[data-lifecycle-save="${SAVE_NONE}"]`)?.replaceChildren(
            'Continue without saving current changes'
        );
        choices?.querySelector(`[data-lifecycle-save="${SAVE_WORKSPACES}"]`)?.replaceChildren(
            `Save open workspaces & ${words.verb}`
        );
        choices?.querySelector(`[data-lifecycle-save="${SAVE_SESSIONS_AND_WORKSPACES}"]`)?.replaceChildren(
            `Save open sessions + workspaces & ${words.verb}`
        );
        showChoices();
        modal.classList.add('visible');
        modal.setAttribute('aria-hidden', 'false');
        return true;
    }

    function attachFlushResponder(socket, { workspaceId, flush, metadata }) {
        if (!socket?.on || !socket?.emit || typeof flush !== 'function') {
            throw new Error('A socket and flush callback are required');
        }
        const resolvedWorkspaceId = String(workspaceId || '').trim();
        socket.on('lifecycle_flush_requested', async request => {
            if (String(request?.workspace_id || '') !== resolvedWorkspaceId) return;
            const acknowledgement = {
                request_id: String(request?.request_id || ''),
                workspace_id: resolvedWorkspaceId,
                ok: false
            };
            try {
                const flushed = await flush();
                if (flushed?.ok === false) {
                    throw flushed.error || new Error('Presentation flush failed');
                }
                acknowledgement.metadata = typeof metadata === 'function'
                    ? await metadata()
                    : {};
                acknowledgement.ok = true;
            } catch (error) {
                acknowledgement.error = String(error?.message || 'Presentation flush failed').slice(0, 300);
            }
            socket.emit('lifecycle_flush_ack', acknowledgement);
        });
    }

    function requestNativeClose() {
        return openModal({
            action: 'close',
            onReady: async decisionToken => {
                const bridge = window.pywebview?.api;
                if (!bridge?.approve_application_close) {
                    throw new Error('Native close is unavailable');
                }
                const result = await bridge.approve_application_close(decisionToken);
                if (!result?.ok) throw new Error(result?.error || 'GridVibe could not be closed');
            },
            onCancel: () => window.pywebview?.api?.cancel_application_close_request?.()
        });
    }

    return {
        SAVE_NONE,
        SAVE_WORKSPACES,
        SAVE_SESSIONS_AND_WORKSPACES,
        prepare,
        attachFlushResponder,
        openModal,
        requestNativeClose
    };
});
