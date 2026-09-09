    /* ─────────────────────────────────────────────
       The shortcut reference — one button in the top bar, one read-only panel.

       There is no keybind registry in GridVibe: every chord is a hardcoded
       condition inside its own `keydown` handler, and nothing about that
       changes here. What was missing was never the ability to *move* a chord —
       the Alt family was chosen deliberately, one-handed, dead-key-free and
       stable across QWERTY and QWERTZ — it was a way to *see* them without
       opening README.md. So this is the seeing half and none of the machinery:
       no config section, no capture input, no conflict checker, no reserved-
       chord warnings, and not one handler rewritten.

       Consequences, stated rather than rediscovered:

         · **Nothing detects a conflict.** Two handlers matching one chord would
           both run. None do today, and this panel makes a new collision
           *visible* — two rows, one chord — which is most of what a checker
           would have caught.
         · **The `AltGr` guard and the `event.code` rule stay per-handler
           disciplines.** There is no single matcher to enforce them.
         · **The chord is named by what your layout prints.** The handlers match
           `event.code`; this panel prints a name, and on a QWERTZ layout those
           are not always the same letter. Every chord listed below was chosen
           so the two agree. A future chord that moves between layouts must be
           labelled from `event.key`, not from the `code` its handler matches.

       Split the way `session-menu.js` is: the array below is the list, the
       builder turns it into one `innerHTML` write, and the open/close pair is
       the only thing that touches the page. Each page keeps its own outside
       press and Escape; terminals.js keeps one rule more, which belongs to the
       bar rather than to the panel — a top bar that has left the flow takes the
       panel with it, or a panel anchored to a bar that is not there floats over
       the grid with nothing above it.

       **One list, both pages.** The launcher and the workspace window render
       the same partial, load the same module and print the same rows; the
       panel's shape is in the shared shortcuts-help.css and only the palette
       and the direction it opens are each page's own. So the list is *not*
       filtered to the page it is showing on: which window you are in is one
       more mode, and a row that quietly vanishes reads as a missing feature
       exactly as it does for the native-only and multi-workspace rows. The two
       rows that would otherwise be read wrongly from the other page say where
       they apply, in the same parenthetical every other mode-dependent row
       uses.

       **Mode-dependent rows are marked, never hidden.** Alt+X is native-window
       only and the workspace chords need more than one workspace open; a panel
       that filtered itself per mode would be a second place deciding what mode
       you are in, and a row that quietly vanishes reads as a missing feature. A
       parenthetical is honest and costs nothing.

       Loaded before terminals.js / launcher.js so their document listeners can
       call in.
    ───────────────────────────────────────────── */

    const SHORTCUTS_HELP_ROOT_ID = 'shortcutsHelpRoot';
    const SHORTCUTS_HELP_BUTTON_ID = 'shortcutsHelpBtn';
    const SHORTCUTS_HELP_PANEL_ID = 'shortcutsHelp';

    /* ── The list ──
       Grouped the way it is looked up, not the way it is implemented. A row is:

         chords      one or more chords, each an ordered list of key names; the
                     builder renders every name as its own <kbd>
         chordJoin   what sits between two chords — ' / ' for alternatives,
                     ' – ' for the ends of a range
         chordLabel  stands in for `chords` when a binding has no fixed chord to
                     print (the push-to-talk key is the user's own)
         action      what it does
         note        the parenthetical for a row that only applies in one mode

       Every entry that is a keyboard chord is also the source for README.md's
       table, which is pinned to this array by test_shortcuts_help.py — one
       hand-maintained list instead of two. */
    const SHORTCUT_HELP_GROUPS = [
        {
            title: 'Navigation',
            rows: [
                {
                    chords: [['Alt', '1'], ['Alt', '9']],
                    chordJoin: ' – ',
                    action: 'Switch session group'
                },
                {
                    chords: [['Alt', 'W'], ['Alt', 'Shift', 'W']],
                    action: 'Next / previous workspace window',
                    note: 'multiple workspaces only'
                },
                {
                    chords: [['Alt', 'W']],
                    action: 'Return to the workspace that opened the launcher',
                    note: 'on the launcher page'
                },
                {
                    chords: [['Alt', 'Q']],
                    action: 'Open the launcher',
                    /* The one row that names the launcher without being a
                       launcher chord — and this panel is on the launcher too. */
                    note: 'in a workspace window'
                },
                {
                    chords: [['Alt', 'A']],
                    action: 'Open the agent dashboard'
                }
            ]
        },
        {
            title: 'Terminal',
            rows: [
                {
                    chords: [['Ctrl', 'Shift', 'F']],
                    action: 'Search the scrollback'
                },
                {
                    chords: [['Ctrl', 'Shift', 'C']],
                    action: 'Copy the selection'
                },
                {
                    chords: [['Ctrl', 'V']],
                    action: 'Paste into the pane'
                },
                {
                    /* The one configurable binding in the app. This panel lists
                       it and does not own it: it is edited where it has always
                       been edited, and stored with the voice preferences. */
                    chords: [],
                    chordLabel: 'your push-to-talk chord',
                    action: 'Hold to dictate',
                    note: 'set in App Settings'
                }
            ]
        },
        {
            title: 'Explorer',
            rows: [
                {
                    chords: [['Ctrl', 'F']],
                    action: 'Find in the open file'
                },
                {
                    chords: [['Ctrl', 'Shift', 'F']],
                    action: 'Toggle repository search'
                },
                {
                    chords: [['Ctrl', 'Shift', 'V']],
                    action: 'Toggle the Markdown preview'
                },
                {
                    chords: [['F5']],
                    action: 'Refresh the focused explorer'
                },
                {
                    chords: [['Enter'], ['Shift', 'Enter'], ['↑'], ['↓']],
                    action: 'Step through find matches',
                    note: 'in any find bar'
                },
                {
                    chords: [['Esc']],
                    action: 'Drop the selection, or close the open menu'
                }
            ]
        },
        {
            title: 'Editor',
            rows: [
                {
                    chords: [['Ctrl', 'Shift', 'E']],
                    action: 'Edit the open file in place — and, while editing, cancel'
                },
                {
                    chords: [['Ctrl', 'S']],
                    action: 'Save'
                },
                {
                    chords: [['Tab']],
                    action: 'Indent'
                },
                {
                    chords: [['Esc']],
                    action: 'Cancel the edit'
                }
            ]
        },
        {
            title: 'Window',
            rows: [
                {
                    chords: [['Alt', 'X']],
                    action: 'Minimize every GridVibe window',
                    note: 'native window only'
                }
            ]
        },
        {
            /* Pointer gestures, so they sit apart rather than under a heading
               that calls them shortcuts — but they are listed, because an
               undocumented Alt+click is precisely what a reader opens this
               panel to find. */
            title: 'Mouse',
            rows: [
                {
                    chords: [['Alt', 'click']],
                    action: 'Fold the whole sibling level in the Files tree, or collapse every commit in the Git graph'
                },
                {
                    chords: [['Ctrl', 'click'], ['Shift', 'click']],
                    action: 'Extend the explorer selection'
                }
            ]
        }
    ];

    /* Every keyboard chord in the list, as a flat set of printable names.
       README.md's table is checked against this, so the two lists cannot
       drift apart in the one direction that matters — a chord that exists
       here and nowhere else. Pointer gestures and the chordless push-to-talk
       row are not chords and are excluded. */
    function shortcutHelpChordNames() {
        const names = [];
        SHORTCUT_HELP_GROUPS.forEach(group => {
            if (group.title === 'Mouse') {
                return;
            }
            group.rows.forEach(row => {
                (row.chords || []).forEach(chord => {
                    names.push(chord.join('+'));
                });
            });
        });
        return names;
    }

    /* ── Markup ── */

    function shortcutsHelpChordHtml(chord) {
        return chord
            .map(key => `<kbd class="shortcuts-help-key">${escHtml(key)}</kbd>`)
            .join('<span class="shortcuts-help-plus">+</span>');
    }

    function shortcutsHelpChordsHtml(row) {
        const chords = row.chords || [];
        if (!chords.length) {
            return `<span class="shortcuts-help-chord-label">${escHtml(row.chordLabel || '')}</span>`;
        }
        const join = `<span class="shortcuts-help-chord-join">${escHtml(row.chordJoin || ' / ')}</span>`;
        return chords.map(shortcutsHelpChordHtml).join(join);
    }

    function shortcutsHelpRowHtml(row) {
        const note = row.note
            ? ` <span class="shortcuts-help-note">(${escHtml(row.note)})</span>`
            : '';
        return `
            <div class="shortcuts-help-row">
                <div class="shortcuts-help-chord">${shortcutsHelpChordsHtml(row)}</div>
                <div class="shortcuts-help-action">${escHtml(row.action)}${note}</div>
            </div>
        `;
    }

    function shortcutsHelpGroupHtml(group) {
        return `
            <section class="shortcuts-help-group">
                <h3 class="shortcuts-help-group-title">${escHtml(group.title)}</h3>
                ${group.rows.map(shortcutsHelpRowHtml).join('')}
            </section>
        `;
    }

    function shortcutsHelpPanelHtml() {
        return SHORTCUT_HELP_GROUPS.map(shortcutsHelpGroupHtml).join('');
    }

    /* ── Fitting the panel to the room it actually has ──
       The stylesheet caps the panel at `min(62vh, 540px)`, which is a cap
       against the *window* and knows nothing about the box the panel is drawn
       in. On the launcher that box is `.column`, a scroll container — so a
       panel anchored above a button in the bottom action bar grew straight up
       through the top of the setup card and had its first group clipped away.
       A reference whose first entry cannot be reached is not one.

       So the height is measured against whatever actually clips the panel:
       every scrolling ancestor up to the viewport, intersected, because an
       inner clipper sits inside its outer ones and the tightest of them is the
       edge the panel meets first. The measurement only ever *tightens* the
       stylesheet's cap — the cap is a reading decision and this is a fitting
       one, and a panel allowed to grow past it because the window is tall
       would be a different surface.

       The arithmetic is separated from the measuring so it can be run without
       a page: `shortcutsHelpFittedHeight()` is the whole policy and takes
       numbers. */

    /* Matches the 6px the stylesheet's anchor offsets the panel by. */
    const SHORTCUTS_HELP_GAP = 6;
    /* Never flush against the edge it was about to be clipped by: a panel
       touching the boundary reads as one that is still cut off. */
    const SHORTCUTS_HELP_EDGE = 8;
    /* A floor, because a sliver is worse than a scrollbar. Below this the
       panel overflows its box again — deliberately, since at that point the
       window is too short for any answer to be a good one. */
    const SHORTCUTS_HELP_MIN_HEIGHT = 140;
    /* Leaving with the pointer closes the panel, but not on the first frame
       the cursor clips a corner on its way to the scrollbar. */
    const SHORTCUTS_HELP_LEAVE_GRACE_MS = 260;

    function shortcutsHelpFittedHeight(measurements) {
        const {
            opensUp,
            buttonTop,
            buttonBottom,
            boundsTop,
            boundsBottom,
            styleCap
        } = measurements;
        const room = opensUp
            ? buttonTop - boundsTop
            : boundsBottom - buttonBottom;
        const usable = room - SHORTCUTS_HELP_GAP - SHORTCUTS_HELP_EDGE;
        const capped = Number.isFinite(styleCap) && styleCap > 0
            ? Math.min(styleCap, usable)
            : usable;
        return Math.max(Math.round(capped), SHORTCUTS_HELP_MIN_HEIGHT);
    }

    /* The clip the panel is drawn inside: every ancestor that scrolls, down to
       the viewport. Intersected rather than stopping at the first one, so a
       scroller that is itself scrolled out of a shorter one cannot report more
       room than there is. */
    function shortcutsHelpClipBounds(root) {
        let top = 0;
        let bottom = window.innerHeight;
        let node = root.parentElement;
        while (node && node !== document.body && node !== document.documentElement) {
            if (window.getComputedStyle(node).overflowY !== 'visible') {
                const rect = node.getBoundingClientRect();
                top = Math.max(top, rect.top);
                bottom = Math.min(bottom, rect.bottom);
            }
            node = node.parentElement;
        }
        return { top, bottom };
    }

    function fitShortcutsHelpPanel(root, button, panel) {
        if (
            typeof window === 'undefined'
            || typeof window.getComputedStyle !== 'function'
            || typeof panel.getBoundingClientRect !== 'function'
        ) {
            return;
        }
        /* Cleared first, so the cap read back is the stylesheet's own and not
           what the previous open left behind. */
        panel.style.maxHeight = '';
        const buttonRect = button.getBoundingClientRect();
        const panelRect = panel.getBoundingClientRect();
        /* Which way it opened is the page's decision (--sh-anchor-*), so it is
           read off the result rather than asked for again here. */
        const opensUp = panelRect.bottom <= buttonRect.top + 1;
        const bounds = shortcutsHelpClipBounds(root);
        panel.style.maxHeight = `${shortcutsHelpFittedHeight({
            opensUp,
            buttonTop: buttonRect.top,
            buttonBottom: buttonRect.bottom,
            boundsTop: bounds.top,
            boundsBottom: bounds.bottom,
            styleCap: parseFloat(window.getComputedStyle(panel).maxHeight)
        })}px`;
    }

    /* ── Leaving closes it ──
       The panel is a reference you glance at, not a surface you operate, so the
       two ways of stopping looking at it close it: moving the pointer off it,
       and taking the window's focus somewhere else. Both are armed on open and
       taken off on close, because a listener that outlives its surface is the
       thing that closes the *next* panel unbidden.

       `mouseleave` on the root is the whole hover test: the panel is a child of
       the root, so the pointer moving from the button onto the panel never
       leaves it, and no separate "is it heading for the panel" rule is needed.
       Opening from the keyboard arms it too and costs nothing — a pointer that
       was never over the root cannot leave it. */

    let releaseShortcutsHelpDismissers = null;

    function armShortcutsHelpDismissers(root) {
        releaseShortcutsHelpDismissers?.();
        if (typeof root.addEventListener !== 'function') {
            return;
        }
        let graceHandle = null;
        const cancelGrace = () => {
            if (graceHandle !== null) {
                clearTimeout(graceHandle);
                graceHandle = null;
            }
        };
        const onLeave = () => {
            cancelGrace();
            graceHandle = setTimeout(() => {
                graceHandle = null;
                closeShortcutsHelp();
            }, SHORTCUTS_HELP_LEAVE_GRACE_MS);
        };
        const onBlur = () => closeShortcutsHelp();
        const onVisibility = () => {
            if (document.hidden) {
                closeShortcutsHelp();
            }
        };
        root.addEventListener('mouseleave', onLeave);
        root.addEventListener('mouseenter', cancelGrace);
        window?.addEventListener?.('blur', onBlur);
        document.addEventListener?.('visibilitychange', onVisibility);
        releaseShortcutsHelpDismissers = () => {
            cancelGrace();
            root.removeEventListener('mouseleave', onLeave);
            root.removeEventListener('mouseenter', cancelGrace);
            window?.removeEventListener?.('blur', onBlur);
            document.removeEventListener?.('visibilitychange', onVisibility);
            releaseShortcutsHelpDismissers = null;
        };
    }

    /* ── Open / close ──
       Not a modal: it changes nothing, so a backdrop and a focus trap would be
       worse than the thing they guard. Focus moves into the panel on open so
       the list can be scrolled and read from the keyboard, and comes back to
       the button on close — but only when the panel still had it, or a close
       triggered by a press somewhere else would yank focus out of whatever was
       just pressed. */

    function closeShortcutsHelp() {
        const root = document.getElementById(SHORTCUTS_HELP_ROOT_ID);
        if (!root || !root.classList.contains('open')) {
            return;
        }
        const button = document.getElementById(SHORTCUTS_HELP_BUTTON_ID);
        const panel = document.getElementById(SHORTCUTS_HELP_PANEL_ID);
        const hadFocus = Boolean(
            panel
            && document.activeElement
            && (panel === document.activeElement || panel.contains?.(document.activeElement))
        );
        releaseShortcutsHelpDismissers?.();
        root.classList.remove('open');
        button?.setAttribute('aria-expanded', 'false');
        /* The rows go with the panel. They are static, so rebuilding them on
           the next open costs nothing — and leaving thirty rows in the document
           for a surface that is closed almost all of the time buys nothing
           either. */
        if (panel) {
            panel.innerHTML = '';
            /* The fitted height belongs to the window the panel was opened in,
               so it goes with the rows rather than standing until the next
               open measures over it. */
            if (panel.style) {
                panel.style.maxHeight = '';
            }
        }
        if (hadFocus) {
            button?.focus?.();
        }
    }

    function toggleShortcutsHelp(event) {
        event?.preventDefault();
        event?.stopPropagation();
        const root = document.getElementById(SHORTCUTS_HELP_ROOT_ID);
        const button = document.getElementById(SHORTCUTS_HELP_BUTTON_ID);
        const panel = document.getElementById(SHORTCUTS_HELP_PANEL_ID);
        if (!root || !button || !panel) {
            return;
        }
        if (root.classList.contains('open')) {
            closeShortcutsHelp();
            return;
        }
        panel.innerHTML = shortcutsHelpPanelHtml();
        root.classList.add('open');
        button.setAttribute('aria-expanded', 'true');
        /* After the class, because a panel still `display: none` measures
           nothing, and before focus, so the list is already the size it will
           be read at when the caret lands in it. */
        fitShortcutsHelpPanel(root, button, panel);
        armShortcutsHelpDismissers(root);
        panel.focus?.();
    }
