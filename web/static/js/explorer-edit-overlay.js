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

   The tokenizing pass is a whole-document one, which is why this is now only
   reached at mount (where the viewer's cache answers for the draft) and from
   the settle pass a few frames after typing stops. The frames in between
   splice the rows the draft actually moved — see
   spliceExplorerEditUnderlayRows() — because running this on every keystroke
   made the cost of typing O(document) rather than O(edit). */
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
    pane._explorerEditOverlayLines = explorerEditUnderlayLines(draft);

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

/* The line texts the rows are built from, in the renderer's own terms (a
   trailing newline opens a final empty line, a stray CR is not part of the
   line) — so a splice compares like with like against what is on screen. */
function explorerEditUnderlayLines(draft) {
    return explorerSourceLineRecords(String(draft == null ? '' : draft))
        .map(record => record.text);
}

function explorerEditRepaintPolicy() {
    return (typeof window !== 'undefined' && window.GridVibeExplorerRepaint) || null;
}

/* Replace the rows the draft actually moved, and renumber whatever the change
   in line count shifted. Returns false when the DOM is not the shape the plan
   assumed, which sends the caller back to the full rebuild.

   The replaced rows are coloured by the per-line fallback lexer rather than by
   a whole-document Highlight.js pass: tokenizing the document is the cost this
   exists to avoid, and it cannot be done for one line — a line's colour
   depends on the block it sits in. The settle pass below repaints the whole
   underlay with real tokens once typing stops, so the interim colour lives on
   the line under the caret for a fraction of a second. Where the file has no
   Highlight.js grammar at all, the two passes agree exactly. */
function spliceExplorerEditUnderlayRows(underlay, model, plan) {
    const container = underlay.querySelector('.explorer-source-lines');
    if (!container || container.children.length !== plan.before) {
        return false;
    }
    const html = [];
    for (let at = plan.start; at < plan.start + plan.inserted; at += 1) {
        html.push(explorerSourceRowHtml(model, model.rows[at], []));
    }
    const host = document.createElement('div');
    host.innerHTML = html.join('');
    const fresh = Array.from(host.children);
    for (let removed = 0; removed < plan.removed; removed += 1) {
        container.children[plan.start]?.remove();
    }
    const anchor = container.children[plan.start] || null;
    fresh.forEach(row => container.insertBefore(row, anchor));

    if (plan.inserted !== plan.removed) {
        for (let at = plan.start + plan.inserted; at < container.children.length; at += 1) {
            const row = container.children[at];
            row.dataset.explorerLine = String(at + 1);
            const gutter = row.firstElementChild;
            if (gutter) {
                gutter.textContent = String(at + 1);
            }
        }
    }
    return true;
}

/* `full` forces the whole-document rebuild: the settle pass asks for it, and
   so does anything the splice cannot express (a first paint, a change too wide
   to be a splice, a container that is not the shape the plan assumed). */
function paintExplorerEditUnderlay(index, { full = false } = {}) {
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

    /* Built with a null token map — "no Highlight.js runs, lex each line" —
       because the splice path must not tokenize. The full path swaps a real
       map in below, having paid for it once rather than once per frame. */
    const model = explorerSourceRowModel(draft, language, new Set(), null, { foldControls: false });
    const lines = model.records.map(record => record.text);
    const policy = explorerEditRepaintPolicy();
    const plan = (!full && policy)
        ? policy.lineSplicePlan(pane._explorerEditOverlayLines, lines)
        : { mode: 'full' };
    if (plan.mode === 'none') {
        return;
    }

    let spliced = false;
    if (plan.mode === 'splice') {
        spliced = spliceExplorerEditUnderlayRows(underlay, model, {
            ...plan,
            before: (pane._explorerEditOverlayLines || []).length
        });
    }
    if (!spliced) {
        cancelExplorerEditUnderlaySettle(pane);
        underlay.innerHTML = explorerEditUnderlayHtml(draft, language);
    } else {
        scheduleExplorerEditUnderlaySettle(index);
    }
    pane._explorerEditOverlayDraft = draft;
    pane._explorerEditOverlayLines = lines;
    /* The rows this touched are new nodes, so every range the find and the
       occurrence tint had painted onto the ones they replaced is now detached.
       explorer-edit-find.js drops them and re-derives from the draft that is
       now on screen. */
    window.repaintExplorerEditFind?.(index);
}

/* How long the underlay may show fallback colours before it repaints with real
   Highlight.js tokens. A one-shot debounce, never an interval: it is armed by
   a splice and disarmed by the next full paint or by teardown. */
const EXPLORER_EDIT_UNDERLAY_SETTLE_MS = 180;

function cancelExplorerEditUnderlaySettle(pane) {
    if (pane && pane._explorerEditOverlaySettle) {
        window.clearTimeout(pane._explorerEditOverlaySettle);
        pane._explorerEditOverlaySettle = 0;
    }
}

function scheduleExplorerEditUnderlaySettle(index) {
    const pane = terminals[index];
    if (!pane) {
        return;
    }
    cancelExplorerEditUnderlaySettle(pane);
    pane._explorerEditOverlaySettle = window.setTimeout(() => {
        pane._explorerEditOverlaySettle = 0;
        if (pane._explorerEdit) {
            paintExplorerEditUnderlay(index, { full: true });
        }
    }, EXPLORER_EDIT_UNDERLAY_SETTLE_MS);
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
        pane._explorerEditOverlayLines = null;
        cancelExplorerEditUnderlaySettle(pane);
    }
    // The find's paint and its cached ranges were resolved against a draft
    // that is about to stop existing.
    window.clearExplorerEditFind?.(index);
    document.getElementById(`explorer-code-${index}`)?.classList.remove('editor-focused');
}
