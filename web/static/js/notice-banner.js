/* GridVibeNotice — the one global notification surface.

   The launcher used to have two independent message sinks: `#message`, which
   was markup inside the Terminal Layout card and so painted every unrelated
   failure into step 01, and `#quickUpdateStatus`, which was added next to the
   update button because the first one was in the wrong place. The update flow
   wrote to both, so one failure arrived twice, in two corners, with two
   lifetimes.

   The contract this module owns: **one message, one surface, one at a time.**
   A new notice of any severity replaces whatever is showing — there is no
   stack, no queue, and no history, because this is a notification, not a
   debugging log. The banner is launcher content and stays behind every
   dialog; a failure raised with a dialog open persists and is read when the
   dialog closes.

   Two halves, deliberately separated:

   - `policy` is pure — no DOM, no timers, no globals. `nextState` is the
     single owner of replacement, so nothing else decides what is on screen.
   - `create(runtime)` is the adapter. Every DOM and timer touch goes through
     the injected runtime, which is what lets Node execute the behaviour
     instead of tests asserting source text.

   Page-agnostic by construction (D11): it binds to ids from
   `templates/partials/notice_banner.html`, holds no launcher-specific
   knowledge, and the workspace page can adopt it by including the same
   partial. Only the launcher wires it up today. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    /* A page that has a document gets one instance and the two thin globals
       that use it: showGridVibeNotice(text, type) for every call site, and
       dismissGridVibeNotice() for the partial's × button. The instance is
       created eagerly but looks its elements up lazily, so a notice fired
       before the banner markup is parsed is a no-op that still reaches the
       console rather than a throw during page init. */
    if (!root || !root.document) return;
    const banner = api.create({
        getElement: id => root.document.getElementById(id),
        setTimeout: (fn, ms) => root.setTimeout(fn, ms),
        clearTimeout: handle => root.clearTimeout(handle),
        logError: text => root.console && root.console.error(text)
    });
    root.showGridVibeNotice = (text, type) => banner.show(text, type);
    root.dismissGridVibeNotice = () => banner.dismiss();
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const ELEMENT_ID = 'gvNoticeBanner';
    const TEXT_ID = 'gvNoticeText';
    const TYPES = ['error', 'warning', 'success', 'info'];
    const DEFAULT_TYPE = 'info';
    const AUTO_DISMISS_MS = 6000;
    /* Generous enough for a wrapped git error, low enough that a runaway
       server response cannot make the banner the whole page. The CSS caps the
       rendered height on top of this. */
    const MAX_TEXT_LENGTH = 600;

    function iconElementId(type) {
        return `gvNoticeIcon${type.charAt(0).toUpperCase()}${type.slice(1)}`;
    }

    /* ── Policy (pure) ── */

    /* Call sites pass '' for "no particular severity", and a few pass a value
       straight from a server payload, so anything unrecognised has to resolve
       rather than throw. */
    function normalizeType(value) {
        const normalized = String(value === null || value === undefined ? '' : value)
            .trim()
            .toLowerCase();
        return TYPES.includes(normalized) ? normalized : DEFAULT_TYPE;
    }

    function normalizeText(value) {
        if (value === null || value === undefined) {
            return '';
        }
        const collapsed = String(value).replace(/\s+/g, ' ').trim();
        if (collapsed.length <= MAX_TEXT_LENGTH) {
            return collapsed;
        }
        let cut = collapsed.slice(0, MAX_TEXT_LENGTH - 1);
        const tail = cut.charCodeAt(cut.length - 1);
        /* Never split a surrogate pair — half a pair renders as a replacement
           glyph, which looks like corruption in the middle of an error. */
        if (tail >= 0xd800 && tail <= 0xdbff) {
            cut = cut.slice(0, -1);
        }
        return `${cut}…`;
    }

    /* A failure must never disappear before it is read; a confirmation should
       not need dismissing. */
    function isPersistent(type) {
        const normalized = normalizeType(type);
        return normalized === 'error' || normalized === 'warning';
    }

    function autoDismissMs(type) {
        return isPersistent(type) ? 0 : AUTO_DISMISS_MS;
    }

    function shouldEchoToConsole(type) {
        return normalizeType(type) === 'error';
    }

    /* The single owner of "what is on screen". Returns the whole next slot, or
       null for "nothing" — never an array and never an append, which is what
       makes stacking impossible by construction. The revision lets the adapter
       tell its own pending auto-dismiss from a superseded one. */
    function nextState(current, incoming) {
        const text = normalizeText(incoming && incoming.text);
        if (!text) {
            return null;
        }
        const type = normalizeType(incoming && incoming.type);
        const previousRevision = Number(current && current.revision);
        return {
            text,
            type,
            persistent: isPersistent(type),
            autoDismissMs: autoDismissMs(type),
            echo: shouldEchoToConsole(type),
            revision: (Number.isFinite(previousRevision) ? previousRevision : 0) + 1
        };
    }

    const policy = {
        TYPES: TYPES.slice(),
        DEFAULT_TYPE,
        AUTO_DISMISS_MS,
        MAX_TEXT_LENGTH,
        normalizeType,
        normalizeText,
        isPersistent,
        autoDismissMs,
        shouldEchoToConsole,
        nextState
    };

    /* ── DOM adapter ── */

    function create(runtime) {
        const io = runtime || {};
        const getElement = typeof io.getElement === 'function' ? io.getElement : () => null;
        const schedule = typeof io.setTimeout === 'function' ? io.setTimeout : null;
        const cancel = typeof io.clearTimeout === 'function' ? io.clearTimeout : () => {};
        const logError = typeof io.logError === 'function' ? io.logError : () => {};

        let slot = null;
        /* One module-scope handle, cleared by every show(): a per-notice timer
           is how a replaced notice ends up hiding its successor. */
        let timer = null;

        function stopTimer() {
            if (timer === null) {
                return;
            }
            cancel(timer);
            timer = null;
        }

        function hide() {
            const element = getElement(ELEMENT_ID);
            if (element) {
                element.setAttribute('hidden', '');
            }
        }

        function paint(state) {
            const element = getElement(ELEMENT_ID);
            const textNode = getElement(TEXT_ID);
            if (!element || !textNode) {
                return false;
            }
            /* textContent, never innerHTML — a server error is allowed to
               contain '<' without becoming markup. */
            textNode.textContent = state.text;
            TYPES.forEach(name => {
                element.classList.toggle(`is-${name}`, name === state.type);
                const icon = getElement(iconElementId(name));
                if (!icon) {
                    return;
                }
                if (name === state.type) {
                    icon.removeAttribute('hidden');
                } else {
                    icon.setAttribute('hidden', '');
                }
            });
            element.setAttribute('aria-live', state.persistent ? 'assertive' : 'polite');
            element.removeAttribute('hidden');
            return true;
        }

        function show(text, type) {
            const state = nextState(slot, { text, type });
            stopTimer();
            if (!state) {
                slot = null;
                hide();
                return null;
            }
            if (state.echo) {
                logError(state.text);
            }
            slot = state;
            if (!paint(state) || state.persistent || !schedule) {
                return state;
            }
            timer = schedule(() => {
                timer = null;
                /* A notice that has already been replaced or dismissed must not
                   be able to hide whatever took its place. */
                if (slot && slot.revision === state.revision) {
                    slot = null;
                    hide();
                }
            }, state.autoDismissMs);
            return state;
        }

        /* Cosmetic by contract: dismissing cancels, blocks, and changes
           nothing else in the page. */
        function dismiss() {
            stopTimer();
            slot = null;
            hide();
        }

        /* `current` is the slot itself, which is what makes "exactly one
           notice, ever" assertable without reading the DOM back. */
        return { show, dismiss, current: () => slot };
    }

    return {
        ELEMENT_ID,
        TEXT_ID,
        iconElementId,
        policy,
        create
    };
}));
