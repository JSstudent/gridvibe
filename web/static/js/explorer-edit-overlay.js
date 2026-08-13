/* Explorer in-place editor — the highlight overlay's DOM half. Loaded after
   explorer-viewer.js (whose row renderer it reuses) and before
   explorer-editor.js, which owns edit state and calls in here through three
   optional hooks.

   Entering edit mode used to replace the rendered, numbered Source rows with a
   bare textarea in one frame: the gutter vanished and every glyph jumped left
   by the gutter's width and down by the textarea's 8px of padding. The overlay
   keeps the rows — painted *behind* a textarea with a transparent background —
   so the geometry never moves. The textarea still does all the real editing:
   edit state, the save/conflict flow and every unsaved-work guard are
   untouched, and `state.draft` is still `textarea.value`.

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

/* The same arithmetic the rendered rows use, hoisted onto the stack: the
   textarea's left padding is this plus the code cell's own 14px, which is what
   puts its first glyph exactly on the underlay's code column. The rows inherit
   it here too, so both layers move together when a new digit appears. */
function explorerEditGutterWidthCss(draft) {
    return explorerSourceGutterWidthCss(explorerEditLineCount(draft), false);
}

/* The read-only renderer, called as the viewer calls it bar two deliberate
   arguments:

   * no search ranges — the search chrome is disabled while editing anyway; and
   * no language — which suppresses the Markdown fold <button>s. A focusable
     control under a covering textarea is an unclickable tab trap, and folding
     a buffer being typed into is incoherent regardless. It also means no
     tokenizer runs, so this stage costs no per-keystroke highlighting.

   Stage 1 shows the gutter only: the textarea keeps its own glyphs and CSS
   paints the underlay's code column transparent. */
function explorerEditUnderlayHtml(draft) {
    return renderExplorerSourceLines(draft, '', [], new Set(), null);
}

function explorerEditOverlayViable(draft) {
    const policy = explorerEditOverlayPolicy();
    if (!policy) {
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

    code.innerHTML = `
        <div
            class="explorer-edit-stack"
            data-explorer-edit-stack="${index}"
            style="--explorer-source-gutter-width: ${explorerEditGutterWidthCss(draft)};"
        >
            <div class="explorer-edit-underlay" aria-hidden="true">${explorerEditUnderlayHtml(draft)}</div>
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
    stack.style.setProperty('--explorer-source-gutter-width', explorerEditGutterWidthCss(draft));
    underlay.innerHTML = explorerEditUnderlayHtml(draft);
    pane._explorerEditOverlayDraft = draft;
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
    document.getElementById(`explorer-code-${index}`)?.classList.remove('editor-focused');
}
