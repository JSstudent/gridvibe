/* Explorer in-place editor — the highlight overlay's DOM half. Loaded after
   explorer-viewer.js (whose row renderer it reuses) and before
   explorer-editor.js, which owns edit state and calls in here through three
   optional hooks.

   Entering edit mode used to replace the rendered, numbered Source rows with a
   bare textarea in one frame: the colours and the gutter vanished and every
   glyph jumped left by the gutter's width and down by the textarea's 8px of
   padding. The overlay keeps the rows — painted *behind* a textarea whose own
   text and background are transparent — so the geometry never moves and the
   syntax colours are the read-only view's, because they come from the read-only
   view's renderer. The textarea still does all the real editing: edit state,
   the save/conflict flow and every unsaved-work guard are untouched, and
   `state.draft` is still `textarea.value`.

   Which element scrolls is the decision that keeps this small. The textarea is
   absolutely positioned over the whole stack with `overflow: hidden`, so it is
   exactly as tall as its own content and has no scroll of its own; the
   surrounding `.explorer-source-view` scrolls both layers together, exactly as
   it does in read-only mode. The caret follows typing because the browser
   scrolls the nearest scrollable ancestor, and there is no scroll-sync code to
   drift.

   The overlay is an enhancement, never a precondition: when it stands down —
   an oversized buffer, or this file failing to load at all — the panel holds
   exactly the bare textarea it always did. Nothing here is persisted; the
   editor stays transient in-memory. */

/* The decisions live in the DOM-free policy module so they can be executed by
   Node tests. Missing means the overlay simply never engages. */
function explorerEditOverlayPolicy() {
    return window.GridVibeExplorerEditHighlight || null;
}

/* Matches explorerSourceLineRecords()'s count — a trailing newline opens a
   final empty line — without building the records twice per refresh. */
function explorerEditLineCount(draft) {
    const text = String(draft == null ? '' : draft);
    let lines = 1;
    for (let at = text.indexOf('\n'); at !== -1; at = text.indexOf('\n', at + 1)) {
        lines += 1;
    }
    return lines;
}

/* The language the read-only Source view would render this file in — the same
   expression renderExplorerSource() uses, so the underlay colours the draft
   exactly as the panel it is standing in for coloured the file. A file past
   the plain-preview threshold carries no language there and carries none here
   either. */
function explorerEditLanguage(pane) {
    if (!pane || pane._explorerFilePlain) {
        return '';
    }
    return pane._explorerFileLanguage || '';
}

/* The same arithmetic the rendered rows use, hoisted onto the stack: the
   textarea's left padding is this plus the code cell's own 14px, which is what
   puts its first glyph exactly on the underlay's code column. The rows inherit
   it here too, so both layers move together when a new digit appears.

   Markdown reserves the fold chevron's width even though the underlay renders
   no fold buttons, because that is what the read-only view reserved — the
   gutter has to survive the transition at the width it already had. */
function explorerEditGutterWidthCss(draft, language) {
    return explorerSourceGutterWidthCss(
        explorerEditLineCount(draft),
        normalizeExplorerLanguage(language) === 'markdown'
    );
}

/* The read-only renderer, called as the viewer calls it bar three deliberate
   arguments:

   * no search ranges — the search chrome is disabled while editing anyway;
   * no fold controls — a focusable control under a covering textarea is an
     unclickable tab trap, and folding a buffer being typed into is incoherent
     regardless; and
   * no token cache of its own. The viewer memoizes its map on
     `pane._explorerHighlightCache` because a file's content rarely moves; a
     draft moves on every keystroke, so a slot for it would never hit — and
     writing drafts into *that* slot would evict the entry which currently
     makes the post-save re-render free. `undefined` is the renderer's existing
     "tokenize this yourself" argument (an explicit `null` means a cached
     miss), so a moved draft tokenizes and the viewer's cache is untouched.

   The tokenizing pass is a whole-document one, which is why this is only ever
   reached through the rAF-coalesced refresh below and only under the viability
   bound. */
function explorerEditUnderlayHtml(draft, language, runs) {
    return renderExplorerSourceLines(
        draft, language, [], new Set(), runs, { foldControls: false }
    );
}

/* The one moment the viewer's cache legitimately answers for a draft: at
   mount the draft still *is* the file, and the Source view this overlay is
   standing in for was rendered from exactly that content+language pair — so
   entering edit mode costs no tokenizing at all.

   The equality guard is what keeps it honest. A CRLF file's draft has been
   newline-normalized and is a different string; handing that to the cache
   would rewrite the entry under the file's own key and cost the post-save
   re-render its free hit. Such a draft simply tokenizes like any other. */
function explorerEditMountRuns(pane, draft, language) {
    if (!pane || draft !== pane._explorerFileContent) {
        return undefined;
    }
    return explorerHighlightDocumentLinesCached(
        pane, draft, normalizeExplorerLanguage(language)
    );
}

function explorerEditOverlayViable(draft) {
    const policy = explorerEditOverlayPolicy();
    if (!policy) {
        return false;
    }
    /* The underlay *is* the read-only row renderer, run again on the live
       draft every animation frame. A buffer the viewer already refuses to
       build rows for once is not one to rebuild sixty times a second, so the
       presentation tier stands the overlay down before its own byte cap is
       even consulted; the editor falls back to a bare textarea exactly as it
       does above 2 MiB. */
    if (window.GridVibeExplorerTiers?.sourceTierForContent(draft) === 'large') {
        return false;
    }
    return policy.overlayViability({
        contentLength: String(draft == null ? '' : draft).length,
        maxContentLength: EXPLORER_PLAIN_PREVIEW_THRESHOLD
    }).enabled;
}

/* Install the editor's textarea into the Source panel, wrapped in the overlay
   stack when the buffer is a candidate for it. Returns nothing: the editor's
   own lookups find the textarea by id either way. */
function mountExplorerEditOverlay(index, code, textareaHtml) {
    const pane = terminals[index];
    const state = pane && pane._explorerEdit;
    if (!code || !state) {
        return;
    }
    const draft = String(state.draft == null ? '' : state.draft);
    if (!explorerEditOverlayViable(draft)) {
        // Exactly today's edit mode: a full-height textarea that scrolls
        // itself. Silent, per the enhancement contract.
        code.innerHTML = textareaHtml;
        return;
    }
    const language = explorerEditLanguage(pane);

    code.innerHTML = `
        <div
            class="explorer-edit-stack"
            data-explorer-edit-stack="${index}"
            style="--explorer-source-gutter-width: ${explorerEditGutterWidthCss(draft, language)};"
        >
            <div class="explorer-edit-underlay" aria-hidden="true">${explorerEditUnderlayHtml(draft, language, explorerEditMountRuns(pane, draft, language))}</div>
            ${textareaHtml}
        </div>
    `;
    pane._explorerEditOverlayDraft = draft;

    /* The stacked textarea is as tall as the whole document, so its own focus
       outline would be drawn around the buffer and never seen. The ring moves
       to the scroller — the box the user actually looks at — by class rather
       than :has(), so it does not ride on the runtime's selector support. */
    const view = document.getElementById(`explorer-code-${index}`);
    const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
    if (!view || !textarea) {
        return;
    }
    textarea.addEventListener('focus', () => view.classList.add('editor-focused'));
    textarea.addEventListener('blur', () => view.classList.remove('editor-focused'));
}

function paintExplorerEditUnderlay(index) {
    const pane = terminals[index];
    const state = pane && pane._explorerEdit;
    const stack = document.querySelector(`[data-explorer-edit-stack="${index}"]`);
    const underlay = stack && stack.querySelector('.explorer-edit-underlay');
    if (!state || !underlay) {
        return;
    }
    const draft = String(state.draft == null ? '' : state.draft);
    const language = explorerEditLanguage(pane);
    stack.style.setProperty(
        '--explorer-source-gutter-width', explorerEditGutterWidthCss(draft, language)
    );
    underlay.innerHTML = explorerEditUnderlayHtml(draft, language);
    pane._explorerEditOverlayDraft = draft;
    /* These rows are new nodes, so every range the find and the occurrence
       tint had painted onto the old ones is now detached. explorer-edit-find.js
       drops them and re-derives from the draft that is now on screen. */
    window.repaintExplorerEditFind?.(index);
}

/* Called from the editor's existing `input` handler, which covers typing, Tab
   insertion, paste and both dictation insertion paths. A rebuild is a whole
   document render, so it is coalesced into one animation frame (never an
   interval, never a busy wait) and skipped outright when the draft has not
   moved since the last paint. */
function refreshExplorerEditOverlay(index) {
    const pane = terminals[index];
    const state = pane && pane._explorerEdit;
    const policy = explorerEditOverlayPolicy();
    const stack = document.querySelector(`[data-explorer-edit-stack="${index}"]`);
    if (!state || !policy || !stack) {
        return;
    }
    const decision = policy.refreshDecision({
        enabled: true,
        pending: Boolean(pane._explorerEditOverlayFrame),
        draft: state.draft,
        rendered: pane._explorerEditOverlayDraft
    });
    if (decision !== 'schedule') {
        return;
    }
    if (typeof window.requestAnimationFrame !== 'function') {
        paintExplorerEditUnderlay(index);
        return;
    }
    pane._explorerEditOverlayFrame = window.requestAnimationFrame(() => {
        pane._explorerEditOverlayFrame = 0;
        paintExplorerEditUnderlay(index);
    });
}

/* One added line in clearExplorerEditState() — the choke point every teardown
   route already funnels through — releases the queued frame and the focus
   ring. The stack itself needs no removal: every exit either rebuilds the
   Source panel or discards the card wholesale. */
function teardownExplorerEditOverlay(index) {
    const pane = terminals[index];
    if (pane) {
        if (pane._explorerEditOverlayFrame && typeof window.cancelAnimationFrame === 'function') {
            window.cancelAnimationFrame(pane._explorerEditOverlayFrame);
        }
        pane._explorerEditOverlayFrame = 0;
        pane._explorerEditOverlayDraft = '';
    }
    // The find's paint and its cached ranges were resolved against a draft
    // that is about to stop existing.
    window.clearExplorerEditFind?.(index);
    document.getElementById(`explorer-code-${index}`)?.classList.remove('editor-focused');
}
