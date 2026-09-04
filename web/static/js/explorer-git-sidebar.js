/* GridVibe Git sidebar — extracted from explorer-viewer.js by the
   move-only Git-domain split required by architecture guardrail 6.

   Owns Git status presentation, repository loading and quiet repaint,
   commit graph/search/active-row painting, and every sidebar Git action.
   The extracted functions below are byte-for-byte the implementations that
   stood in explorer-viewer.js; both files remain classic scripts sharing one
   global scope. Loaded directly after explorer-viewer.js. */

    function explorerGitRequestUrl(
        sessionId,
        endpoint,
        scopePath,
        extra = {},
        scopeKind = 'dir'
    ) {
        const params = new URLSearchParams();
        if (scopePath !== null && scopePath !== undefined) {
            params.set('scope', 'path');
            params.set('path', String(scopePath || ''));
            if (scopeKind === 'file') {
                params.set('kind', 'file');
            }
        }
        Object.entries(extra || {}).forEach(([key, value]) => {
            if (value !== null && value !== undefined && String(value) !== '') {
                params.set(key, String(value));
            }
        });
        const query = params.toString();
        return `/api/explorer/${encodeURIComponent(sessionId)}/git/${endpoint}${query ? `?${query}` : ''}`;
    }

    /* Where navigation alone has put the pane: the open file, or the folder
       the listing is showing. */
    function explorerGitDerivedBrowsingScope(pane) {
        if (pane?._explorerMode === 'file' && pane._explorerFilePath) {
            return { path: String(pane._explorerFilePath), kind: 'file' };
        }
        return { path: String(pane?._explorerPath || ''), kind: 'dir' };
    }

    /* What the pane is browsing, which is what both header controls are
       about: the derived scope above, unless the reader has singled out one
       row since it last moved (explorer-git-pin.js owns that rule). */
    function explorerGitBrowsingScope(pane) {
        const derived = explorerGitDerivedBrowsingScope(pane);
        return window.GridVibeExplorerGitPin.explorerGitBrowsedScope(
            derived.path, derived.kind, pane?._explorerGitBrowseTarget || null
        );
    }

    /* The one writer for that override. `null` gives the derived scope back.

       A gesture that leaves the browsing scope where it was costs nothing —
       not a paint and not a load — for the same reason navigation that leaves
       the scope alone does: loadExplorerGitRepo()'s own "already loaded"
       early return still re-renders the panel, and the panel carries the
       commit-message textarea. */
    function setExplorerGitBrowseTarget(index, path, kind = 'dir') {
        const pane = terminals[index];
        if (!pane) {
            return false;
        }
        const derived = explorerGitDerivedBrowsingScope(pane);
        const before = explorerGitBrowsingScope(pane);
        pane._explorerGitBrowseTarget = window.GridVibeExplorerGitPin
            .explorerGitBrowseOverride(path, kind, derived.path, derived.kind);
        const after = explorerGitBrowsingScope(pane);
        if (before.path === after.path && before.kind === after.kind) {
            return false;
        }
        refreshExplorerGitScopeAffordances(index);
        if (explorerGitScopeNeedsLoad(pane)) {
            loadExplorerGitRepo(index);
        }
        return true;
    }

    function explorerGitScopePath(pane) {
        if (pane?._explorerGitFollowBrowsing) {
            return explorerGitBrowsingScope(pane).path;
        }
        return typeof pane?._explorerGitPinnedPath === 'string'
            ? pane._explorerGitPinnedPath
            : null;
    }

    function explorerGitScopeKind(pane) {
        if (pane?._explorerGitFollowBrowsing) {
            return explorerGitBrowsingScope(pane).kind;
        }
        return typeof pane?._explorerGitPinnedPath === 'string'
            && pane._explorerGitPinKind === 'file'
            ? 'file'
            : 'dir';
    }

    /* A scope as a plain string: `null` (no pin, not following) and ''
       (pinned at the explorer root) both go out as the root and are therefore
       the same load identity, even though they are different pane states. */
    function explorerGitScopeIdentity(scopePath) {
        return scopePath === null || scopePath === undefined ? '' : String(scopePath);
    }

    function explorerGitRequestedScope(pane) {
        return explorerGitScopeIdentity(explorerGitScopePath(pane));
    }

    function explorerGitRequestedScopeKind(pane) {
        return explorerGitScopeKind(pane);
    }

    /* Two different questions, and answering both with one field is what made
       a pin look permanently unloaded.

       `_explorerGitAnchorPath` is the load identity: "which scope is the model
       on this pane the model *for*". It is compared against the scope the next
       load would request, so it has to be the scope that *was* requested.
       `data.anchor_path` is the server's answer — the resolved, root-relative
       spelling of that scope — and it is a different string whenever the
       request's spelling was not already canonical. Storing the answer and
       comparing it to the question meant such a pane never counted as loaded:
       every render refetched the whole repository, and the early return that
       exists to stop that never fired once.

       The server's answer is not kept at all. It was, briefly, on the grounds
       that the sidebar could show it -- but the sidebar names the scope from
       the pane's own pinned path (explorer-git-pin.js), so the field was
       written on every load and read by nothing (guardrail 5). The payload
       itself is still on the pane in `_explorerGitRepo` if a surface ever
       does want it. */
    function explorerGitNoteLoadedScope(
        pane,
        requestedScopePath,
        requestedScopeKind = 'dir'
    ) {
        if (!pane) {
            return;
        }
        pane._explorerGitAnchorPath = explorerGitScopeIdentity(requestedScopePath);
        pane._explorerGitAnchorKind = requestedScopeKind === 'file' ? 'file' : 'dir';
        /* The page is part of that identity, and it is read off the pane
           rather than passed in because every caller sets it before it asks:
           the model on the pane is the model for the page that was requested,
           whichever of the three request paths brought it back. */
        pane._explorerGitAnchorLimit = Number(pane._explorerGitCommitLimit) || 0;
    }

    /* How far back the Graph has been expanded, and the scope that expansion
       was made in.

       Two fields rather than one because "expanded to 180" is only meaningful
       beside "of this scope": the pin, Follow browsing and plain navigation
       under Follow all repoint the panel at another graph, and carrying a
       reader's expansion onto a scope they never expanded would silently make
       every folder they walk into a three-page read. A mutation deliberately
       does *not* clear it -- staging a file is not a new graph, and collapsing
       the reader's expansion under them for it would be the worse surprise.

       Runtime-only and per pane, like the commit find's query: it is a control
       position, not pane presentation, so nothing persists it. */
    function explorerGitCommitScopeKey(pane) {
        return `${explorerGitRequestedScope(pane)}
${explorerGitRequestedScopeKind(pane)}`;
    }

    function explorerGitDropStaleCommitLimit(pane) {
        if (!pane) {
            return;
        }
        if (pane._explorerGitCommitLimitScope !== explorerGitCommitScopeKey(pane)) {
            delete pane._explorerGitCommitLimit;
            delete pane._explorerGitCommitLimitScope;
        }
    }

    /* The page every request for this pane's repository summary carries --
       the reads and the mutations alike, because a stage that answered with
       the default page would collapse a graph the reader had expanded. An
       unexpanded pane sends nothing and takes the server's own default. */
    function explorerGitCommitLimitParams(pane) {
        const limit = Number(pane?._explorerGitCommitLimit);
        return Number.isFinite(limit) && limit > 0 ? { limit: String(limit) } : {};
    }

    /* Does the pane's selected scope still match the model the sidebar is
       showing? Every browsing surface asks this before calling for a load,
       because loadExplorerGitRepo()'s own "already loaded" early return is not
       free: it re-renders the Git panel, and the panel carries the
       commit-message textarea and the commit-search input, so a render nobody
       asked for takes the caret out of one of them. Navigation that leaves the
       scope where it was must therefore reach neither. */
    function explorerGitScopeNeedsLoad(pane) {
        if (!pane?._explorerGitSidebarOpen) {
            return false;
        }
        if (!pane._explorerGitRepoLoaded) {
            return true;
        }
        return pane._explorerGitAnchorPath !== explorerGitRequestedScope(pane)
            || (pane._explorerGitAnchorKind || 'dir') !== explorerGitRequestedScopeKind(pane);
    }

    function explorerGitStatusLabel(git) {
        if (!git || typeof git !== 'object') {
            return '';
        }
        const status = git.status || 'clean';
        if (status === 'clean' && git.has_descendant_changes) {
            return '*';
        }
        return EXPLORER_GIT_STATUS_LABELS[status] || '';
    }

    function explorerGitStatusTitle(git) {
        if (!git || typeof git !== 'object') {
            return 'Git status unavailable';
        }
        const status = git.status || 'clean';
        if (git.has_descendant_changes && git.descendant_status) {
            return `Directory contains ${git.descendant_status} Git changes`;
        }
        if (status === 'clean' && git.has_descendant_changes) {
            return 'Directory contains Git changes';
        }
        return `Git status: ${status}`;
    }

    function explorerGitBadgeHtml(git) {
        const label = explorerGitStatusLabel(git);
        const status = git?.status || 'clean';
        const className = label ? status : 'clean';
        return `<span class="explorer-git-badge ${escHtml(className)}" title="${escHtml(explorerGitStatusTitle(git))}">${escHtml(label)}</span>`;
    }

    function explorerHasGitDiff(git) {
        if (!git || typeof git !== 'object') {
            return false;
        }
        return ['modified', 'added', 'deleted', 'renamed', 'conflicted'].includes(git.status || '');
    }

    /* What stands where a branch name would, when there is no branch: the
       same sentence `git branch` prints for the state, in the same order of
       preference.

       A detached HEAD used to print its bare seven-character abbreviation
       here, which is indistinguishable from a branch actually named
       `3c2574d` -- so a checkout that landed on a commit rather than a branch
       (a tag, a remote-tracking ref, a submodule's recorded revision) read as
       the sidebar having lost the branch name. It now names what was checked
       out -- `Detached at origin/bfla_db_name` -- and falls back to the
       abbreviation only when nothing can be named, which is what Git does for
       a checkout of a raw id too.

       Both facts are the server's: `detached` is its reading of
       `branch.head (detached)` and `detached_ref` its reading of the HEAD
       reflog. Neither is re-derived here -- a status carrying no branch header
       at all is a third fact, and an older payload without the fields keeps
       exactly the label it always had. */
    function explorerGitHeadLabel(git) {
        const head = git.head ? String(git.head).slice(0, 7) : '';
        if (!git.detached) {
            return head || 'Git';
        }
        const ref = typeof git.detached_ref === 'string' ? git.detached_ref.trim() : '';
        if (ref) {
            /* `at` and `from` are the same two words `git branch` prints, and
               they are not decoration: `from` says HEAD has moved on since the
               checkout, which is the state where commits made here belong to
               no branch. Only an explicit `false` is `from`, so a payload that
               named a ref without answering the question still reads as `at`. */
            return `${git.detached_at === false ? 'Detached from' : 'Detached at'} ${ref}`;
        }
        return head ? `Detached at ${head}` : 'Detached HEAD';
    }

    /* The branch line's tooltip. It carries the abbreviated commit whenever the
       label spent its room on a ref name instead -- the id is what a detached
       HEAD is actually *at*, and losing it to the better label would be a
       trade, not an improvement. */
    function explorerGitBranchTitle(git) {
        const text = explorerGitBranchLabel(git);
        const head = git && git.detached && git.head ? String(git.head).slice(0, 7) : '';
        return head && !text.includes(head) ? `${text} (${head})` : text;
    }

    function explorerGitSummaryText(git) {
        if (!git || typeof git !== 'object') {
            return '';
        }
        if (!git.available) {
            return git.error ? 'Git unavailable' : 'No Git repo';
        }
        const parts = [git.branch || explorerGitHeadLabel(git)];
        if (Number(git.ahead || 0) > 0) {
            parts.push(`↑${git.ahead}`);
        }
        if (Number(git.behind || 0) > 0) {
            parts.push(`↓${git.behind}`);
        }
        if (git.dirty) {
            parts.push('*');
        }
        return parts.join(' ');
    }

    function explorerGitBranchLabel(git) {
        return explorerGitSummaryText(git) || 'Git';
    }

    /* What the repo bar says about the scope every action in this panel acts
       on. An unpinned, non-following pane is scoped to the explorer root by
       default and gets no word for it — naming the default on every pane is
       noise. A pin made *at* the root does get one, because a root pin and no
       pin ask the server for exactly the same thing: without a word for it,
       pinning at the root round-trips perfectly and still reads as though the
       pin had been lost. */
    function explorerGitScopeLabel(scopePath, scopeKind = 'dir') {
        return window.GridVibeExplorerGitPin.explorerGitPinLabel(scopePath, scopeKind);
    }

    /* The same scope spelled in full, for the places a title or a tooltip can
       carry the whole path: a file label is only its leaf, and two files with
       one name in different folders are otherwise one word for two scopes. */
    function explorerGitScopePathLabel(scopePath) {
        return window.GridVibeExplorerGitPin.explorerGitPinPathLabel(scopePath);
    }

    /* The seven characters a commit row shows, and the full id hash mode
       searches. Written once because the render and the search paint both
       build the same row: they spelled the fallback differently, so a payload
       carrying only one of the two fields would have shown one thing and then
       repainted as another. */
    function explorerGitCommitShortHash(commit) {
        return String(commit?.hash || commit?.full_hash || '').slice(0, 7);
    }

    function explorerGitCommitSearchableHash(commit) {
        return String(commit?.full_hash || commit?.hash || '');
    }

    /* The commit row's card, painted from GridVibeExplorerGitGraph's DOM-free
       model and opened by the row's own right-click.

       It replaces a native `title`, which could neither be styled nor hold
       more than the one line the row was already showing. It used to appear
       on hover, on a delay, while a separate context menu on the same row
       offered the two copy entries -- one row answering to two gestures, and
       the more useful of the two arriving uninvited while the reader was
       merely scanning subjects. The right-click now opens the card, and the
       copy entries are controls *inside* it.

       That makes it a surface the reader operates rather than one they only
       read, and three things follow.

       It cannot live inside the row button -- a button inside a button is not
       a control anybody can click. And it cannot live inside the Git panel
       either, which is where it used to hang: that panel is a scroller, so
       anything laid out in it is capped at the sidebar's width, and the full
       forty-character object id -- the one field a reader comes to this card
       to copy -- wrapped onto two lines in any sidebar narrower than about
       360px. Widening it in place would have handed the whole graph a
       horizontal scrollbar instead. So it goes on `document.body` and is
       positioned in viewport coordinates, exactly as the context menu it
       replaces does, and is sized by its own content: it is never narrower
       than the row it hangs off and never wider than the ceiling in
       terminals.css.

       Living outside the panel is what makes the *dismissals* load-bearing
       rather than incidental. A card laid out inside the panel died with the
       panel's innerHTML and moved with its scroll; this one does neither, so
       a re-render, a scroll of the list under it, a group switch and a pane
       release each have to say so.

       It is also no longer aria-hidden: the row button keeps carrying the same
       facts as its accessible name, because the card is only on screen while
       the reader keeps it there.

       Nothing is emitted per row any more. A scope may carry three hundred
       commits, and rendering three hundred cards to show at most one is the
       cost the old markup paid on every repaint. */
    let _explorerGitCommitCard = null;
    let _explorerGitCommitCardRow = null;
    let _explorerGitCommitCardPane = null;

    /* Which pane the open card belongs to, for the three callers that have
       to tell "mine" from "somebody else's": the panel about to rewrite its
       own innerHTML, the group being cached off screen, and the pane being
       discarded. The card is bound to the pane object rather than to its grid
       slot or its panel id, for the reason every post-await write in this
       file is. */
    function explorerGitCommitCardPane() {
        return _explorerGitCommitCardPane;
    }

    /* One copy control. The slot is emitted whether or not it holds a button:
       the card's rows are `display: contents` over a three-column grid, so a
       row that contributed two cells instead of three would slide every row
       below it one column across. */
    function explorerGitCommitCopyHtml(action) {
        if (!action) {
            return '<span class="explorer-git-commit-card-copy-slot"></span>';
        }
        return `<span class="explorer-git-commit-card-copy-slot"><button type="button" class="explorer-search-btn explorer-git-commit-card-copy" data-explorer-git-commit-copy="${escHtml(action.key)}"${action.disabled ? ' disabled' : ''} title="${escHtml(action.title)}" aria-label="${escHtml(action.label)}">${UI_COPY_ICON}</button></span>`;
    }

    function explorerGitCommitCardHtml(card, actions) {
        if (!card) {
            return '';
        }
        const copy = actions || {};
        const rows = (card.rows || []).map(row => `
                <span class="explorer-git-commit-card-row">
                    <span class="explorer-git-commit-card-label">${escHtml(row.label)}</span>
                    <span class="explorer-git-commit-card-value${row.mono ? ' is-mono' : ''}">${escHtml(row.value)}</span>
                    ${explorerGitCommitCopyHtml(row.copy ? copy[row.copy] : null)}
                </span>`).join('');
        /* One at a time, so it is addressable by id -- which is what lets the
           change listener ask "is a floating surface open over a row?" with a
           getElementById on every poll rather than a class query over the
           document. */
        return `
            <span class="explorer-git-commit-card" id="explorer-git-commit-card" role="group" aria-label="Commit details" tabindex="-1">
                <span class="explorer-git-commit-card-head">
                    <span class="explorer-git-commit-card-message">${escHtml(card.message)}</span>
                    ${explorerGitCommitCopyHtml(copy.message)}
                </span>
                ${rows ? `<span class="explorer-git-commit-card-rows">${rows}</span>` : ''}
                <span class="explorer-git-commit-card-hint">${escHtml(card.hint)}</span>
            </span>`;
    }

    /* Where the card goes, in the viewport's own coordinates.

       Two answers, and only one of them is a decision. Which *side* of the row
       it opens on is GridVibeExplorerGitGraph.cardPlacement()'s, still
       measured against the Git panel and not the window: the card may be wider
       than the sidebar, but there is no reason for it to leave the sidebar
       vertically, and a card opened from one of the last rows would otherwise
       run off the bottom of the pane. The horizontal position has no decision
       in it at all -- the row's own left edge, pulled back inside the window
       if a wide card would hang off the right of it.

       The floor is written first because it is one of the things that decides
       how tall the card is, and the side is chosen on its height. It is the
       row's width, so a commit with little to report still reads as a card
       belonging to that row rather than a small box beside it; `max-content`
       and the CSS ceiling settle everything above the floor. The card is
       `visibility: hidden` until all of this is written, not `display: none`,
       precisely so it has a box to measure. */
    function applyExplorerGitCommitCardPlacement(card, row) {
        const policy = window.GridVibeExplorerGitGraph;
        const panel = row?.closest('.explorer-git-panel');
        if (!policy || !card || !panel) {
            return;
        }
        const rowBox = row.getBoundingClientRect();
        card.style.minWidth = `${Math.round(rowBox.width)}px`;
        const cardBox = card.getBoundingClientRect();
        const panelBox = panel.getBoundingClientRect();
        const placement = policy.cardPlacement({
            rowTop: rowBox.top,
            rowBottom: rowBox.bottom,
            viewTop: panelBox.top,
            viewBottom: panelBox.bottom,
            cardHeight: cardBox.height,
            // The 2px the card overlaps its row by, in both directions.
            gap: 2
        });
        const rightmost = Math.max(8, window.innerWidth - cardBox.width - 8);
        card.style.left = `${Math.round(Math.max(8, Math.min(rowBox.left, rightmost)))}px`;
        card.style.top = placement === 'above'
            ? `${Math.round(rowBox.top - cardBox.height + 2)}px`
            : `${Math.round(rowBox.bottom - 2)}px`;
    }

    /* Dismissal is the context menu's, because the card is now the same kind
       of thing: one at a time, Escape and an outside press close it, and the
       gesture hands focus back to the row that opened it.

       What is *not* the context menu's is the third listener. The menu names
       no row, so nothing about the page moving underneath it makes it wrong;
       this card is pinned to one, and a scroll of the list that row is in
       leaves it pointing at a row that has moved out from under it. Only a
       scroll that actually contains the row: the pane has several scrollers,
       and taking the card away when one of the others moves would be a
       disappearance the reader cannot account for.

       The row, not the card, is what every handler tests for staleness. The
       card is on `document.body` and so stays connected whatever happens to
       the panel; the row leaving the document is what says this card is about
       a pane the reader can no longer see. */
    function dismissExplorerGitCommitCard(options) {
        const { restoreFocus = true } = options || {};
        document.removeEventListener('keydown', _explorerGitCommitCardKeydown, true);
        document.removeEventListener('mousedown', _explorerGitCommitCardOutside, true);
        document.removeEventListener('scroll', _explorerGitCommitCardScroll, true);
        _explorerGitCommitCard?.remove();
        const row = _explorerGitCommitCardRow;
        _explorerGitCommitCard = null;
        _explorerGitCommitCardRow = null;
        _explorerGitCommitCardPane = null;
        row?.classList.remove('is-card-open');
        if (restoreFocus && row?.isConnected) {
            row.focus({ preventScroll: true });
        }
    }

    function _explorerGitCommitCardStale() {
        if (_explorerGitCommitCard && _explorerGitCommitCardRow?.isConnected) {
            return false;
        }
        dismissExplorerGitCommitCard({ restoreFocus: false });
        return true;
    }

    function _explorerGitCommitCardOutside(event) {
        if (_explorerGitCommitCardStale()) {
            return;
        }
        if (_explorerGitCommitCard.contains(event.target)) {
            return;
        }
        /* A secondary press on the card's *own* row is the first half of the
           right-click that toggles it, and the contextmenu event carrying the
           second half has not been delivered yet. Dismissing here would take
           the card away and let that second half build it straight back, so
           the gesture the reader made would never close anything. Every other
           press dismisses, this row's primary button included. */
        if (event.button === 2 && _explorerGitCommitCardRow.contains(event.target)) {
            return;
        }
        dismissExplorerGitCommitCard({ restoreFocus: false });
    }

    function _explorerGitCommitCardKeydown(event) {
        if (_explorerGitCommitCardStale()) {
            return;
        }
        if (event.key === 'Escape') {
            event.preventDefault();
            dismissExplorerGitCommitCard();
        }
    }

    function _explorerGitCommitCardScroll(event) {
        if (_explorerGitCommitCardStale()) {
            return;
        }
        if (event.target?.contains?.(_explorerGitCommitCardRow)) {
            dismissExplorerGitCommitCard({ restoreFocus: false });
        }
    }

    /* Open the card for one commit row.

       The facts come from the loaded model, matched on the row's own hash --
       the row carries only the three values the copy controls need, and the
       author, date and refs the card exists to show are in the payload the
       sidebar already fetched. A row whose commit is no longer in that model
       still gets a card built from what the row itself carries, rather than a
       gesture that silently does nothing. */
    function openExplorerGitCommitCard(index, row) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        const policy = window.GridVibeExplorerGitGraph;
        if (!pane || !panel || !row || !policy || !panel.contains(row)) {
            return;
        }
        /* The gesture toggles on the row it names: right-clicking the row
           whose card is already open closes it, so the reader puts it away
           with the same press rather than having to find empty space to click
           in. A different row still replaces the card, and the focus goes back
           to the row exactly as Escape's dismissal leaves it. */
        if (_explorerGitCommitCardRow === row) {
            dismissExplorerGitCommitCard();
            return;
        }
        dismissExplorerGitCommitCard({ restoreFocus: false });
        const hash = row.dataset.explorerGitCommitToggle || '';
        const loaded = (pane._explorerGitRepo?.commits || [])
            .find(commit => String(commit.hash || '') === hash);
        const record = loaded || {
            hash,
            full_hash: row.dataset.explorerGitCommitFull || '',
            message: row.dataset.explorerGitCommitMessage || ''
        };
        const expanded = Boolean(hash && ensureExplorerDiffExpandedCommits(pane).has(
            window.GridVibeExplorerGitActive.commitKey(hash)
        ));
        const actions = window.GridVibeExplorerGitMenu.commitCopyActions({
            hash: String(record.hash || ''),
            fullHash: String(record.full_hash || ''),
            message: String(record.message || '')
        }, _copyText);
        document.body.insertAdjacentHTML('beforeend', explorerGitCommitCardHtml(
            policy.commitCard(record, { expanded, now: Date.now() }),
            actions
        ));
        const card = document.getElementById('explorer-git-commit-card');
        if (!card) {
            return;
        }
        /* The palette follows the *pane*, not the app. A pane can be toggled
           to the opposite theme, and its own `data-explorer-theme` block is
           what carries --explorer-float-border and the rest; on document.body
           the card is outside that block, so it wears the pane's theme
           explicitly or it comes out in the other one. */
        const paneTheme = row.closest('.explorer-pane')?.dataset.explorerTheme;
        if (paneTheme) {
            card.dataset.explorerTheme = paneTheme;
        }
        _explorerGitCommitCard = card;
        _explorerGitCommitCardRow = row;
        _explorerGitCommitCardPane = pane;
        row.classList.add('is-card-open');
        card.querySelectorAll('[data-explorer-git-commit-copy]').forEach(button => {
            button.addEventListener('click', () => {
                actions[button.dataset.explorerGitCommitCopy]?.action();
                /* Copy and close, the way the menu entry this replaces did:
                   the surface going away is the only acknowledgement a
                   clipboard write gets. */
                dismissExplorerGitCommitCard();
            });
        });
        applyExplorerGitCommitCardPlacement(card, row);
        card.classList.add('is-open');
        const first = card.querySelector('button:not(:disabled)');
        (first || card).focus({ preventScroll: true });
        window.setTimeout(() => {
            document.addEventListener('mousedown', _explorerGitCommitCardOutside, true);
        }, 0);
        document.addEventListener('keydown', _explorerGitCommitCardKeydown, true);
        document.addEventListener('scroll', _explorerGitCommitCardScroll, true);
    }

    /* The Graph's "Show more", and the two wordless states that replace it.

       Rendered as part of the panel like every other row: it is not sticky and
       not a floating affordance, because it belongs to the end of the list and
       is only reachable by having scrolled there. The chevron points down for
       the same reason the commit rows' does -- there is more below. */
    function explorerGitGraphMoreHtml(plan) {
        if (!plan || !plan.visible) {
            return '';
        }
        const button = plan.label
            ? `<button type="button" class="explorer-git-graph-more-btn" data-explorer-git-show-more ${plan.canLoadMore ? '' : 'disabled'} title="Read further back in this scope's history" aria-label="Show more commits">
                    <span class="explorer-git-graph-more-label">${escHtml(plan.label)}</span>
                    <span class="explorer-git-graph-more-chevron" aria-hidden="true">${UI_CHEVRON_DOWN_ICON}</span>
                </button>`
            : '';
        const detail = plan.detail
            ? `<span class="explorer-git-graph-more-detail" role="status">${escHtml(plan.detail)}</span>`
            : '';
        return `<div class="explorer-git-graph-more${plan.atCeiling ? ' is-at-ceiling' : ''}">${button}${detail}</div>`;
    }

    /* The follow button's title names the scope it would take, because that
       scope is no longer always "the browsed folder": a highlighted row and
       the listing's own folder are both browsing acts, and one of them can be
       a file. */
    function explorerGitFollowButtonTitle(pane, following) {
        if (following) {
            return 'Use fixed Git scope';
        }
        const target = explorerGitBrowsingScope(pane);
        const pin = window.GridVibeExplorerGitPin;
        return `Follow the browsed ${target.kind === 'file' ? 'file' : 'folder'} for Git: ${pin.explorerGitPinPathLabel(target.path)}`;
    }

    /* The pin button's three states, read from the module the Files tree's
       marker is painted from (explorer-git-pin.js) so the pressed button and
       the marked row cannot disagree. Reading the pane here rather than in
       the module keeps the module free of pane shape: what it is handed is a
       pinned path and a browsed path.

       Delegated outright, with no local fallback: a second copy of the state
       table -- or of the word for the root -- is exactly the disagreement the
       module exists to prevent, and it is a page script loaded before this
       one. */
    function explorerGitPinState(pane) {
        const target = explorerGitBrowsingScope(pane);
        return window.GridVibeExplorerGitPin.explorerGitPinButtonState(
            typeof pane?._explorerGitPinnedPath === 'string' ? pane._explorerGitPinnedPath : null,
            target.path,
            pane?._explorerGitPinKind === 'file' ? 'file' : 'dir',
            target.kind
        );
    }

    function updateExplorerGitSummary(index, git) {
        const summary = document.getElementById(`explorer-git-${index}`);
        if (!summary) {
            return;
        }
        const text = explorerGitSummaryText(git);
        summary.textContent = text;
        summary.title = git?.error || (git?.repo_root || text);
    }

    function explorerDiffCacheKey(path, commit, mode = '') {
        return `${String(path || '')}\n${String(commit || '')}\n${String(mode || '')}`;
    }

    function explorerDiffSidebarStatusHtml(git) {
        return explorerGitBadgeHtml(git || { status: 'clean' });
    }

    function explorerParentDirectory(path) {
        const cleaned = String(path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        const slashIndex = cleaned.lastIndexOf('/');
        return slashIndex > 0 ? cleaned.slice(0, slashIndex) : '';
    }

    function explorerGitOpenFile(index, path, diffMode = 'worktree') {
        if (!path) {
            return;
        }
        // Changed-file rows jump straight to the diff view (ISSUE-2026-023).
        // 'worktree' shows unstaged hunks, 'staged' shows the indexed hunks, so
        // a partially staged file never surfaces the other section's changes.
        const mode = diffMode === 'staged' ? 'staged' : 'worktree';
        openExplorerFile(index, path, { openDiff: true, diffMode: mode });
    }

    async function explorerGitOpenCommitDiff(index, path, commit) {
        if (!path || !commit) {
            return;
        }
        const opened = await openExplorerFile(index, path, {
            showLoading: true,
            openDiff: true,
            diffCommit: commit
        });
        if (!opened) {
            renderExplorerCommitDiffFile(index, path, commit);
        }
    }

    function explorerGitOpenFolder(index, path) {
        loadExplorerPane(index, explorerParentDirectory(path));
    }

    function explorerGitGraphLane(character, position) {
        // `git log --graph` gives each branch a two-character column, and a diagonal
        // always belongs to the column it is reaching towards, not the one it starts in.
        const column = character === '/' || character === '\\' ? (position + 1) / 2 : position / 2;
        return Math.floor(column) % EXPLORER_GIT_GRAPH_LANE_COUNT;
    }

    function explorerGitGraphHtml(graph) {
        const characters = Array.from(typeof graph === 'string' && graph ? graph : '*');
        return characters.map((character, position) => {
            if (character === ' ') {
                return ' ';
            }
            const lane = explorerGitGraphLane(character, position);
            const nodeClass = character === '*' ? ' node' : '';
            return `<span class="explorer-diff-commit-graph-lane${nodeClass}" data-git-lane="${lane}">${escHtml(character)}</span>`;
        }).join('');
    }

    function explorerGitStatusFromCode(code) {
        switch (code) {
            case 'M':
            case 'T':
                return 'modified';
            case 'A':
                return 'added';
            case 'D':
                return 'deleted';
            case 'R':
                return 'renamed';
            case 'C':
                return 'added';
            case 'U':
                return 'conflicted';
            case '?':
                return 'untracked';
            case '!':
                return 'ignored';
            default:
                return 'clean';
        }
    }

    function explorerGitCodeUnmodified(code) {
        // Porcelain v2 uses '.' for an unchanged index/worktree position; clean rows use ' '.
        return !code || code === ' ' || code === '.';
    }

    function splitExplorerGitChanges(changes) {
        const staged = [];
        const unstaged = [];
        (Array.isArray(changes) ? changes : []).forEach(file => {
            const git = file.git || {};
            const indexCode = git.index_status || ' ';
            const worktreeCode = git.worktree_status || ' ';
            if (git.status === 'conflicted') {
                unstaged.push({ ...file, git: { ...git, status: 'conflicted' } });
                return;
            }
            if (git.status === 'untracked' || indexCode === '?') {
                unstaged.push({ ...file, git: { ...git, status: 'untracked' } });
                return;
            }
            if (!explorerGitCodeUnmodified(indexCode)) {
                staged.push({ ...file, git: { ...git, status: explorerGitStatusFromCode(indexCode) } });
            }
            if (!explorerGitCodeUnmodified(worktreeCode)) {
                unstaged.push({ ...file, git: { ...git, status: explorerGitStatusFromCode(worktreeCode) } });
            }
        });
        return { staged, unstaged };
    }

    function explorerGitCanRevert(status) {
        // Tracked changes are restored from the index. A single explicitly
        // selected untracked file can also be discarded after confirmation.
        return ['modified', 'deleted', 'renamed', 'untracked'].includes(status || '');
    }

    function explorerGitCanBulkDiscard(status) {
        // Bulk discard deliberately keeps untracked files. Removing a new file
        // requires the narrower per-row action and its explicit warning.
        return ['modified', 'deleted', 'renamed'].includes(status || '');
    }

    function explorerGitFileLabelHtml(path, fallbackName) {
        /* Change rows lead with the file name and trail the muted directory, so
           the file being changed stays legible at the sidebar's narrow width.
           The directory is the part allowed to ellipsis away. */
        const cleaned = String(path || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
        const slashIndex = cleaned.lastIndexOf('/');
        const name = (slashIndex >= 0 ? cleaned.slice(slashIndex + 1) : cleaned)
            || fallbackName
            || 'Changed file';
        const directory = slashIndex > 0 ? cleaned.slice(0, slashIndex) : '';
        const directoryHtml = directory
            ? `<span class="explorer-diff-commit-file-dir">${escHtml(directory)}</span>`
            : '';
        return `<span class="explorer-diff-commit-file-name">${escHtml(name)}</span>${directoryHtml}`;
    }

    function renderExplorerGitFileRows(index, files, options = {}) {
        const entries = Array.isArray(files) ? files : [];
        if (!entries.length) {
            return `<div class="explorer-diff-sidebar-empty">${escHtml(options.emptyText || 'No files.')}</div>`;
        }
        const commitHash = options.commitHash || '';
        const action = options.action || '';
        // Staged rows diff against HEAD (index hunks); everything else shows the
        // worktree hunks so a partially staged file never leaks the wrong side.
        const diffMode = action === 'unstage' ? 'staged' : 'worktree';
        return entries.map(file => {
            const path = file.path || file.repo_path || '';
            const status = (file.git && file.git.status) || '';
            const pathAction = commitHash
                ? `data-explorer-git-open-commit-diff="${escHtml(path)}" data-explorer-git-commit="${escHtml(commitHash)}"`
                : `data-explorer-git-open-file="${escHtml(path)}" data-explorer-git-diff-mode="${escHtml(diffMode)}"`;
            /* Stage/unstage draw their plus and minus with the shared
               UI_PLUS_ICON / UI_MINUS_ICON — the same pair the search panel's
               expand/collapse and the browser pane's new tab already use —
               rather than the `+`/`−` text they used to carry beside the SVG
               revert and open-folder buttons on the same row. A glyph centres
               itself by `font-size` and an SVG does not, so both buttons (and
               the editor's zoom pair, which made the same move) carry flex
               centring and an explicit icon box in terminals.css. */
            let actionButton = '';
            if (action === 'stage') {
                actionButton = `<button type="button" class="explorer-search-btn explorer-git-stage-btn" data-explorer-git-stage="${escHtml(path)}" title="Stage changes" aria-label="Stage changes">${UI_PLUS_ICON}</button>`;
            } else if (action === 'unstage') {
                actionButton = `<button type="button" class="explorer-search-btn explorer-git-unstage-btn" data-explorer-git-unstage="${escHtml(path)}" title="Unstage changes" aria-label="Unstage changes">${UI_MINUS_ICON}</button>`;
            }
            const discardLabel = status === 'untracked'
                ? 'Delete untracked file'
                : 'Discard changes (revert)';
            const revertButton = (action === 'stage' && explorerGitCanRevert(status))
                ? `<button type="button" class="explorer-search-btn explorer-git-revert-btn" data-explorer-git-revert="${escHtml(path)}" data-explorer-git-revert-status="${escHtml(status)}" title="${discardLabel}" aria-label="${discardLabel}">${EXPLORER_GIT_REVERT_ICON}</button>`
                : '';
            /* Download reads the worktree, so it is only offered where the
               worktree copy is the file the row names: not for deleted files
               and not for history rows (those show a past commit's version). */
            const downloadPath = (path && !commitHash && status !== 'deleted')
                ? ` data-explorer-download-path="${escHtml(path)}"`
                : '';
            /* The row's own diff identity, so the active-row paint can tell
               this row from the other three that name the same path without
               reading the action attributes off the button inside it. */
            const rowIdentity = ` data-explorer-git-row-commit="${escHtml(commitHash)}" data-explorer-git-row-mode="${escHtml(commitHash ? '' : diffMode)}"`;
            return `
                <div class="explorer-diff-commit-file" title="${escHtml(path)}" data-explorer-copy-path="${escHtml(path)}"${downloadPath}${rowIdentity}>
                    ${explorerFileTypeIconHtml(path)}
                    <button type="button" class="explorer-diff-commit-file-path" ${pathAction}>${explorerGitFileLabelHtml(path, file.name)}</button>
                    ${explorerDiffSidebarStatusHtml(file.git)}
                    <span class="explorer-diff-commit-file-actions">
                        ${revertButton}
                        ${actionButton}
                        <button type="button" class="explorer-search-btn explorer-open-folder-btn" data-explorer-git-open-folder="${escHtml(path)}" title="Open containing folder" aria-label="Open containing folder">${EXPLORER_OPEN_FOLDER_ICON}</button>
                    </span>
                </div>
            `;
        }).join('');
    }

    /* ─────────────────────────────────────────────
       Explorer copy-path context menu (ISSUE-2026-028)
       Delegated on the tree + Git panels so right-clicking any file row offers
       an in-page (WebView2-safe) Copy path / Copy relative path menu. Copy is a
       read, so this stays inside the read-only explorer contract.
    ───────────────────────────────────────────── */
    function ensureExplorerGitCommitSearchState(pane) {
        if (!pane._explorerGitCommitSearch) {
            pane._explorerGitCommitSearch = {
                query: '', activeIndex: 0, open: false, mode: 'subject'
            };
        }
        return pane._explorerGitCommitSearch;
    }

    function explorerGitCommitSearchCountText(state, plan) {
        if (!state.query) {
            return '';
        }
        if (plan.emptyText) {
            return '';
        }
        return `${plan.matchCount ? plan.activeIndex + 1 : 0}/${plan.matchCount}`;
    }

    function explorerGitCommitMessageFocusState(index) {
        const input = document.getElementById(`explorer-git-commit-message-${index}`);
        if (!input || document.activeElement !== input) {
            return null;
        }
        return {
            start: typeof input.selectionStart === 'number' ? input.selectionStart : 0,
            end: typeof input.selectionEnd === 'number' ? input.selectionEnd : 0
        };
    }

    function restoreExplorerGitCommitMessageFocus(index, state) {
        if (!state) {
            return;
        }
        const input = document.getElementById(`explorer-git-commit-message-${index}`);
        if (!input) {
            return;
        }
        input.focus();
        try {
            input.setSelectionRange(state.start, state.end);
        } catch (error) {
            // A textarea supports selection, but focus restoration must stay
            // harmless if a test double or browser implementation does not.
        }
    }

    /* Paint only, for the same reason as paintExplorerGitActiveRows below:
       the panel carries the commit-message textarea and the search input
       itself, so a keystroke must repaint the subject spans in place rather
       than re-render the panel and take the caret with it. */
    function paintExplorerGitCommitSearch(index, { scroll = false } = {}) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        const policy = window.GridVibeExplorerGitSearch;
        if (!pane || !panel || !policy) {
            return;
        }
        const state = ensureExplorerGitCommitSearchState(pane);
        const mode = state.mode === 'hash' ? 'hash' : 'subject';
        const repo = pane._explorerGitRepo || {};
        const commits = Array.isArray(repo.commits) ? repo.commits : [];
        const plan = policy.searchPlan(commits, state.query, state.activeIndex, { mode });
        state.activeIndex = plan.activeIndex;
        let ordinal = 0;
        panel.querySelectorAll('[data-explorer-git-commit-toggle]').forEach((row, rowIndex) => {
            const subjectEl = row.querySelector('.explorer-diff-commit-subject');
            const commit = commits[rowIndex];
            if (!subjectEl || !commit) {
                return;
            }
            const hash = explorerGitCommitSearchableHash(commit);
            const shortHash = explorerGitCommitShortHash(commit);
            const ranges = plan.perCommit[rowIndex] || [];
            const subjectHtml = mode === 'subject' && ranges.length
                ? policy.markedSubjectHtml(
                    policy.commitSubject(commit), ranges, ordinal, plan.activeIndex
                )
                : escHtml(policy.commitSubject(commit));
            const hashHtml = mode === 'hash' && ranges.length
                ? policy.markedHashHtml(hash, ranges[0])
                : escHtml(shortHash);
            const hashMark = policy.hashMarkClass(ranges, ordinal, plan.activeIndex);
            ordinal += ranges.length;
            subjectEl.innerHTML =
                `<span class="explorer-diff-commit-hash${hashMark}">${hashHtml}</span> `
                + subjectHtml;
        });
        const count = panel.querySelector('[data-explorer-git-commit-search-count]');
        if (count) {
            count.textContent = explorerGitCommitSearchCountText(state, plan);
        }
        const empty = panel.querySelector('[data-explorer-git-commit-search-empty]');
        if (empty) {
            empty.textContent = plan.emptyText || '';
            empty.hidden = !state.open || !plan.emptyText;
        }
        const modeButton = panel.querySelector('[data-explorer-git-commit-search-mode]');
        if (modeButton) {
            const hashMode = mode === 'hash';
            modeButton.setAttribute('aria-pressed', hashMode ? 'true' : 'false');
            modeButton.title = hashMode ? 'Search commit subjects' : 'Search commit ids';
            modeButton.setAttribute('aria-label', modeButton.title);
        }
        panel.querySelectorAll(
            '[data-explorer-git-commit-search-prev], [data-explorer-git-commit-search-next]'
        ).forEach(button => {
            button.disabled = plan.matchCount === 0;
        });
        if (scroll && plan.matchCount) {
            const active = panel.querySelector(
                '.explorer-diff-commit .explorer-search-match.active, '
                + '.explorer-diff-commit-hash.explorer-git-commit-search-hit.active'
            );
            if (active) {
                requestAnimationFrame(() => {
                    scrollExplorerGitCommitRowIntoView(panel, active);
                });
            }
        }
    }

    /* Scroll the Git panel — vertically, and only it, which is why this does
       the maths instead of calling `scrollIntoView` on the match: `nearest`
       walks the horizontal axis too, and the subject span is an
       `overflow: hidden` scroller, so scrolling the mark into view shifted the
       whole subject left and pushed the hash out of the row. Same minimum-
       scroll maths as scrollExplorerTreeRowIntoView: a row already in view is
       left alone. */
    function scrollExplorerGitCommitRowIntoView(panel, mark) {
        const row = mark.closest('.explorer-diff-commit');
        if (!row) {
            return;
        }
        const panelBox = panel.getBoundingClientRect();
        const rowBox = row.getBoundingClientRect();
        const margin = Math.min(rowBox.height, Math.max(0, (panelBox.height - rowBox.height) / 2));
        if (rowBox.top < panelBox.top + margin) {
            panel.scrollTop -= (panelBox.top + margin) - rowBox.top;
        } else if (rowBox.bottom > panelBox.bottom - margin) {
            panel.scrollTop += rowBox.bottom - (panelBox.bottom - margin);
        }
    }

    function stepExplorerGitCommitSearch(index, delta) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerGitCommitSearchState(pane);
        state.activeIndex = Number(state.activeIndex || 0) + delta;
        paintExplorerGitCommitSearch(index, { scroll: true });
    }

    function clearExplorerGitCommitSearch(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const state = ensureExplorerGitCommitSearchState(pane);
        state.query = '';
        state.activeIndex = 0;
        paintExplorerGitCommitSearch(index);
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        const input = panel?.querySelector('[data-explorer-git-commit-search-input]');
        if (input) {
            input.value = '';
            input.focus();
        }
    }

    /* Show or hide the bar for the state the pane already holds. Never moves
       focus: a background re-render (explorer-git-watch.js) calls this too,
       and a sidebar that grabs the caret every few seconds is worse than one
       with no find at all. The toggle handler below focuses explicitly. */
    function applyExplorerGitCommitSearchVisibility(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const state = ensureExplorerGitCommitSearchState(pane);
        const bar = panel.querySelector('.explorer-git-commit-search');
        if (bar) {
            bar.hidden = !state.open;
        }
        const toggle = panel.querySelector('[data-explorer-git-commit-search-toggle]');
        if (toggle) {
            toggle.setAttribute('aria-expanded', state.open ? 'true' : 'false');
        }
        const input = panel.querySelector('[data-explorer-git-commit-search-input]');
        if (input && input.value !== state.query) {
            input.value = state.query;
        }
    }

    /* `action` is 'toggle' (the magnifier) or 'close' (Escape). Closing drops
       the query, so the marks it painted have to come off with it — hence the
       repaint after the visibility change. */
    function setExplorerGitCommitSearchOpen(index, action) {
        const pane = terminals[index];
        const policy = window.GridVibeExplorerGitSearch;
        if (!pane || !policy) {
            return;
        }
        pane._explorerGitCommitSearch = policy.nextVisibility(
            ensureExplorerGitCommitSearchState(pane), action
        );
        applyExplorerGitCommitSearchVisibility(index);
        paintExplorerGitCommitSearch(index);
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        const open = pane._explorerGitCommitSearch.open;
        const target = open
            ? panel?.querySelector('[data-explorer-git-commit-search-input]')
            : panel?.querySelector('[data-explorer-git-commit-search-toggle]');
        target?.focus();
    }

    async function toggleExplorerGitFollowBrowsing(index) {
        const pane = terminals[index];
        if (!pane || pane._explorerGitActionBusy || pane._explorerGitRepoLoading) {
            return false;
        }
        pane._explorerGitFollowBrowsing = !Boolean(pane._explorerGitFollowBrowsing);
        /* Before the load, for the same reason a pin write paints before its
           own: the Files tree's Follow marker reports a pane field that has
           already moved, and making the reader wait out a repository round
           trip to see it would be reporting the request rather than the
           state. Synchronous — nothing has awaited yet, so this is still the
           pane the gesture was made on. */
        refreshExplorerGitScopeAffordances(index);
        invalidateExplorerGitRepo(index);
        notePanePresentationChanged(index);
        await loadExplorerGitRepo(index);
        return true;
    }

    /* Everything outside the Git panel that reports where the *scope* is,
       painted from one place so those surfaces cannot drift apart: today the
       Files tree's pin and Follow markers, which move on exactly the same
       events the panel's own pin affordances do.

       Both markers, not just the pin: Follow moves on plain navigation, which
       is the one thing that changes a scope without reloading the repository,
       so a Follow marker left out of here would sit on the folder the reader
       walked away from until the next load re-rendered the tree.

       Paint-only and attribute-level by construction. Re-rendering the Git
       panel for a scope move is not an option — it carries the commit-message
       textarea and the commit-search input, and a re-render takes the caret —
       and re-rendering the tree body would reset its scroll. */
    function refreshExplorerGitScopeAffordances(index) {
        if (typeof applyExplorerTreeScopeMarks === 'function') {
            applyExplorerTreeScopeMarks(index);
        }
        applyExplorerGitPinButtonState(index);
    }

    /* The pin button, painted attribute-only.

       Two things move it. A pin write, which the tree marker answers beside
       it; and plain navigation, which changes `pinnedHere` while leaving the
       Git model untouched — with Follow off nothing reloads, so without this
       the button would keep a stale pressed state and a stale title until the
       next load.

       Attributes and a class, never a re-render: the panel carries the
       commit-message textarea and the commit-search input, and re-rendering
       it takes the caret. The repo bar's Clear pin is toggled by `hidden`
       rather than added and removed for the same reason the tree's root
       marker is — it must be able to move on a write whose reload has not
       re-rendered the panel yet. It now tracks the pin's *existence* rather
       than where the pane is standing, so plain navigation leaves it alone. */
    function applyExplorerGitPinButtonState(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const state = explorerGitPinState(pane);
        const button = panel.querySelector('[data-explorer-git-pin-toggle]');
        if (button) {
            button.setAttribute('aria-pressed', state.pressed ? 'true' : 'false');
            button.title = state.title;
            button.setAttribute('aria-label', state.title);
            button.classList.toggle('is-pinned-elsewhere', state.state === 'elsewhere');
        }
        const scopeClear = panel.querySelector('[data-explorer-git-scope-clear]');
        if (scopeClear) {
            scopeClear.hidden = !state.clearAvailable;
        }
    }

    /* One writer for the pin, so the header, menus, and explicit Clear pin
       cannot drift into different records. `null` means "no pin"; any string
       (including '') is a pinned path and carries its file/directory kind. */
    async function setExplorerGitPinnedScope(index, pinnedPath, pinnedKind = 'dir') {
        const pane = terminals[index];
        if (!pane || pane._explorerGitActionBusy || pane._explorerGitRepoLoading) {
            return false;
        }
        if (pinnedPath === null) {
            delete pane._explorerGitPinnedPath;
            delete pane._explorerGitPinKind;
        } else {
            pane._explorerGitPinnedPath = String(pinnedPath);
            pane._explorerGitPinKind = pinnedKind === 'file' ? 'file' : 'dir';
        }
        /* Before the load, not after it: the marker reports a pane field that
           has already moved, so making the reader wait out a repository round
           trip to see it would be reporting the request rather than the state.
           Synchronous, so no identity re-check is owed — nothing has awaited
           yet and this is still the pane the gesture was made on. */
        refreshExplorerGitScopeAffordances(index);
        invalidateExplorerGitRepo(index);
        notePanePresentationChanged(index);
        await loadExplorerGitRepo(index);
        return true;
    }

    /* Pin *here*: clear only when the pin is the file or folder being browsed,
       otherwise pin this path — including when a pin already exists
       somewhere else, which is one write and not an unpin followed by a pin.
       Two writes would mean two invalidate + load round trips, a visible
       flash at the intermediate root scope, and two presentation writes for
       one gesture. Never an ancestor match, so this can never clear a pin the
       user made on another path. */
    function toggleExplorerGitPinHere(index) {
        const pane = terminals[index];
        const target = explorerGitBrowsingScope(pane);
        return setExplorerGitPinnedScope(
            index,
            explorerGitPinState(pane).state === 'here'
                ? null
                : target.path,
            target.kind
        );
    }

    function clearExplorerGitPinnedScope(index) {
        return setExplorerGitPinnedScope(index, null);
    }

    /* How far down the panel a second sticky box has to start.

       The Graph's commit find is sticky too, and it must stack *below* the
       frozen repo bar rather than behind it: two sticky boxes at `top: 0` in
       one scroller claim the same strip, and the header wins on z-index, so
       the bar the reader is typing in would slide out of sight behind it —
       the one thing "a control the user is operating stays on screen"
       forbids.

       CSS cannot ask a sibling for its height, and this header's is genuinely
       variable: a repository line that may be absent, a branch line, and
       nought to two scope lines. So the header publishes its height as a
       custom property the find bar's `top` reads.

       Measured by ResizeObserver rather than by reading `offsetHeight` after
       the render, for two reasons. The panel is routinely rendered while
       `hidden` — a background group, a sidebar the reader has not opened —
       where every box measures 0, and writing that 0 in as the offset would
       stick the find bar behind the header for as long as the render stood;
       the observer answers when the box actually acquires a size. And a read
       straight after the `innerHTML` write is a forced synchronous layout on
       every poll of the change listener, which repaints this panel quietly
       and often.

       One observer per pane, re-pointed at each render's header, because the
       panel element outlives its contents. `offsetHeight` inside the callback
       is a border-box integer read at a point where layout is already clean;
       `contentRect` would drop the bar's own 8px padding. */
    function observeExplorerGitHeaderHeight(index) {
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!panel) {
            return;
        }
        const header = panel.querySelector('.explorer-git-repo-bar');
        if (!header) {
            /* Loading, and the repository-error panel: no frozen header, so
               nothing below it is owed an offset. Clearing rather than
               keeping the last one, because the find bar is still rendered on
               the error panel and would otherwise start below a header that
               is not there. */
            panel._explorerGitHeaderObserver?.disconnect();
            panel.style?.removeProperty('--explorer-git-header-height');
            delete panel._explorerGitHeaderHeight;
            return;
        }
        if (typeof window.ResizeObserver !== 'function') {
            return;
        }
        if (!panel._explorerGitHeaderObserver) {
            panel._explorerGitHeaderObserver = new window.ResizeObserver(entries => {
                const height = Math.max(0, Math.round(entries[0]?.target?.offsetHeight || 0));
                if (height === panel._explorerGitHeaderHeight) {
                    return;
                }
                panel._explorerGitHeaderHeight = height;
                panel.style?.setProperty('--explorer-git-header-height', `${height}px`);
            });
        }
        panel._explorerGitHeaderObserver.disconnect();
        panel._explorerGitHeaderObserver.observe(header);
    }

    function renderExplorerGitPanel(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        wireExplorerCopyPathMenu(panel, index);
        /* The card no longer lives in this panel, so the innerHTML below
           takes the row it is pinned to and leaves the card floating over
           whatever replaces it. */
        if (_explorerGitCommitCardPane === pane) {
            dismissExplorerGitCommitCard({ restoreFocus: false });
        }
        if (pane._explorerGitRepoLoading) {
            panel.innerHTML = '<div class="explorer-diff-sidebar-empty">Loading repository...</div>';
            observeExplorerGitHeaderHeight(index);
            return;
        }
        if (pane._explorerGitRepoError && !pane._explorerGitRepo) {
            const following = Boolean(pane._explorerGitFollowBrowsing);
            const followTitle = explorerGitFollowButtonTitle(pane, following);
            const pinned = typeof pane._explorerGitPinnedPath === 'string';
            const pinState = explorerGitPinState(pane);
            const pinnedKind = pane._explorerGitPinKind === 'file' ? 'file' : 'dir';
            /* A pin is faithfully re-applied on restore, including one made in
               a folder that is not inside any worktree — that is the pin
               working, not the pin being lost. But a bare "Folder is not
               inside a Git worktree" names no folder, and the pinned one may
               be nowhere near where the pane is now browsing, so the message
               reads as a bug in the pane rather than as a scope the user
               chose. Name the scope, and put the one action that resolves it
               next to it. In-pane and role="status": a pin is a state that
               lasts as long as the pin does, not an event for the launcher's
               banner. */
            const pinnedScopeNotice = pinned
                ? `
                <div class="explorer-git-scope-notice" role="status">
                    <span class="explorer-git-scope-notice-text">Pinned Git ${pinnedKind === 'file' ? 'file' : 'folder'}: <span class="explorer-git-scope-notice-path" title="${escHtml(explorerGitScopePathLabel(pane._explorerGitPinnedPath))}">${escHtml(explorerGitScopeLabel(pane._explorerGitPinnedPath, pinnedKind))}</span></span>
                    <button type="button" class="explorer-git-clear-pin-btn" data-explorer-git-clear-pin>Clear pin</button>
                </div>`
                : '';
            panel.innerHTML = `
                <div class="explorer-diff-sidebar-error">${escHtml(pane._explorerGitRepoError)}</div>
                ${pinnedScopeNotice}
                <div class="explorer-diff-sidebar-section">
                    <div class="explorer-diff-sidebar-title explorer-git-section-title">
                        <span>Graph</span>
                        <span class="explorer-git-section-actions">
                            <button type="button" class="explorer-search-btn explorer-git-pin-toggle${pinState.state === 'elsewhere' ? ' is-pinned-elsewhere' : ''}" data-explorer-git-pin-toggle aria-pressed="${pinState.pressed ? 'true' : 'false'}" title="${escHtml(pinState.title)}" aria-label="${escHtml(pinState.title)}">${EXPLORER_GIT_PIN_ICON}</button>
                            <button type="button" class="explorer-search-btn explorer-git-follow-toggle" data-explorer-git-follow-toggle aria-pressed="${following ? 'true' : 'false'}" title="${escHtml(followTitle)}" aria-label="${escHtml(followTitle)}">${EXPLORER_GIT_FOLLOW_ICON}</button>
                            <button type="button" class="explorer-search-btn explorer-git-commit-search-toggle" disabled title="Search commit messages" aria-label="Search commit messages">${EXPLORER_GIT_SEARCH_ICON}</button>
                        </span>
                    </div>
                </div>`;
            panel.querySelector('[data-explorer-git-pin-toggle]')?.addEventListener('click', () => {
                toggleExplorerGitPinHere(index);
            });
            panel.querySelector('[data-explorer-git-follow-toggle]')?.addEventListener('click', () => {
                toggleExplorerGitFollowBrowsing(index);
            });
            panel.querySelector('[data-explorer-git-clear-pin]')?.addEventListener('click', () => {
                clearExplorerGitPinnedScope(index);
            });
            observeExplorerGitHeaderHeight(index);
            return;
        }

        const repo = pane._explorerGitRepo || {};
        const git = repo.git || {};
        const errorBanner = pane._explorerGitRepoError
            ? `<div class="explorer-diff-sidebar-error">${escHtml(pane._explorerGitRepoError)}</div>`
            : '';
        /* The change listener (explorer-git-watch.js) suspends itself after
           repeated failures; it must never make the sidebar look broken, so
           this is one muted line above the last good state — Refresh re-arms. */
        const watchPausedBanner = pane._explorerGitWatchSuspended
            ? '<div class="explorer-diff-sidebar-empty explorer-git-watch-paused">Live updates paused — use Refresh</div>'
            : '';
        const changes = Array.isArray(repo.changes) ? repo.changes : [];
        const { staged, unstaged } = splitExplorerGitChanges(changes);
        /* Discard All remains tracked-only even though a confirmed per-row
           action may now remove one explicitly selected untracked file. */
        const discardable = unstaged.filter(file => explorerGitCanBulkDiscard(file.git && file.git.status));
        const commits = Array.isArray(repo.commits) ? repo.commits : [];
        const expandedCommits = ensureExplorerDiffExpandedCommits(pane);
        const busy = Boolean(pane._explorerGitActionBusy);
        const commitMessage = typeof pane._explorerGitCommitMessage === 'string' ? pane._explorerGitCommitMessage : '';
        const hasUpstream = git.ahead !== null && git.ahead !== undefined;
        const publishLabel = hasUpstream ? 'Push' : 'Publish branch';
        const repoName = String(git.repo_name || '').trim();
        const repoBranchText = explorerGitBranchLabel(git);
        const repoBranchTitle = explorerGitBranchTitle(git);
        const following = Boolean(pane._explorerGitFollowBrowsing);
        const followTitle = explorerGitFollowButtonTitle(pane, following);
        const pinState = explorerGitPinState(pane);
        const browsedScope = explorerGitBrowsingScope(pane);
        const pinnedKind = pane._explorerGitPinKind === 'file' ? 'file' : 'dir';
        const scopeLines = window.GridVibeExplorerGitPin.explorerGitScopeLines(
            typeof pane._explorerGitPinnedPath === 'string' ? pane._explorerGitPinnedPath : null,
            browsedScope.path,
            following,
            pinnedKind,
            browsedScope.kind
        );
        const effectiveScopeKind = explorerGitScopeKind(pane);
        const effectiveScopePath = explorerGitScopePath(pane);
        const bulkScopeLabel = effectiveScopeKind === 'file'
            ? `file ${explorerGitScopePathLabel(effectiveScopePath)}`
            : (effectiveScopePath === null ? 'explorer root' : `folder ${explorerGitScopePathLabel(effectiveScopePath)}`);
        const commitSearch = ensureExplorerGitCommitSearchState(pane);
        const commitSearchMode = commitSearch.mode === 'hash' ? 'hash' : 'subject';
        const searchPolicy = window.GridVibeExplorerGitSearch;
        const searchPlan = searchPolicy
            ? searchPolicy.searchPlan(
                commits, commitSearch.query, commitSearch.activeIndex, { mode: commitSearchMode }
            )
            : { perCommit: commits.map(() => []), matchCount: 0, activeIndex: 0, emptyText: '' };
        commitSearch.activeIndex = searchPlan.activeIndex;
        const graphPolicy = window.GridVibeExplorerGitGraph;
        /* One clock for the whole pass: the "3 days ago" in every row's
           accessible name is read once per render, so every row on screen
           agrees about now. */
        const renderedAt = Date.now();
        const pagePlan = graphPolicy
            ? graphPolicy.pagePlan(repo, { loading: Boolean(pane._explorerGitCommitPageLoading) })
            : null;
        let searchOrdinal = 0;
        const commitRows = commits.length
            ? commits.map((commit, commitIndex) => {
                const hash = commit.hash || '';
                const searchableHash = explorerGitCommitSearchableHash(commit);
                const expanded = hash && expandedCommits.has(
                    window.GridVibeExplorerGitActive.commitKey(hash)
                );
                const subject = searchPolicy
                    ? searchPolicy.commitSubject(commit)
                    : (commit.subject || commit.line || '');
                const ranges = searchPlan.perCommit[commitIndex] || [];
                const subjectHtml = commitSearchMode === 'subject' && ranges.length
                    ? searchPolicy.markedSubjectHtml(subject, ranges, searchOrdinal, searchPlan.activeIndex)
                    : escHtml(subject);
                const hashHtml = commitSearchMode === 'hash' && ranges.length
                    ? searchPolicy.markedHashHtml(searchableHash, ranges[0])
                    : escHtml(explorerGitCommitShortHash(commit));
                const hashMark = searchPolicy
                    ? searchPolicy.hashMarkClass(ranges, searchOrdinal, searchPlan.activeIndex)
                    : '';
                searchOrdinal += ranges.length;
                /* Built for the row button's accessible name, not for a
                   card: the card itself is created on demand by the
                   right-click (openExplorerGitCommitCard). */
                const card = graphPolicy
                    ? graphPolicy.commitCard(commit, { expanded, now: renderedAt })
                    : null;
                return `
                    <button type="button" class="explorer-diff-commit" data-explorer-git-commit-toggle="${escHtml(hash)}" data-explorer-git-commit-full="${escHtml(commit.full_hash || '')}" data-explorer-git-commit-message="${escHtml(commit.message || '')}" ${hash ? '' : 'disabled'} aria-label="${escHtml(card ? card.summary : (commit.line || ''))}" aria-expanded="${expanded ? 'true' : 'false'}">
                        <span class="explorer-diff-commit-graph">${explorerGitGraphHtml(commit.graph)}</span>
                        <span class="explorer-diff-commit-toggle" aria-hidden="true">${expanded ? UI_CHEVRON_DOWN_ICON : UI_CHEVRON_RIGHT_ICON}</span>
                        <span class="explorer-diff-commit-subject"><span class="explorer-diff-commit-hash${hashMark}">${hashHtml}</span> ${subjectHtml}</span>
                    </button>
                    ${expanded ? `<div class="explorer-diff-commit-files">${renderExplorerGitFileRows(index, commit.files, { emptyText: 'No files recorded for this commit.', commitHash: hash })}</div>` : ''}
                `;
            }).join('')
            : '<div class="explorer-diff-sidebar-empty">No commits in this scope.</div>';

        /* The repo bar is the panel's frozen header (see the sticky rule in
           terminals.css): repository, branch, and the pin/Follow scope lines
           are the facts every row further down is *about*, so they stay on
           screen while the change lists and the graph scroll under them.

           Publish/Push is deliberately not in it. It is the one mutation in
           this panel that reaches a remote, and freezing it would leave it
           under the pointer at every scroll position; it sits in its own
           section immediately below and scrolls away like everything else. */
        panel.innerHTML = `
            ${errorBanner}
            ${watchPausedBanner}
            <div class="explorer-diff-sidebar-section explorer-git-repo-bar">
                <div class="explorer-git-repo-details">
                    ${repoName ? `
                    <div class="explorer-git-repo-line explorer-git-repo-root" title="${escHtml(git.repo_root || repoName)}">
                        <span class="explorer-git-repo-icon">${EXPLORER_FOLDER_ICON}</span>
                        <span class="explorer-git-repo-text">${escHtml(repoName)}</span>
                    </div>` : ''}
                    <div class="explorer-git-repo-line explorer-git-repo-branch" title="${escHtml(repoBranchTitle)}">
                        <span class="explorer-git-repo-icon">${EXPLORER_GIT_TOGGLE_ICON}</span>
                        <span class="explorer-git-repo-text">${escHtml(repoBranchText)}</span>
                    </div>
                    ${scopeLines.map(line => `
                    <div class="explorer-git-repo-line explorer-git-repo-scope explorer-git-repo-scope-${line.kind}${line.overridden ? ' is-overridden' : ''}" title="${escHtml(line.title)}">
                        <span class="explorer-git-repo-icon">${line.kind === 'follow' ? EXPLORER_GIT_FOLLOW_ICON : EXPLORER_GIT_PIN_ICON}</span>
                        <span class="explorer-git-repo-text">${escHtml(line.label)}</span>
                        ${line.kind === 'pin' ? `<button type="button" class="explorer-git-clear-pin-btn explorer-git-scope-clear-btn" data-explorer-git-clear-pin data-explorer-git-scope-clear ${line.clearAvailable ? '' : 'hidden'} title="Clear the pinned Git ${line.scopeKind === 'file' ? 'file' : 'folder'}: ${escHtml(line.path)}" aria-label="Clear the pinned Git ${line.scopeKind === 'file' ? 'file' : 'folder'}: ${escHtml(line.path)}">Clear pin</button>` : ''}
                    </div>`).join('')}
                </div>
            </div>
            <div class="explorer-diff-sidebar-section explorer-git-publish-box">
                <button type="button" class="explorer-git-publish-btn" data-explorer-git-publish ${busy ? 'disabled' : ''} title="Push the current branch to its remote">${escHtml(publishLabel)}</button>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title explorer-git-section-title">
                    <span>Staged Changes</span>
                    <span class="explorer-git-section-actions">
                        <button type="button" class="explorer-search-btn explorer-git-unstage-btn explorer-git-unstage-all-btn" data-explorer-git-unstage-all ${(busy || !staged.length) ? 'disabled' : ''} title="Unstage all changes in ${escHtml(bulkScopeLabel)}" aria-label="Unstage all changes">${UI_MINUS_ICON}</button>
                    </span>
                </div>
                <div class="explorer-diff-commit-files explorer-git-change-list">
                    ${renderExplorerGitFileRows(index, staged, { emptyText: 'No staged changes.', action: 'unstage' })}
                </div>
                <div class="explorer-git-commit-box">
                    <textarea class="explorer-git-commit-message" id="explorer-git-commit-message-${index}" rows="2" placeholder="Message (commits staged changes)" ${busy ? 'disabled' : ''}>${escHtml(commitMessage)}</textarea>
                    <button type="button" class="explorer-git-commit-btn" data-explorer-git-commit ${(busy || !staged.length) ? 'disabled' : ''} title="Commit staged changes">Commit</button>
                </div>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title explorer-git-section-title">
                    <span>Changes</span>
                    <span class="explorer-git-section-actions">
                        <button type="button" class="explorer-search-btn explorer-git-revert-btn explorer-git-discard-all-btn" data-explorer-git-discard-all ${(busy || !discardable.length) ? 'disabled' : ''} title="Discard all changes in ${escHtml(bulkScopeLabel)}" aria-label="Discard all changes">${EXPLORER_GIT_REVERT_ICON}</button>
                        <button type="button" class="explorer-search-btn explorer-git-stage-btn explorer-git-stage-all-btn" data-explorer-git-stage-all ${(busy || !unstaged.length) ? 'disabled' : ''} title="Stage all changes in ${escHtml(bulkScopeLabel)}" aria-label="Stage all changes">${UI_PLUS_ICON}</button>
                    </span>
                </div>
                <div class="explorer-diff-commit-files explorer-git-change-list">
                    ${renderExplorerGitFileRows(index, unstaged, { emptyText: 'No unstaged changes.', action: 'stage' })}
                </div>
            </div>
            <div class="explorer-diff-sidebar-section">
                <div class="explorer-diff-sidebar-title explorer-git-section-title">
                    <span>Graph</span>
                    <span class="explorer-git-section-actions">
                        <button type="button" class="explorer-search-btn explorer-git-pin-toggle${pinState.state === 'elsewhere' ? ' is-pinned-elsewhere' : ''}" data-explorer-git-pin-toggle aria-pressed="${pinState.pressed ? 'true' : 'false'}" ${busy ? 'disabled' : ''} title="${escHtml(pinState.title)}" aria-label="${escHtml(pinState.title)}">${EXPLORER_GIT_PIN_ICON}</button>
                        <button type="button" class="explorer-search-btn explorer-git-follow-toggle" data-explorer-git-follow-toggle aria-pressed="${following ? 'true' : 'false'}" ${busy ? 'disabled' : ''} title="${escHtml(followTitle)}" aria-label="${escHtml(followTitle)}">${EXPLORER_GIT_FOLLOW_ICON}</button>
                        <button type="button" class="explorer-search-btn explorer-git-commit-search-toggle" data-explorer-git-commit-search-toggle aria-expanded="${commitSearch.open ? 'true' : 'false'}" title="Search commit messages" aria-label="Search commit messages">${EXPLORER_GIT_SEARCH_ICON}</button>
                    </span>
                </div>
                <div class="explorer-git-commit-search" ${commitSearch.open ? '' : 'hidden'}>
                    <input
                        type="search"
                        class="explorer-search-input"
                        data-explorer-git-commit-search-input
                        placeholder="Search commits"
                        autocomplete="off"
                        spellcheck="false"
                        aria-label="Search commit messages"
                        value="${escHtml(commitSearch.query)}"
                    >
                    <span class="explorer-search-count" data-explorer-git-commit-search-count>${explorerGitCommitSearchCountText(commitSearch, searchPlan)}</span>
                    <button type="button" class="explorer-search-btn explorer-git-commit-search-mode" data-explorer-git-commit-search-mode aria-pressed="${commitSearchMode === 'hash' ? 'true' : 'false'}" title="${commitSearchMode === 'hash' ? 'Search commit subjects' : 'Search commit ids'}" aria-label="${commitSearchMode === 'hash' ? 'Search commit subjects' : 'Search commit ids'}">${EXPLORER_GIT_HASH_ICON}</button>
                    <button type="button" class="explorer-search-btn" data-explorer-git-commit-search-prev ${searchPlan.matchCount ? '' : 'disabled'} title="Previous match" aria-label="Previous match">↑</button>
                    <button type="button" class="explorer-search-btn" data-explorer-git-commit-search-next ${searchPlan.matchCount ? '' : 'disabled'} title="Next match" aria-label="Next match">↓</button>
                    <button type="button" class="explorer-search-btn" data-explorer-git-commit-search-clear title="Clear search" aria-label="Clear search">×</button>
                </div>
                <span class="explorer-git-commit-search-empty" data-explorer-git-commit-search-empty role="status" aria-live="polite" ${(commitSearch.open && searchPlan.emptyText) ? '' : 'hidden'}>${escHtml(searchPlan.emptyText || '')}</span>
                ${commitRows}
                ${explorerGitGraphMoreHtml(pagePlan)}
            </div>
        `;
        const commitMessageInput = panel.querySelector(`#explorer-git-commit-message-${index}`);
        if (commitMessageInput) {
            commitMessageInput.addEventListener('input', () => {
                pane._explorerGitCommitMessage = commitMessageInput.value;
            });
        }
        const commitSearchInput = panel.querySelector('[data-explorer-git-commit-search-input]');
        if (commitSearchInput) {
            commitSearchInput.addEventListener('input', () => {
                const state = ensureExplorerGitCommitSearchState(pane);
                state.query = commitSearchInput.value;
                state.activeIndex = 0;
                paintExplorerGitCommitSearch(index);
            });
            commitSearchInput.addEventListener('keydown', event => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    stepExplorerGitCommitSearch(index, event.shiftKey ? -1 : 1);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    setExplorerGitCommitSearchOpen(index, 'close');
                }
            });
        }
        panel.querySelector('[data-explorer-git-commit-search-toggle]')?.addEventListener('click', () => {
            setExplorerGitCommitSearchOpen(index, 'toggle');
        });
        panel.querySelector('[data-explorer-git-commit-search-mode]')?.addEventListener('click', () => {
            const state = ensureExplorerGitCommitSearchState(pane);
            state.mode = state.mode === 'hash' ? 'subject' : 'hash';
            state.activeIndex = 0;
            paintExplorerGitCommitSearch(index);
        });
        panel.querySelector('[data-explorer-git-follow-toggle]')?.addEventListener('click', () => {
            toggleExplorerGitFollowBrowsing(index);
        });
        panel.querySelector('[data-explorer-git-pin-toggle]')?.addEventListener('click', () => {
            toggleExplorerGitPinHere(index);
        });
        /* The one always-reachable clear, and it is offered wherever a pin
           is — including on the pinned folder itself, where the pressed
           button is a second way to the same write. The button no longer
           clears a pin you have navigated away from, so without this a pin
           on a folder that is collapsed, deleted, or outside the current
           root would be unclearable; and a clear that appeared only from
           elsewhere went missing at the one folder a reader stands in when
           they decide to unpin. Same writer as the button; no modifier
           gesture, since Alt already means level-fold here and an invisible
           gesture is not an affordance. */
        panel.querySelector('[data-explorer-git-clear-pin]')?.addEventListener('click', () => {
            clearExplorerGitPinnedScope(index);
        });
        panel.querySelector('[data-explorer-git-commit-search-prev]')?.addEventListener('click', () => {
            stepExplorerGitCommitSearch(index, -1);
        });
        panel.querySelector('[data-explorer-git-commit-search-next]')?.addEventListener('click', () => {
            stepExplorerGitCommitSearch(index, 1);
        });
        panel.querySelector('[data-explorer-git-show-more]')?.addEventListener('click', () => {
            loadMoreExplorerGitCommits(index);
        });
        panel.querySelector('[data-explorer-git-commit-search-clear]')?.addEventListener('click', () => {
            clearExplorerGitCommitSearch(index);
        });
        panel.querySelectorAll('[data-explorer-git-stage]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitStageFile(index, button.dataset.explorerGitStage || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-unstage]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitUnstageFile(index, button.dataset.explorerGitUnstage || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-revert]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitRevertFile(
                    index,
                    button.dataset.explorerGitRevert || '',
                    button.dataset.explorerGitRevertStatus || '',
                );
            });
        });
        const stageAllButton = panel.querySelector('[data-explorer-git-stage-all]');
        if (stageAllButton) {
            stageAllButton.addEventListener('click', () => explorerGitStageAll(index));
        }
        const unstageAllButton = panel.querySelector('[data-explorer-git-unstage-all]');
        if (unstageAllButton) {
            unstageAllButton.addEventListener('click', () => explorerGitUnstageAll(index));
        }
        const discardAllButton = panel.querySelector('[data-explorer-git-discard-all]');
        if (discardAllButton) {
            discardAllButton.addEventListener('click', () => explorerGitDiscardAll(index));
        }
        const publishButton = panel.querySelector('[data-explorer-git-publish]');
        if (publishButton) {
            publishButton.addEventListener('click', () => explorerGitPublish(index));
        }
        const commitButton = panel.querySelector('[data-explorer-git-commit]');
        if (commitButton) {
            commitButton.addEventListener('click', () => explorerGitCommit(index));
        }
        panel.querySelectorAll('[data-explorer-git-open-file]').forEach(button => {
            button.addEventListener('click', () => {
                explorerGitOpenFile(
                    index,
                    button.dataset.explorerGitOpenFile || '',
                    button.dataset.explorerGitDiffMode || 'worktree',
                );
            });
        });
        panel.querySelectorAll('[data-explorer-git-open-commit-diff]').forEach(button => {
            button.addEventListener('click', () => {
                explorerGitOpenCommitDiff(
                    index,
                    button.dataset.explorerGitOpenCommitDiff || '',
                    button.dataset.explorerGitCommit || '',
                );
            });
        });
        panel.querySelectorAll('[data-explorer-git-open-folder]').forEach(button => {
            button.addEventListener('click', event => {
                event.stopPropagation();
                explorerGitOpenFolder(index, button.dataset.explorerGitOpenFolder || '');
            });
        });
        panel.querySelectorAll('[data-explorer-git-commit-toggle]').forEach(button => {
            button.addEventListener('mousedown', event => {
                if (event.altKey) {
                    /* Keep the textarea active until the click handler captures
                       its selection. The render below replaces that textarea. */
                    event.preventDefault();
                }
            });
            button.addEventListener('click', event => {
                const commit = button.dataset.explorerGitCommitToggle || '';
                if (!commit) {
                    return;
                }
                const expanded = ensureExplorerDiffExpandedCommits(pane);
                if (event.altKey) {
                    const focusState = explorerGitCommitMessageFocusState(index);
                    const plan = window.GridVibeExplorerGitSearch.collapseAllPlan(
                        Array.from(expanded)
                    );
                    if (plan.changed) {
                        expanded.clear();
                        renderExplorerGitPanel(index);
                        notePanePresentationChanged(index);
                        restoreExplorerGitCommitMessageFocus(index, focusState);
                    }
                    return;
                }
                const key = window.GridVibeExplorerGitActive.commitKey(commit);
                /* Collapsing the commit holding the open diff is final: this
                   pane has already recorded it as revealed, so the reveal in
                   syncExplorerGitActiveRows() never re-opens it. */
                if (expanded.has(key)) {
                    expanded.delete(key);
                } else {
                    expanded.add(key);
                }
                renderExplorerGitPanel(index);
                notePanePresentationChanged(index);
            });
        });
        observeExplorerGitHeaderHeight(index);
        paintExplorerGitActiveRows(index);
    }

    /* The viewer's identity in the sidebar's vocabulary. `_explorerDiffCommit`
       is what separates a past commit's copy of a path from the worktree's. */
    function explorerGitViewerTarget(pane) {
        return window.GridVibeExplorerGitActive.viewerTarget({
            mode: pane?._explorerMode,
            filePath: pane?._explorerFilePath,
            diffCommit: pane?._explorerDiffCommit,
            diffMode: pane?._explorerDiffMode
        });
    }

    /* Paint only — a class toggle over rows already on screen (guardrail 8),
       never a markup rewrite: the sidebar carries a commit-message textarea,
       and re-rendering it on every file open would take the caret with it. */
    function paintExplorerGitActiveRows(index) {
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel) {
            return;
        }
        const model = window.GridVibeExplorerGitActive;
        const target = explorerGitViewerTarget(pane);
        let activeRow = null;
        panel.querySelectorAll('.explorer-diff-commit-file[data-explorer-copy-path]').forEach(row => {
            const active = model.rowIsActive(target, {
                path: row.dataset.explorerCopyPath || '',
                commitHash: row.dataset.explorerGitRowCommit || '',
                diffMode: row.dataset.explorerGitRowMode || ''
            });
            row.classList.toggle('active', active);
            if (active) {
                row.setAttribute('aria-current', 'true');
                activeRow = activeRow || row;
            } else {
                row.removeAttribute('aria-current');
            }
        });
        panel.querySelectorAll('[data-explorer-git-commit-toggle]').forEach(button => {
            button.classList.toggle(
                'active',
                model.commitIsActive(target, button.dataset.explorerGitCommitToggle || '')
            );
        });
        /* Only after a reveal, so an ordinary repaint never fights the scroll
           position the sidebar was restored (or scrolled by hand) to. */
        if (pane._explorerGitScrollActiveIntoView) {
            pane._explorerGitScrollActiveIntoView = false;
            activeRow?.scrollIntoView({ block: 'nearest' });
        }
    }

    /* Reveal + paint. Re-renders the panel only when a commit actually had to
       be opened, which happens at most once per pane per commit. */
    function syncExplorerGitActiveRows(index) {
        const pane = terminals[index];
        if (!pane || !document.getElementById(`explorer-git-panel-${index}`)) {
            return;
        }
        const expanded = ensureExplorerDiffExpandedCommits(pane);
        const plan = window.GridVibeExplorerGitActive.revealPlan(explorerGitViewerTarget(pane), {
            expanded: Array.from(expanded),
            revealed: pane._explorerGitRevealedCommit || '',
            commits: (pane._explorerGitRepo?.commits || []).map(commit => commit.hash || '')
        });
        if (!plan.revealedCommit) {
            paintExplorerGitActiveRows(index);
            return;
        }
        pane._explorerGitRevealedCommit = plan.revealedCommit;
        pane._explorerGitScrollActiveIntoView = true;
        if (!plan.expandKey) {
            paintExplorerGitActiveRows(index);
            return;
        }
        expanded.add(plan.expandKey);
        // Opening the row is expansion state like any other, so it is saved
        // like any other — the whole point is that it survives the next restore.
        renderExplorerGitPanel(index);
        notePanePresentationChanged(index);
    }

    function renderExplorerGitPanels(index) {
        renderExplorerGitPanel(index);
        syncExplorerGitActiveRows(index);
    }

    function invalidateExplorerGitRepo(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        pane._explorerGitRepoLoaded = false;
        pane._explorerGitRepoLoading = false;
        pane._explorerGitRepoError = '';
        pane._explorerGitRepo = null;
        pane._explorerGitAnchorPath = '';
        pane._explorerGitAnchorKind = 'dir';
        pane._explorerGitAnchorLimit = 0;
        renderExplorerGitPanels(index);
    }

    function ensureExplorerDiffExpandedCommits(pane) {
        if (!(pane?._explorerDiffExpandedCommits instanceof Set)) {
            pane._explorerDiffExpandedCommits = new Set();
        }
        return pane._explorerDiffExpandedCommits;
    }

    async function loadExplorerGitRepo(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        // Before the URL is built: an expansion belongs to the scope it was
        // made in, and this is the one place every scope move passes through.
        explorerGitDropStaleCommitLimit(pane);
        const scopePath = explorerGitScopePath(pane);
        const scopeKind = explorerGitScopeKind(pane);
        const requestedAnchorPath = explorerGitScopeIdentity(scopePath);
        const requestedLimit = Number(pane?._explorerGitCommitLimit) || 0;
        /* Like compared with like: both sides are the scope that was, or would
           be, *requested* — never the resolved spelling the server answers with.
           The page joins that identity, so "Show more" is a real reload rather
           than an early return on the model it is trying to grow. */
        const loadedForPath = pane?._explorerGitAnchorPath === requestedAnchorPath
            && (pane?._explorerGitAnchorKind || 'dir') === scopeKind
            && (Number(pane?._explorerGitAnchorLimit) || 0) === requestedLimit;
        if (!pane || !sessionId || (pane._explorerGitRepoLoaded && loadedForPath) || pane._explorerGitRepoLoading) {
            renderExplorerGitPanels(index);
            return;
        }

        pane._explorerGitRepoLoading = true;
        pane._explorerGitRepoError = '';
        renderExplorerGitPanels(index);
        try {
            const response = await fetch(
                explorerGitRequestUrl(
                    sessionId,
                    'repo',
                    scopePath,
                    explorerGitCommitLimitParams(pane),
                    scopeKind
                )
            );
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load Git repository');
            }
            if (
                terminals[index] !== pane
                || sessionIds[index] !== sessionId
                || explorerGitScopePath(pane) !== scopePath
                || explorerGitScopeKind(pane) !== scopeKind
            ) {
                return;
            }
            pane._explorerGitRepoLoaded = true;
            pane._explorerGitRepo = data;
            explorerGitNoteLoadedScope(pane, requestedAnchorPath, scopeKind);
            pane._explorerGitRevision = typeof data.revision === 'string' ? data.revision : '';
            // A user-initiated load re-arms a suspended change-listener watch.
            pane._explorerGitWatchSuspended = false;
            syncExplorerTabGitFromRepo(index, data);
        } catch (error) {
            if (
                terminals[index] === pane
                && sessionIds[index] === sessionId
                && explorerGitScopePath(pane) === scopePath
                && explorerGitScopeKind(pane) === scopeKind
            ) {
                console.error('[GridVibe Sessions] Explorer Git repository failed:', error);
                pane._explorerGitRepoError = error.message || 'Failed to load Git repository.';
            }
        } finally {
            pane._explorerGitRepoLoading = false;
            if (terminals[index] === pane && sessionIds[index] === sessionId) {
                renderExplorerGitPanels(index);
                if (
                    pane._explorerGitSidebarOpen
                    && (
                        explorerGitScopePath(pane) !== scopePath
                        || explorerGitScopeKind(pane) !== scopeKind
                    )
                ) {
                    loadExplorerGitRepo(index);
                }
            }
        }
    }

    /* One panel render that leaves the reader where they were standing.

       renderExplorerGitPanel() rewrites the panel's innerHTML, and emptying a
       scroller clamps it to 0 -- fine for a first paint, wrong for a repaint
       of a list the reader has scrolled to the bottom of to reach the control
       they just pressed. */
    function repaintExplorerGitPanelInPlace(index) {
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        const scrollTop = panel ? panel.scrollTop : 0;
        renderExplorerGitPanel(index);
        if (panel) {
            panel.scrollTop = Math.min(scrollTop, panel.scrollHeight);
        }
    }

    /* "Show more": read one page further back in the same scope.

       A quiet reload rather than loadExplorerGitRepo(), for the same reason
       the change listener uses one -- the Loading placeholder would replace a
       panel the reader is standing at the bottom of, and the quiet swap puts
       both the panel's scroll offset and the commit-message caret back. The
       page moves before the request so the request carries it, and the panel
       repaints at once so the button reports the read it has started rather
       than the state it is leaving.

       Everything the answer touches is bound to the captured identity. The
       busy flag is cleared on the pane that asked wherever it now lives -- a
       flag left set is a control that can never act again -- while the
       repaint and the swap address the slot and are skipped once the slot has
       changed hands. A failure puts the page back, so the model on the pane
       and the page it is the model *for* cannot disagree. */
    async function loadMoreExplorerGitCommits(index) {
        const identity = explorerGitCaptureIdentity(index);
        const pane = identity.pane;
        const policy = window.GridVibeExplorerGitGraph;
        if (!pane || !policy || pane._explorerGitCommitPageLoading) {
            return false;
        }
        const plan = policy.pagePlan(pane._explorerGitRepo, {});
        if (!plan.canLoadMore || !plan.nextLimit) {
            return false;
        }
        const previousLimit = pane._explorerGitCommitLimit;
        const previousScope = pane._explorerGitCommitLimitScope;
        pane._explorerGitCommitLimit = plan.nextLimit;
        pane._explorerGitCommitLimitScope = explorerGitCommitScopeKey(pane);
        pane._explorerGitCommitPageLoading = true;
        repaintExplorerGitPanelInPlace(index);
        let painted = false;
        try {
            const data = await refreshExplorerGitRepoQuiet(index);
            /* Cleared before the swap paints, not after: the swap is the one
               render that restores the scroll, so a second one behind it
               would take the reader back to the top to change a label. */
            delete pane._explorerGitCommitPageLoading;
            if (!data) {
                pane._explorerGitCommitLimit = previousLimit;
                pane._explorerGitCommitLimitScope = previousScope;
                return false;
            }
            if (!explorerGitIdentityIsCurrent(index, identity)) {
                return false;
            }
            painted = applyExplorerGitRepoQuiet(
                index, data, identity.scopePath, identity.scopeKind
            );
            return true;
        } catch (error) {
            pane._explorerGitCommitLimit = previousLimit;
            pane._explorerGitCommitLimitScope = previousScope;
            return false;
        } finally {
            delete pane._explorerGitCommitPageLoading;
            if (!painted && explorerGitIdentityIsCurrent(index, identity)) {
                repaintExplorerGitPanelInPlace(index);
            }
        }
    }

    async function refreshExplorerGitRepoQuiet(index) {
        /* Background variant of loadExplorerGitRepo for the Git change
           listener (explorer-git-watch.js): forced (no _explorerGitRepoLoaded
           early return, no invalidate — the last good panel stays on screen),
           quiet (only a git-refreshing class on the panel, never the Loading
           placeholder), and identity-checked (a stale pane/session id yields
           null). Returns the fresh payload, or null on failure/staleness —
           the pane keeps its last good _explorerGitRepo either way. */
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const scopePath = explorerGitScopePath(pane);
        const scopeKind = explorerGitScopeKind(pane);
        if (!pane || !sessionId || pane._explorerGitRepoLoading || pane._explorerGitRepoRefreshing) {
            return null;
        }
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        pane._explorerGitRepoRefreshing = true;
        panel?.classList.add('git-refreshing');
        try {
            const response = await fetch(
                explorerGitRequestUrl(
                    sessionId,
                    'repo',
                    scopePath,
                    explorerGitCommitLimitParams(pane),
                    scopeKind
                ),
                { cache: 'no-store' }
            );
            const data = await response.json();
            if (!response.ok) {
                return null;
            }
            if (
                terminals[index] !== pane
                || sessionIds[index] !== sessionId
                || explorerGitScopePath(pane) !== scopePath
                || explorerGitScopeKind(pane) !== scopeKind
            ) {
                return null;
            }
            return data;
        } catch (error) {
            return null;
        } finally {
            pane._explorerGitRepoRefreshing = false;
            panel?.classList.remove('git-refreshing');
        }
    }

    function applyExplorerGitRepoQuiet(
        index,
        data,
        requestedScopePath,
        requestedScopeKind
    ) {
        /* Swap a quietly fetched Git payload into the sidebar in place: one
           panel render, scroll/focus preserved, tab badges re-rendered only
           when the badge map actually changed (syncExplorerTabGitFromRepo
           guards that itself). The commit draft, expanded commits, sidebar
           sizing and open state all live in pane state and survive the render. */
        const pane = terminals[index];
        const panel = document.getElementById(`explorer-git-panel-${index}`);
        if (!pane || !panel || !data) {
            return false;
        }
        const scrollTop = panel.scrollTop;
        const active = document.activeElement;
        const focusState = active && panel.contains(active) && typeof active.selectionStart === 'number'
            ? { id: active.id, start: active.selectionStart, end: active.selectionEnd }
            : null;
        pane._explorerGitRepo = data;
        pane._explorerGitRepoLoaded = true;
        explorerGitNoteLoadedScope(
            pane,
            requestedScopePath === undefined
                ? explorerGitRequestedScope(pane)
                : requestedScopePath,
            requestedScopeKind === undefined
                ? explorerGitRequestedScopeKind(pane)
                : requestedScopeKind
        );
        pane._explorerGitRevision = typeof data.revision === 'string' ? data.revision : '';
        syncExplorerTabGitFromRepo(index, data);
        renderExplorerGitPanel(index);
        panel.scrollTop = Math.min(scrollTop, panel.scrollHeight);
        if (focusState && focusState.id) {
            const target = panel.querySelector(`#${CSS.escape(focusState.id)}`);
            if (target) {
                target.focus();
                try {
                    target.setSelectionRange(focusState.start, focusState.end);
                } catch (error) {
                    /* The replacement node is not selectable; focus is enough. */
                }
            }
        }
        return true;
    }

    /* What a Git action is allowed to still do when its answer arrives.

       The request itself is already bound -- its scope rode in the URL and the
       server acted on that scope -- so nothing here cancels or reissues it.
       What needs binding is everything the *answer* does, because none of the
       three things it addresses survives an await on its own: `terminals[i]`
       is a grid slot and a group switch rehouses it, the session id goes with
       it, and Follow browsing moves the captured pane's own scope while the
       request is in flight. The three are one identity, and the two ways it
       can break need different answers -- hence a state rather than a
       boolean. */
    const EXPLORER_GIT_IDENTITY_CURRENT = 'current';
    const EXPLORER_GIT_IDENTITY_SCOPE_CHANGED = 'scope-changed';
    const EXPLORER_GIT_IDENTITY_PANE_REPLACED = 'pane-replaced';

    function explorerGitCaptureIdentity(index) {
        const pane = terminals[index];
        return {
            pane,
            sessionId: sessionIds[index],
            scopePath: explorerGitScopePath(pane),
            scopeKind: explorerGitScopeKind(pane)
        };
    }

    function explorerGitIdentityState(index, identity) {
        if (!identity || !identity.pane) {
            return EXPLORER_GIT_IDENTITY_PANE_REPLACED;
        }
        if (terminals[index] !== identity.pane || sessionIds[index] !== identity.sessionId) {
            return EXPLORER_GIT_IDENTITY_PANE_REPLACED;
        }
        if (
            explorerGitScopePath(identity.pane) !== identity.scopePath
            || explorerGitScopeKind(identity.pane) !== identity.scopeKind
        ) {
            return EXPLORER_GIT_IDENTITY_SCOPE_CHANGED;
        }
        return EXPLORER_GIT_IDENTITY_CURRENT;
    }

    function explorerGitIdentityIsCurrent(index, identity) {
        return explorerGitIdentityState(index, identity) === EXPLORER_GIT_IDENTITY_CURRENT;
    }

    /* The mutation landed; only its answer is stale. Deliberately *not*
       invalidateExplorerGitRepo(): that also drops `_explorerGitRepo`, which
       is the model the pane's panel is still painted from -- blanking a
       background pane's sidebar to report that something else finished is a
       worse answer than a slightly old one. The pane simply stops counting as
       loaded, so the next load refetches instead of short-circuiting on the
       anchor path it already holds, and `_explorerGitReloadPending` is what
       gets that load run when the pane comes back on screen. */
    function explorerGitMarkModelStale(pane) {
        if (!pane) {
            return;
        }
        pane._explorerGitRepoLoaded = false;
        pane._explorerGitAnchorPath = '';
        pane._explorerGitAnchorKind = 'dir';
        pane._explorerGitAnchorLimit = 0;
        pane._explorerGitReloadPending = true;
    }

    async function performExplorerGitAction(index, endpoint, body) {
        const identity = explorerGitCaptureIdentity(index);
        const { pane, sessionId, scopePath, scopeKind } = identity;
        if (!pane || !sessionId || pane._explorerGitActionBusy) {
            return false;
        }
        pane._explorerGitActionBusy = true;
        pane._explorerGitRepoError = '';
        renderExplorerGitPanels(index);
        let succeeded = false;
        try {
            const response = await fetch(explorerGitRequestUrl(
                sessionId,
                endpoint,
                scopePath,
                explorerGitCommitLimitParams(pane),
                scopeKind
            ), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body || {}),
            });
            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.error || 'Git action failed');
            }
            if (explorerGitIdentityIsCurrent(index, identity)) {
                pane._explorerGitRepo = data;
                pane._explorerGitRepoLoaded = true;
                explorerGitNoteLoadedScope(
                    pane, explorerGitScopeIdentity(scopePath), scopeKind
                );
                pane._explorerGitRevision = typeof data.revision === 'string' ? data.revision : '';
                // A successful GridVibe Git action is authoritative: it re-arms a
                // suspended change-listener watch and resets its baseline.
                pane._explorerGitWatchSuspended = false;
                syncExplorerTabGitFromRepo(index, data);
                succeeded = true;
            } else {
                explorerGitMarkModelStale(pane);
            }
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer Git action failed:', error);
            // The error belongs to the scope it was raised for. Painting it onto
            // a pane that has since moved would attach it to a repository model
            // the user never asked this of.
            if (explorerGitIdentityIsCurrent(index, identity)) {
                pane._explorerGitRepoError = error.message || 'Git action failed.';
            } else {
                explorerGitMarkModelStale(pane);
            }
        } finally {
            // Always released on the captured pane -- a busy flag left set is a
            // pane that can never act again -- but painted only where it is
            // still the pane on screen.
            pane._explorerGitActionBusy = false;
            const state = explorerGitIdentityState(index, identity);
            if (state !== EXPLORER_GIT_IDENTITY_PANE_REPLACED) {
                renderExplorerGitPanels(index);
                if (state === EXPLORER_GIT_IDENTITY_SCOPE_CHANGED && pane._explorerGitSidebarOpen) {
                    // Same pane, different scope: the fresh load is owed here
                    // and now, because this pane is the one on screen.
                    pane._explorerGitReloadPending = false;
                    loadExplorerGitRepo(index);
                }
            }
        }
        if (!succeeded || !explorerGitIdentityIsCurrent(index, identity)) {
            return succeeded;
        }
        if (pane._explorerMode === 'directory') {
            loadExplorerPane(index, null, { force: true, showLoading: false });
        }
        if (EXPLORER_GIT_WORKTREE_ENDPOINTS.has(endpoint)) {
            await refreshExplorerAfterGitAction(index, body && body.path ? String(body.path) : '');
        }
        return succeeded;
    }

    /* Git actions that change working-tree or index state — publish only talks
       to the remote, so it never needs the ISSUE-2026-034 refresh below. */
    const EXPLORER_GIT_WORKTREE_ENDPOINTS = new Set([
        'stage', 'unstage', 'revert', 'commit', 'stage-all', 'unstage-all', 'discard-all',
    ]);

    /* ISSUE-2026-034: after a mutating Git action the Files tree and the open
       file/diff must not go stale. The tree reload guards internally on
       pane._explorerTreeSidebarOpen; the open file re-fetches in place (which
       drops the cached diff and re-pulls it when a diff view is showing).
       Single-path actions only refresh the file they touched — bulk actions
       (commit / stage-all / discard-all) can affect any open path. */
    async function refreshExplorerAfterGitAction(index, actionPath) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        reloadExplorerTree(index);
        if (pane._explorerMode !== 'file' || !pane._explorerFilePath) {
            return;
        }
        if (actionPath && actionPath !== pane._explorerFilePath) {
            return;
        }
        pane._explorerDiffLoaded = false;
        pane._explorerDiffCacheKey = '';
        await openExplorerFile(index, pane._explorerFilePath, {
            showLoading: false,
            preserveScroll: true,
            tab: pane._explorerActiveTabId
        });
    }

    function explorerGitStageFile(index, path) {
        if (!path) {
            return;
        }
        performExplorerGitAction(index, 'stage', { path });
    }

    function explorerGitStageAll(index) {
        performExplorerGitAction(index, 'stage-all', {});
    }

    /* Bulk form of the per-row Revert (OD-1): worktree restore of tracked
       files only — staged content is preserved and untracked files are never
       deleted (no git clean). Irreversible, so it goes through the in-page
       confirm shell. */
    async function explorerGitDiscardAll(index) {
        const confirmed = await openGenericConfirmModal({
            title: 'Discard all changes?',
            copy: 'Discard unstaged changes in tracked files in the current Git scope?',
            note: 'Unstaged edits will be lost. Staged versions and untracked files are kept.',
            confirmLabel: 'Discard all',
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        performExplorerGitAction(index, 'discard-all', {});
    }

    function explorerGitUnstageFile(index, path) {
        if (!path) {
            return;
        }
        performExplorerGitAction(index, 'unstage', { path });
    }

    /* Bulk form of the per-row Unstage: index-only, so nothing in the
       worktree moves and Stage All puts it straight back — no confirm, unlike
       the irreversible Discard All beside it. */
    function explorerGitUnstageAll(index) {
        performExplorerGitAction(index, 'unstage-all', {});
    }

    /* Discarding working-tree edits is irreversible, so it goes through the
       in-page confirm shell (WebView2 blocks window.confirm) before the
       narrow discard route runs; a reverted open file reloads in place. */
    async function explorerGitRevertFile(index, path, status = '') {
        if (!path) {
            return;
        }
        const untracked = status === 'untracked';
        const confirmed = await openGenericConfirmModal({
            title: untracked ? 'Delete untracked file?' : 'Discard changes?',
            copy: untracked
                ? `Permanently delete the untracked file "${path}"?`
                : `Discard the unstaged changes in "${path}"?`,
            note: untracked
                ? 'This new file is not tracked by Git and cannot be restored after deletion.'
                : 'This unstaged edit will be lost. Any staged version of the file is kept.',
            confirmLabel: untracked ? 'Delete file' : 'Discard changes',
            danger: true,
        });
        if (!confirmed) {
            return;
        }
        /* A reverted open file reloads in place via the shared post-action
           refresh (ISSUE-2026-034). */
        performExplorerGitAction(index, 'revert', { path });
    }

    async function explorerGitCommit(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const message = String(pane._explorerGitCommitMessage || '').trim();
        if (!message) {
            pane._explorerGitRepoError = 'Commit message is required.';
            renderExplorerGitPanels(index);
            const input = document.getElementById(`explorer-git-commit-message-${index}`);
            if (input) {
                input.focus();
            }
            return;
        }
        const identity = explorerGitCaptureIdentity(index);
        const committed = await performExplorerGitAction(index, 'commit', { message });
        if (!committed) {
            return;
        }
        // The draft belongs to the pane, so it is cleared on the pane. The
        // render is slot work and waits on the same identity every other
        // post-await paint does.
        pane._explorerGitCommitMessage = '';
        if (explorerGitIdentityIsCurrent(index, identity)) {
            renderExplorerGitPanels(index);
        }
    }

    /* Publishing is outward-facing, so it confirms through the in-page shell
       (WebView2 blocks window.confirm — Regression Guardrail 4). */
    async function explorerGitPublish(index) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        const git = (pane._explorerGitRepo && pane._explorerGitRepo.git) || {};
        const branch = git.branch || 'this branch';
        const confirmed = await openGenericConfirmModal({
            title: 'Publish branch?',
            copy: `Publish ${branch} to its remote?`,
            confirmLabel: 'Publish',
        });
        if (!confirmed) {
            return;
        }
        performExplorerGitAction(index, 'publish', {});
    }
