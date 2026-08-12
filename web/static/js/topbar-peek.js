/* GridVibeTopbarPeek — the hidden top bar's hover reveal.

   The workspace top bar leaves the layout for two independent reasons:

   - the session-bar chevron, which is the user's own choice and is persisted
     as the workspace's `topbar_visible`; and
   - fullscreen, which auto-hides it for the duration and must never write
     that setting — leaving fullscreen has to give back whatever the chevron
     last said.

   Either way the Sessions… and Workspace… menus went with it, and those menus
   are the only place **Save Session** and **Save Workspace** live: hiding the
   bar to get room took away the controls you hide it in order to keep using.
   This module gives the hidden bar back on demand — the pointer resting on the
   window's top edge, or the peek handle being clicked or focused — as an
   *overlay*, never a return to the flow, so a trip to the top edge costs no
   terminal refit and no persisted state changes at all.

   Two halves, deliberately separated (the notice-banner split):

   - `policy` is pure — no DOM, no timers, no globals. `isHidden` and
     `isRetained` are the whole decision: the bar is out of the flow when the
     chevron or fullscreen says so, and a revealed bar must stay for as long as
     the pointer is on it, focus is in it, or one of its menus is open.
   - `create(runtime)` is the adapter. Every element, timer, and class write
     goes through the injected runtime, which is what lets Node execute the
     behaviour instead of tests asserting source text.

   The page keeps ownership of what the reveal *looks* like: this module
   reports `{hidden, peeking}` and terminals.js maps that onto the two body
   classes and the one refit that a flow change actually needs. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeTopbarPeek = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const ZONE_ID = 'topbarPeekZone';
    const HANDLE_ID = 'topbarPeekHandle';
    const BAR_ID = 'terminalTopbar';
    /* Focus target for the handle: the first control in the bar, so activating
       the handle by keyboard lands somewhere you can Tab onward from. */
    const BAR_FOCUS_ID = 'sessionsMenuBtn';
    /* Long enough to cross the gap between the bar and a pointer that overshot
       it, short enough that the bar is gone by the time you have looked back
       at the pane you were aiming for. */
    const CLOSE_GRACE_MS = 320;

    /* ── Policy (pure) ── */

    /* The two inputs are independent and either one hides the bar; only their
       disjunction reaches the layout, which is why `collapsed` stays readable
       on its own — it is what gets persisted. */
    function isHidden(state) {
        return Boolean(state && (state.collapsed || state.fullscreen));
    }

    /* While any of these hold the peek must not close. The menu case is the
       whole point of the feature: an open Sessions… menu that vanished because
       the pointer had wandered a few pixels off the bar would be the same bug
       in a new place. */
    function isRetained(state) {
        return Boolean(state && (state.pointerInside || state.focusInside || state.menuOpen));
    }

    const policy = {
        CLOSE_GRACE_MS,
        isHidden,
        isRetained
    };

    /* ── DOM adapter ── */

    function create(runtime) {
        const io = runtime || {};
        const getElement = typeof io.getElement === 'function' ? io.getElement : () => null;
        const schedule = typeof io.setTimeout === 'function' ? io.setTimeout : null;
        const cancel = typeof io.clearTimeout === 'function' ? io.clearTimeout : () => {};
        const onChange = typeof io.onChange === 'function' ? io.onChange : () => {};

        const state = {
            collapsed: false,
            fullscreen: false,
            peeking: false,
            pointerInside: false,
            focusInside: false,
            menuOpen: false
        };
        let timer = null;

        function stopTimer() {
            if (timer === null) {
                return;
            }
            cancel(timer);
            timer = null;
        }

        function mark() {
            return { hidden: isHidden(state), peeking: state.peeking };
        }

        function emit(before) {
            const hidden = isHidden(state);
            const hiddenChanged = hidden !== before.hidden;
            const peekChanged = state.peeking !== before.peeking;
            if (!hiddenChanged && !peekChanged) {
                return;
            }
            onChange({ hidden, peeking: state.peeking, hiddenChanged, peekChanged });
        }

        /* Closing drops the retention flags with it. The bar disappears out
           from under the pointer, so the matching pointerleave may never
           arrive, and a stale `pointerInside` would leave the *next* reveal
           with nothing able to close it. */
        function clearPeek() {
            stopTimer();
            state.peeking = false;
            state.pointerInside = false;
            state.focusInside = false;
        }

        function scheduleClose() {
            stopTimer();
            if (!state.peeking) {
                return;
            }
            if (!schedule) {
                closeIfReleased();
                return;
            }
            timer = schedule(() => {
                timer = null;
                closeIfReleased();
            }, CLOSE_GRACE_MS);
        }

        function closeIfReleased() {
            if (!state.peeking || isRetained(state)) {
                return;
            }
            const before = mark();
            clearPeek();
            emit(before);
        }

        /* Every reveal path lands here, and every one of them also cancels a
           close already counting down — a pointer that leaves and comes back
           must not be closed by the trip it made. */
        function reveal() {
            stopTimer();
            if (!isHidden(state) || state.peeking) {
                return;
            }
            const before = mark();
            state.peeking = true;
            emit(before);
        }

        function setHiddenInput(key, value) {
            const next = Boolean(value);
            if (state[key] === next) {
                return;
            }
            const before = mark();
            state[key] = next;
            /* A bar that just left the flow starts un-peeked; one that just
               rejoined it cannot be peeking at all. Either way the pointer and
               focus flags describe an element that is no longer the same
               thing, so they go too. */
            if (isHidden(state) !== before.hidden) {
                clearPeek();
            }
            emit(before);
        }

        /* The chevron's persisted choice. */
        function setCollapsed(collapsed) {
            setHiddenInput('collapsed', collapsed);
        }

        /* Fullscreen, which hides the bar for its duration only and never
           touches what the chevron stored. */
        function setFullscreen(fullscreen) {
            setHiddenInput('fullscreen', fullscreen);
        }

        function setMenuOpen(open) {
            state.menuOpen = Boolean(open);
            if (state.menuOpen) {
                stopTimer();
            } else {
                scheduleClose();
            }
        }

        function pointerEnterBar() {
            if (!isHidden(state)) {
                return;
            }
            state.pointerInside = true;
            reveal();
        }

        function pointerLeaveBar() {
            if (!state.pointerInside) {
                return;
            }
            state.pointerInside = false;
            scheduleClose();
        }

        function focusEnterBar() {
            if (!isHidden(state)) {
                return;
            }
            state.focusInside = true;
            reveal();
        }

        function focusLeaveBar() {
            if (!state.focusInside) {
                return;
            }
            state.focusInside = false;
            scheduleClose();
        }

        /* Escape, and anything else that wants the overlay gone now. */
        function dismiss() {
            if (!state.peeking) {
                return;
            }
            const before = mark();
            clearPeek();
            emit(before);
        }

        /* A display:none bar has no control to Tab into, so the handle is the
           keyboard route in: activating it reveals the bar and hands focus to
           its first control, from which Tab reaches the rest. */
        function revealAndFocus() {
            reveal();
            const target = getElement(BAR_FOCUS_ID);
            if (target && typeof target.focus === 'function') {
                target.focus();
            }
        }

        function attach() {
            const zone = getElement(ZONE_ID);
            const handle = getElement(HANDLE_ID);
            const bar = getElement(BAR_ID);

            if (zone && typeof zone.addEventListener === 'function') {
                zone.addEventListener('pointerenter', () => reveal());
                /* The revealed bar covers the strip, so leaving it is normally
                   the same movement as entering the bar — which runs second
                   and cancels this close. Without it, a pointer that grazed
                   the top edge and went back out of the window would leave the
                   bar open with nothing able to close it: the bar itself never
                   saw a pointer, so it can never report one leaving. */
                zone.addEventListener('pointerleave', () => scheduleClose());
            }

            if (handle && typeof handle.addEventListener === 'function') {
                handle.addEventListener('click', () => revealAndFocus());
                handle.addEventListener('focus', () => reveal());
                /* The handle sits outside the bar, so focus moving from it
                   into the bar is a leave here and an enter there; the enter
                   runs second and cancels this close. Focus moving anywhere
                   else genuinely releases the peek. */
                handle.addEventListener('blur', () => scheduleClose());
            }

            if (bar && typeof bar.addEventListener === 'function') {
                bar.addEventListener('pointerenter', () => pointerEnterBar());
                bar.addEventListener('pointerleave', () => pointerLeaveBar());
                bar.addEventListener('focusin', () => focusEnterBar());
                bar.addEventListener('focusout', event => {
                    const next = event && event.relatedTarget;
                    /* An open menu panel is a descendant of the bar, so moving
                       focus into one is not leaving. */
                    if (next && typeof bar.contains === 'function' && bar.contains(next)) {
                        return;
                    }
                    focusLeaveBar();
                });
            }
        }

        return {
            attach,
            setCollapsed,
            setFullscreen,
            setMenuOpen,
            reveal,
            revealAndFocus,
            pointerEnterBar,
            pointerLeaveBar,
            focusEnterBar,
            focusLeaveBar,
            dismiss,
            /* A copy, so a caller cannot reach in and desynchronise the state
               from the classes the page has already painted. */
            state: () => ({
                collapsed: state.collapsed,
                fullscreen: state.fullscreen,
                peeking: state.peeking,
                pointerInside: state.pointerInside,
                focusInside: state.focusInside,
                menuOpen: state.menuOpen,
                hidden: isHidden(state),
                retained: isRetained(state)
            })
        };
    }

    return {
        ZONE_ID,
        HANDLE_ID,
        BAR_ID,
        BAR_FOCUS_ID,
        policy,
        create
    };
}));
