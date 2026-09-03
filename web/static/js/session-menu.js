    /* ─────────────────────────────────────────────
       The session/workspace menu, seated in the session tab line.

       The Sessions… and Workspace… dropdowns used to live at the far left of
       the top bar, which is a surface that has to be *revealed* before it can
       be reached — and reaching them from a pane in the middle of the window
       meant traversing half the window along a 50px strip, losing the reveal on
       the way. This is that menu, moved one level down onto the session tab
       line, which is always in the flow: the strip the chevron leaves behind.

       One button, one panel, two sections.

         · Both sections start collapsed and pointing at one reveals it,
           closing the other — at most one open at a time. An expansion is a
           pointer gesture inside one opening of the menu: never workspace
           state, never persisted, dropped when the menu closes. That is what
           keeps it free of an invalidation hook anywhere.
         · **A section's items open beside the panel, never inside it.** Opening
           one in place pushed the other section's row down the panel, so
           swapping between the two moved the row being aimed at out from under
           the pointer. The two heads hold still because nothing ever opens
           between them: the panel holds exactly two rows for the whole life of
           one opening, and the items are a submenu anchored to the row that
           owns them (`.session-menu-section-body`, positioned by CSS).
         · **The pointer leaving the menu closes it**, after the same short
           grace the hidden top bar's reveal uses and for the same reason — the
           panel hangs 6px below the button, so a pointer travelling between
           them is briefly over neither and an ungraced close would fire on the
           way in. Focus is deliberately *not* a second retention input the way
           it is up there: opening the menu leaves focus on the button and
           pressing a section row leaves it on that row, both inside the root,
           so retaining on focus would mean the pointer-leave close almost never
           fired — which is the state this answers. Nothing is lost by leaving
           it out, because a keyboard-only pass never produces a pointer leave
           to begin with: the close is only ever reachable by a pointer that was
           inside.
         · **The row is the control, and the whole row.** `terminal-shell.js`
           makes the chevron a control *beside* the row because a shell row
           already has an action of its own, so one press cannot mean two
           things. These rows have no other action, so there is nothing to
           disambiguate — and a dead label sitting next to the one small
           control that opens it is the worse shape: it reads as a caption the
           pointer is somehow expected to aim past. So the head is an ordinary
           menu row that opens its own submenu — on hover the way a menu does,
           and on press and on keyboard focus so it is reachable without a
           pointer. Opening is idempotent: pointing at the row that is already
           open changes nothing, which is what stops the re-render firing under
           the pointer from flickering the section shut. The chevron stays as
           the marker that says a row has more behind it, `aria-hidden` and
           inside the row rather than beside it.

       The two dynamic workspace lists do not move: `refreshWorkspaceMenuLists()`
       still renders `#openWorkspaceList` and `#moveWorkspaceList` into their
       `.workspace-submenu` blocks, nested inside the Workspace section rather
       than growing chevrons of their own — two levels of disclosure is the
       reachability problem this menu exists to remove. The section is built on
       open, so that refresh is asked for when **Workspace** is expanded and not
       when the menu opens: a menu opened for `Save Session` should not cost a
       `fetchLiveWorkspaces()`.

       Split the way `terminal-shell.js` is: the registry below decides what the
       sections hold and what each row sends, the markup builder turns that into
       one `innerHTML` write, and one delegated click listener dispatches. The
       handlers themselves are terminals.js's own, unchanged, reached through
       SESSION_MENU_ACTIONS so nothing here has to know what a row does.

       Loaded before terminals.js so its document listeners can call in.
    ───────────────────────────────────────────── */

    const SESSION_MENU_ROOT_ID = 'sessionMenuRoot';
    const SESSION_MENU_BUTTON_ID = 'sessionMenuBtn';
    const SESSION_MENU_PANEL_ID = 'sessionMenu';

    /* The section whose items are showing, or '' for none. Reset on close. */
    let _expandedSessionMenuSection = '';

    /* Long enough to cross the 6px between the button and the panel, short
       enough that the menu is gone by the time the pointer has reached whatever
       it left for. The same number the top bar's reveal uses, for the same
       reason — kept as its own constant rather than read off that module,
       because these are two surfaces that happen to agree and not one setting.  */
    const SESSION_MENU_CLOSE_GRACE_MS = 320;
    let _sessionMenuCloseTimer = null;

    function isSessionMenuMultiWorkspace() {
        return typeof isMultiWorkspaceEnabled === 'function' && isMultiWorkspaceEnabled();
    }

    /* Save Workspace is unavailable — not busy — with nothing live to save.
       One predicate, read twice: as a render input when the section is built,
       and by `syncSessionMenuState()`'s paint while it is showing. */
    function sessionMenuHasLiveGroups() {
        return typeof sessionGroups !== 'undefined' && Array.isArray(sessionGroups) && sessionGroups.length > 0;
    }

    /* ── The registry ──
       Rows are data, so what a section holds can be read (and tested) without
       a DOM. Three kinds:
         · item  — one `.app-menu-item` that sends `action`
         · list  — a `.workspace-submenu` heading plus the container one of
                   terminals.js's dynamic lists is rendered into
         · block — a bordered group of items (the two close verbs) */

    function sessionMenuSessionRows() {
        return [
            { kind: 'item', id: '', label: 'Import Session ...', action: 'importSession' },
            { kind: 'item', id: 'saveSessionMenuItem', label: 'Save Session', action: 'saveSession' },
            { kind: 'item', id: 'saveSessionAsMenuItem', label: 'Save Session as ...', action: 'saveSessionAs' },
            { kind: 'item', id: 'saveAllSessionsMenuItem', label: 'Save All Sessions', action: 'saveAllSessions' }
        ];
    }

    function sessionMenuWorkspaceRows() {
        const rows = [
            {
                kind: 'item',
                id: 'saveWorkspaceItem',
                label: 'Save Workspace',
                action: 'saveWorkspace',
                disabled: !sessionMenuHasLiveGroups()
            }
        ];
        if (!isSessionMenuMultiWorkspace()) {
            return rows;
        }
        rows.push(
            { kind: 'item', id: 'renameWorkspaceItem', label: 'Rename Workspace ...', action: 'renameWorkspace' },
            { kind: 'item', id: 'newWorkspaceItem', label: 'New Workspace ...', action: 'newWorkspace' },
            {
                kind: 'list',
                title: 'Open Workspace',
                /* The shortcut is discoverable from the menu it replaces. */
                hint: 'Alt+W',
                listId: 'openWorkspaceList',
                listLabel: 'Open workspace'
            },
            {
                kind: 'list',
                title: 'Move Session to Workspace',
                /* Filled by setMoveWorkspaceScopeLabel(): the heading alone
                   does not say which session is about to move. */
                scopeId: 'moveWorkspaceScope',
                listId: 'moveWorkspaceList',
                listLabel: 'Move session to workspace'
            },
            {
                kind: 'block',
                items: [
                    {
                        kind: 'item',
                        id: 'closeWorkspaceWindowItem',
                        label: 'Close Workspace Window',
                        action: 'closeWorkspaceWindow'
                    },
                    {
                        kind: 'item',
                        id: 'closeWorkspaceItem',
                        label: 'Close Workspace',
                        action: 'closeWorkspace'
                    }
                ]
            }
        );
        return rows;
    }

    const SESSION_MENU_SECTIONS = [
        { key: 'sessions', label: 'Sessions', rows: sessionMenuSessionRows },
        { key: 'workspace', label: 'Workspace', rows: sessionMenuWorkspaceRows }
    ];

    /* What each row sends. Every handler is terminals.js's own and unchanged;
       naming them here is what keeps the registry free of the page. */
    const SESSION_MENU_ACTIONS = {
        importSession: (_button, event) => openNewSessionSelector(event),
        saveSession: button => saveActiveWorkspaceSession(button),
        saveSessionAs: button => saveActiveWorkspaceSessionAs(button),
        saveAllSessions: button => saveAllWorkspaceSessions(button),
        saveWorkspace: button => saveWorkspace(button),
        renameWorkspace: () => renameCurrentWorkspace(),
        newWorkspace: () => createAndOpenWorkspace(),
        closeWorkspaceWindow: () => closeThisWorkspaceWindow(),
        closeWorkspace: () => closeCurrentWorkspace()
    };

    /* ── Markup ── */

    function sessionMenuItemHtml(row) {
        return `
            <button
                type="button"
                class="app-menu-item"
                role="menuitem"
                ${row.id ? `id="${escHtml(row.id)}"` : ''}
                data-session-menu-action="${escHtml(row.action)}"
                ${row.disabled ? 'disabled' : ''}
            >${escHtml(row.label)}</button>
        `;
    }

    function sessionMenuListHtml(row) {
        return `
            <div class="workspace-submenu">
                <div class="workspace-submenu-title">
                    <span>${escHtml(row.title)}</span>
                    ${row.hint ? `<span class="workspace-submenu-hint">${escHtml(row.hint)}</span>` : ''}
                    ${row.scopeId ? `<span class="workspace-submenu-scope" id="${escHtml(row.scopeId)}" hidden></span>` : ''}
                </div>
                <div id="${escHtml(row.listId)}" role="group" aria-label="${escHtml(row.listLabel)}"></div>
            </div>
        `;
    }

    function sessionMenuRowHtml(row) {
        if (row.kind === 'list') {
            return sessionMenuListHtml(row);
        }
        if (row.kind === 'block') {
            return `<div class="workspace-submenu">${row.items.map(sessionMenuItemHtml).join('')}</div>`;
        }
        return sessionMenuItemHtml(row);
    }

    function sessionMenuSectionHtml(section) {
        const expanded = _expandedSessionMenuSection === section.key;
        return `
            <div class="session-menu-section">
                <button
                    type="button"
                    class="app-menu-item session-menu-section-head"
                    role="menuitem"
                    data-session-menu-section="${escHtml(section.key)}"
                    aria-haspopup="menu"
                    aria-expanded="${expanded ? 'true' : 'false'}"
                >
                    <span class="session-menu-section-label">${escHtml(section.label)}</span>
                    <span class="session-menu-section-caret" aria-hidden="true">${UI_CHEVRON_RIGHT_ICON}</span>
                </button>
                ${expanded ? `
                <div class="session-menu-section-body" role="menu" aria-label="${escHtml(section.label)}">
                    ${section.rows().map(sessionMenuRowHtml).join('')}
                </div>` : ''}
            </div>
        `;
    }

    function renderSessionMenu() {
        const panel = document.getElementById(SESSION_MENU_PANEL_ID);
        if (!panel) {
            return;
        }
        panel.innerHTML = SESSION_MENU_SECTIONS.map(sessionMenuSectionHtml).join('');
        /* The Workspace section's two lists are rendered by terminals.js, into
           containers this write has just replaced — so ask for them now, and
           only when that section is actually showing. */
        if (_expandedSessionMenuSection === 'workspace' && typeof refreshWorkspaceMenuLists === 'function') {
            refreshWorkspaceMenuLists();
        }
    }

    /* ── Open / close ── */

    function cancelSessionMenuClose() {
        if (_sessionMenuCloseTimer === null) {
            return;
        }
        clearTimeout(_sessionMenuCloseTimer);
        _sessionMenuCloseTimer = null;
    }

    /* Started when the pointer leaves, cancelled the moment it comes back —
       into the button, the panel or a section's flyout, all of which are inside
       the root the listener sits on. */
    function scheduleSessionMenuClose() {
        cancelSessionMenuClose();
        _sessionMenuCloseTimer = setTimeout(() => {
            _sessionMenuCloseTimer = null;
            closeSessionMenu();
        }, SESSION_MENU_CLOSE_GRACE_MS);
    }

    function closeSessionMenu() {
        /* Every close drops the pending one with it: a menu closed by Escape,
           an outside click or a row's own action and then reopened must not
           inherit a timer from the opening before it. */
        cancelSessionMenuClose();
        const root = document.getElementById(SESSION_MENU_ROOT_ID);
        const button = document.getElementById(SESSION_MENU_BUTTON_ID);
        root?.classList.remove('open');
        button?.setAttribute('aria-expanded', 'false');
        _expandedSessionMenuSection = '';
    }

    function toggleSessionMenu(event) {
        event?.preventDefault();
        event?.stopPropagation();
        const root = document.getElementById(SESSION_MENU_ROOT_ID);
        const button = document.getElementById(SESSION_MENU_BUTTON_ID);
        if (!root || !button) {
            return;
        }

        const shouldOpen = !root.classList.contains('open');
        if (!shouldOpen) {
            closeSessionMenu();
            return;
        }
        cancelSessionMenuClose();
        /* Nothing is expanded when the menu opens: a section's items are
           emitted only once that section has been pointed at. */
        _expandedSessionMenuSection = '';
        renderSessionMenu();
        root.classList.add('open');
        button.setAttribute('aria-expanded', 'true');
    }

    /* Keep an open Workspace section's Save Workspace row in step with a
       workspace that gained or lost its last session group while the menu was
       showing. A **paint**, never a re-render: this runs on every session-tab
       render, and rebuilding the panel would refetch the live workspace list
       each time — and take the row the pointer is on out from under it. The row
       is absent whenever the section is closed, and the same predicate is the
       render input that builds it next time. */
    function syncSessionMenuState() {
        const item = document.getElementById('saveWorkspaceItem');
        if (item) {
            item.disabled = !sessionMenuHasLiveGroups();
        }
    }

    /* Idempotent on purpose. Hover, press and focus all land here, and a render
       replaces the row the pointer is over — which the browser reports as a
       fresh pointer event on the new node. Re-opening what is already open has
       to cost nothing, or that echo would close the section the user is still
       pointing at. */
    function openSessionMenuSection(key) {
        if (!key || _expandedSessionMenuSection === key) {
            return;
        }
        const panel = document.getElementById(SESSION_MENU_PANEL_ID);
        /* The render replaces the row that asked, and focus goes to the body
           with it — so a keyboard open would leave Tab restarting at the top of
           the document. Hand focus back to the rebuilt row, and only when the
           panel had it: a hover must never take focus off whatever else has
           it. */
        const refocus = Boolean(
            panel
            && document.activeElement
            && typeof panel.contains === 'function'
            && panel.contains(document.activeElement)
        );
        _expandedSessionMenuSection = key;
        renderSessionMenu();
        if (refocus) {
            panel.querySelector(`[data-session-menu-section="${key}"]`)?.focus?.();
        }
    }

    /* ── Wiring ──
       Delegated, because the panel's rows are rebuilt on every open and on
       every expansion. `mouseover` and `focusin` rather than `mouseenter` and
       `focus`, because only the bubbling pair reaches a listener on the panel
       that outlives the rows it is listening for. */
    function sessionMenuSectionKeyFor(target) {
        if (!target || typeof target.closest !== 'function') {
            return '';
        }
        const head = target.closest('[data-session-menu-section]');
        return head ? head.dataset.sessionMenuSection : '';
    }

    function wireSessionMenu() {
        const panel = document.getElementById(SESSION_MENU_PANEL_ID);
        if (!panel) {
            return;
        }
        /* The pointer's own exit, on the root rather than the panel: the button
           and the panel are both inside it, and so is every section flyout, so
           travelling anywhere within the menu is not a leave. `mouseleave` and
           `mouseenter` and not their bubbling twins, precisely because these two
           answer for the whole subtree — the root outlives every row, so there
           is nothing here for delegation to buy. */
        const root = document.getElementById(SESSION_MENU_ROOT_ID);
        if (root) {
            root.addEventListener('mouseleave', () => {
                if (root.classList.contains('open')) {
                    scheduleSessionMenuClose();
                }
            });
            root.addEventListener('mouseenter', cancelSessionMenuClose);
        }
        /* Pointing at a section row opens it, the way a menu opens a submenu.
           Pointing at anything else changes nothing: an open section must not
           close because the pointer moved across into its own items. */
        panel.addEventListener('mouseover', event => {
            openSessionMenuSection(sessionMenuSectionKeyFor(event.target));
        });
        /* The keyboard's half of the same gesture — Tab reaches the row, and
           reaching it is what reveals what is behind it. */
        panel.addEventListener('focusin', event => {
            openSessionMenuSection(sessionMenuSectionKeyFor(event.target));
        });
        panel.addEventListener('click', event => {
            const target = event.target;
            if (!target || typeof target.closest !== 'function') {
                return;
            }
            /* A press on a section row opens it and nothing else: it has no
               action of its own, so there is nothing else it could mean. */
            const sectionKey = sessionMenuSectionKeyFor(target);
            if (sectionKey) {
                event.preventDefault();
                event.stopPropagation();
                openSessionMenuSection(sectionKey);
                return;
            }
            const item = target.closest('[data-session-menu-action]');
            if (!item || item.disabled) {
                return;
            }
            event.preventDefault();
            event.stopPropagation();
            const action = SESSION_MENU_ACTIONS[item.dataset.sessionMenuAction];
            if (!action) {
                return;
            }
            /* Closed first, exactly as every `onclick="closeXMenu(); …"` row
               did: the handlers open modals and write the session line, and a
               menu still standing over them is the state this replaces. */
            closeSessionMenu();
            action(item, event);
        });
    }
