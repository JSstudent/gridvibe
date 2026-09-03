/* GridVibeMinimizeAll — one control that gets every GridVibe window out of
   the way at once, in native desktop mode only.

   It minimizes rather than hides. Hiding clears the taskbar too, but with
   every window hidden no GridVibe page has focus, so no page-level keydown
   can hear the way back — the only honest answer would be an OS-level global
   hotkey or a tray icon, both of which are more mechanism than this control
   is worth. Minimized, the taskbar *is* the way back, and clicking any entry
   restores that window through the launcher's own
   `_restore_minimized_window()`. The cost is honest: N taskbar entries stay
   behind, so this is "get out of the way", not "disappear".

   Two halves, the same split `notice-banner.js` uses:

   - `policy` is pure — no DOM, no globals — so the chord rule is executed by
     a Node test rather than asserted as source text.
   - `create(runtime)` is the adapter. Every DOM and bridge touch goes through
     the injected runtime.

   Page-agnostic by construction: both templates render the same button id and
   both include this file. A page that wants a shortcut target to block the
   chord (a focused input, a pane's helper textarea) declares the optional
   hook `minimizeAllShortcutBlocked(target)`, looked up by name at call time
   the way `app-settings.js` looks up its own page hooks. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (!root || !root.document) return;

    const control = api.create({
        getElement: id => root.document.getElementById(id),
        getBridge: () => (root.pywebview && root.pywebview.api) || null,
        isBlockingTarget: target => (
            typeof root.minimizeAllShortcutBlocked === 'function'
                ? Boolean(root.minimizeAllShortcutBlocked(target))
                : false
        ),
        logError: (message, error) => console.error(message, error)
    });

    /* The button's own onclick, and the names a page can call directly. The
       availability predicate is exported because App Settings asks the same
       question about its native-only cascade checkbox, and two copies of it
       is how a shown field and a missing button come to disagree. */
    root.minimizeAllGridVibeWindows = () => control.minimizeAll();
    root.syncGridVibeMinimizeAllControl = () => control.syncControl();
    root.gridVibeMinimizeAllAvailable = () => control.available();

    root.document.addEventListener('keydown', event => control.handleKeydown(event));
    /* The bridge is not there when this file is evaluated, so the control is
       revealed on the event that says it has arrived — and once more now, for
       a page that loaded after `pywebviewready` had already fired. */
    root.addEventListener('pywebviewready', () => control.syncControl());
    if (root.document.readyState === 'loading') {
        root.document.addEventListener('DOMContentLoaded', () => control.syncControl());
    } else {
        control.syncControl();
    }
}(typeof window !== 'undefined' ? window : null, function () {
    const BUTTON_ID = 'minimizeAllWindowsBtn';

    /* Alt+X, matched on `event.code`.

       An Alt chord rather than a Ctrl+Alt one because AltGr reaches the page
       as Ctrl+Alt on Windows, so `Ctrl+Alt+C` — what the note originally
       asked for — is `AltGr+C` = `&` on a Slovenian layout and cannot be
       produced without also typing a character. `!event.ctrlKey` is what
       keeps AltGr out here, exactly as the Alt+Q and Alt+W handlers already
       do, and it is load-bearing rather than boilerplate.

       `X` is `X` on both QWERTY and QWERTZ, so the physical key this matches
       and the name printed beside it agree — no second rule to get wrong.
       It also sits far from the navigation family (Alt+Q launcher, Alt+W
       workspaces, Alt+1..9 panes), so a mis-reach cannot land on the one
       control that moves every window at once. `\ex` is unbound in readline,
       so the xterm pass-through claims nothing from a pane.

       Browser-reserved Alt chords do not apply: this control only ever exists
       inside the native window, which has no menu bar and so no accesskeys. */
    const CHORD_CODE = 'KeyX';
    const CHORD_LABEL = 'Alt+X';

    function matchesChord(event) {
        if (!event || !event.altKey || event.ctrlKey || event.metaKey) return false;
        if (event.shiftKey || event.repeat) return false;
        return event.code === CHORD_CODE;
    }

    /* Native mode only, and not rendered rather than rendered-and-disabled: a
       browser tab cannot minimize its own window, and a dead button with a
       tooltip explaining itself is noise on a surface where every other
       control works. */
    function isAvailable(bridge) {
        return Boolean(bridge && typeof bridge.minimize_all_windows === 'function');
    }

    const policy = {
        BUTTON_ID,
        CHORD_CODE,
        CHORD_LABEL,
        matchesChord,
        isAvailable
    };

    function create(runtime) {
        const {
            getElement,
            getBridge,
            isBlockingTarget = () => false,
            logError = () => {}
        } = runtime || {};

        function bridge() {
            try {
                return getBridge ? getBridge() : null;
            } catch (_error) {
                return null;
            }
        }

        function available() {
            return isAvailable(bridge());
        }

        function syncControl() {
            const button = getElement ? getElement(BUTTON_ID) : null;
            if (!button) return false;
            const show = available();
            button.hidden = !show;
            return show;
        }

        function minimizeAll() {
            const api = bridge();
            if (!isAvailable(api)) return Promise.resolve(false);
            let result;
            try {
                result = api.minimize_all_windows();
            } catch (error) {
                logError('[GridVibe] minimize_all_windows failed:', error);
                return Promise.resolve(false);
            }
            return Promise.resolve(result).then(
                answer => Boolean(answer && answer.ok !== false),
                error => {
                    logError('[GridVibe] minimize_all_windows failed:', error);
                    return false;
                }
            );
        }

        function handleKeydown(event) {
            if (!matchesChord(event)) return false;
            if (isBlockingTarget(event.target)) return false;
            /* Asked after the chord matched, so a browser-mode page never
               swallows Alt+X from whatever else might want it. */
            if (!available()) return false;
            if (typeof event.preventDefault === 'function') event.preventDefault();
            minimizeAll();
            return true;
        }

        return { available, syncControl, minimizeAll, handleKeydown };
    }

    return { policy, create };
}));
