/* GridVibeCloseSession — the three-outcome close prompt for one session group.

   Live terminals are memory-only: when the process ends, they end. So one
   misclick on a × must not silently kill a group, and the prompt offers the
   third way out as well as the two obvious ones — keep the session, save it as
   a reusable preset and *then* close it, or close it outright. Anything that is
   not one of those three presses (Escape, the backdrop) keeps the session.

   It lives here rather than on a page because two surfaces now open it: the
   workspace window's session tab, and the agent dashboard's session card. A
   second copy of a dialog is how two surfaces come to ask differently sized
   questions about the same irreversible act — the dashboard listing a session
   from another workspace makes that worse, not better, because the reader
   pressing × there cannot see the terminals they are about to end.

   Two halves, the split `minimize-all.js` and `notice-banner.js` use:

   - `policy` is pure — no DOM, no globals — so what the prompt says, and when
     it is skipped altogether, are executed by a Node test rather than asserted
     as source text.
   - `create(runtime)` is the adapter. Every DOM touch goes through the
     injected runtime, and the markup it drives is one shared partial
     (`templates/partials/close_session_modal.html`), so the two pages cannot
     drift apart in their ids either.

   A page with no such modal on it gets a controller that resolves to CANCEL
   and touches nothing, the way `openGenericConfirmModal` already behaves. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (!root || !root.document) return;

    const controller = api.create({
        getElement: id => root.document.getElementById(id),
        addDocumentListener: (type, handler) => root.document.addEventListener(type, handler),
        defer: callback => root.setTimeout(callback, 0)
    });

    /* The three decisions and the two verbs, under the names both pages
       already call. Published rather than re-declared per page: a page that
       kept its own copy of 'save-and-close' would compare equal today and
       silently stop matching the day this string changed. */
    root.CLOSE_SESSION_CANCEL = api.policy.CANCEL;
    root.CLOSE_SESSION_CLOSE = api.policy.CLOSE;
    root.CLOSE_SESSION_SAVE_AND_CLOSE = api.policy.SAVE_AND_CLOSE;
    root.openCloseSessionConfirmModal = request => controller.open(request);
    root.closeCloseSessionConfirmModal = decision => controller.close(decision);
    root.isCloseSessionConfirmModalVisible = () => controller.isVisible();
    root.closeSessionPromptSkipDecision = sessions => api.policy.skipDecision(sessions);
    root.closeSessionConnectedCount = sessions => api.policy.connectedCount(sessions);
    root.closeSessionPromptName = group => api.policy.promptName(group);

    controller.init();
}(typeof window !== 'undefined' ? window : null, function () {
    const MODAL_ID = 'closeSessionConfirmModal';
    const COPY_ID = 'closeSessionConfirmCopy';
    const CANCEL_ID = 'closeSessionConfirmCancel';
    const SAVE_ID = 'closeSessionConfirmSave';
    const ACCEPT_ID = 'closeSessionConfirmAccept';

    const CANCEL = 'cancel';
    const CLOSE = 'close';
    const SAVE_AND_CLOSE = 'save-and-close';

    function connectedCount(sessions) {
        return (Array.isArray(sessions) ? sessions : [])
            .filter(session => String(session?.status || '') === 'connected')
            .length;
    }

    /* When the prompt is not worth showing. A group whose panes have *all*
       stopped has nothing left to lose, so asking about it is a dialog that
       only ever has one sensible answer.

       The `sessions.length > 0` guard is load-bearing and is why this is not
       simply `connectedCount === 0`: an empty array is also the answer a failed
       status lookup produces, and the safe reading of "I could not find out" is
       to ask, not to close. Returns null for "ask". */
    function skipDecision(sessions) {
        const list = Array.isArray(sessions) ? sessions : [];
        return list.length > 0 && connectedCount(list) === 0 ? CLOSE : null;
    }

    /* What a session is called in the sentence. One rule, because the two
       surfaces read from different payloads — a live group on the workspace
       page, a composed dashboard row — and a session named one thing here and
       another there is a session the reader cannot match to the × they
       pressed. */
    function promptName(group) {
        return String(group?.name || '').trim()
            || String(group?.group_id || '').trim()
            || 'this session';
    }

    /* The question. It names the terminals rather than only the session,
       because "3 terminals (2 connected)" is the fact that makes the prompt
       worth reading; a group whose count could not be established asks the
       short form instead of inventing a zero. */
    function promptCopy(name, connected, total) {
        const totalCount = Number(total) || 0;
        const connectedTotal = Number(connected) || 0;
        const noun = totalCount === 1 ? 'terminal' : 'terminals';
        return totalCount > 0
            ? `Close "${name}" and its ${totalCount} ${noun} (${connectedTotal} connected)?`
            : `Close "${name}"?`;
    }

    const policy = {
        MODAL_ID,
        COPY_ID,
        CANCEL_ID,
        SAVE_ID,
        ACCEPT_ID,
        CANCEL,
        CLOSE,
        SAVE_AND_CLOSE,
        connectedCount,
        skipDecision,
        promptName,
        promptCopy
    };

    function create(runtime) {
        const {
            getElement,
            addDocumentListener = () => {},
            defer = callback => callback()
        } = runtime || {};

        let resolver = null;

        function element(id) {
            return getElement ? getElement(id) : null;
        }

        function isVisible() {
            const modal = element(MODAL_ID);
            return Boolean(modal && modal.classList && modal.classList.contains('visible'));
        }

        function close(decision = CANCEL) {
            const modal = element(MODAL_ID);
            if (modal) {
                modal.classList.remove('visible');
                modal.setAttribute('aria-hidden', 'true');
            }
            const pending = resolver;
            resolver = null;
            if (pending) {
                pending(decision);
            }
            return decision;
        }

        function open(request = {}) {
            const modal = element(MODAL_ID);
            if (!modal) {
                return Promise.resolve(CANCEL);
            }
            /* A second open never strands the first: the outgoing prompt keeps
               its session, exactly as Escape would. */
            close(CANCEL);

            const copy = element(COPY_ID);
            if (copy) {
                copy.textContent = promptCopy(
                    promptName(request.group || request),
                    request.connectedCount,
                    request.totalCount
                );
            }
            modal.classList.add('visible');
            modal.setAttribute('aria-hidden', 'false');
            /* Cancel takes focus, not the danger button: the prompt is reached
               by pressing a ×, and a focused destructive default turns a
               stray Enter into the thing being confirmed. */
            defer(() => element(CANCEL_ID)?.focus?.());

            return new Promise(resolve => {
                resolver = resolve;
            });
        }

        function init() {
            const modal = element(MODAL_ID);
            if (!modal) {
                return false;
            }
            modal.addEventListener('click', event => {
                if (event.target === modal) {
                    close(CANCEL);
                }
            });
            element(CANCEL_ID)?.addEventListener('click', () => close(CANCEL));
            element(SAVE_ID)?.addEventListener('click', () => close(SAVE_AND_CLOSE));
            element(ACCEPT_ID)?.addEventListener('click', () => close(CLOSE));
            addDocumentListener('keydown', event => {
                if (event?.key === 'Escape' && isVisible()) {
                    close(CANCEL);
                }
            });
            return true;
        }

        return { init, open, close, isVisible };
    }

    return { policy, create };
}));
