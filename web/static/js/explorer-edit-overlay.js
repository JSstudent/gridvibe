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

/* The pane's own line-record slot. The draft moves on every keystroke, so its
   records never hit the page-wide LRU and could only evict what does — see
   explorerSourceLineRecords(). */
function explorerEditRecordCache(pane) {
    if (!pane) {
        return null;
    }
    if (!pane._explorerEditRecordCache) {
        pane._explorerEditRecordCache = { source: null, records: null };
    }
    return pane._explorerEditRecordCache;
}

/* The read-only view's row model, resolved as the viewer resolves it bar three
   deliberate arguments:

   * no fold controls — a focusable control under a covering textarea is an
     unclickable tab trap, and folding a buffer being typed into is incoherent
     regardless;
   * the pane's own record slot rather than the shared one, per above; and
   * no token cache of its own. The viewer memoizes its map on
     `pane._explorerHighlightCache` because a file's content rarely moves; a
     draft moves on every keystroke, so a slot for it would never hit — and
     writing drafts into *that* slot would evict the entry which currently
     makes the post-save re-render free. `undefined` is the renderer's existing
     "tokenize this yourself" argument (an explicit `null` means a cached
     miss), so a moved draft tokenizes and the viewer's cache is untouched.

   The tokenizing pass is a whole-document one, which is why a real token map
   only reaches here at mount (where the viewer's cache answers for the draft)
   and from the settle pass a few frames after typing stops. The frames in
   between splice the rows the draft actually moved — see
   spliceExplorerEditUnderlayRows() — because tokenizing on every keystroke
   made the cost of typing O(document) rather than O(edit). */
function explorerEditUnderlayModel(pane, draft, language, runs) {
    return explorerSourceRowModel(draft, language, new Set(), runs, {
        foldControls: false,
        recordCache: explorerEditRecordCache(pane)
    });
}

/* renderExplorerSourceLines()'s body with the model hoisted out, because the
   paint keys below are read off the same model — building it twice to emit the
   rows once is a whole-document walk for nothing. Rows still come from
   explorerSourceRowHtml(), so a row emitted here is byte-identical to the same
   row emitted in bulk or one at a time anywhere else.

   The empty search ranges are the fourth deliberate argument: the search
   chrome is disabled while editing, and the editor's own find paints through
   the CSS Custom Highlight API instead of into the markup. The paint key below
   depends on that. */
function explorerEditUnderlayRowsHtml(model) {
    return `${explorerSourceLinesOpenTag(model)}${
        model.rows.map(row => explorerSourceRowHtml(model, row, [])).join('')
    }</div>`;
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
    const normalizedLanguage = normalizeExplorerLanguage(language);
    const cache = pane._explorerHighlightCache;
    if (cache && cache.content === draft && cache.language === normalizedLanguage) {
        return cache.lines;
    }
    /* The Source worker may still be tokenizing when Edit is pressed. Do not
       undo Phase 2 by running the same large pass synchronously at mount; the
       underlay starts in fallback colour and its normal settle pass fills the
       whole-document colours in. */
    if (explorerEditWorkerEligible(draft, normalizedLanguage)) {
        return null;
    }
    return explorerHighlightDocumentLinesCached(
        pane, draft, normalizedLanguage
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
    const mountRuns = explorerEditMountRuns(pane, draft, language);
    const model = explorerEditUnderlayModel(pane, draft, language, mountRuns);

    code.innerHTML = `
        <div
            class="explorer-edit-stack"
            data-explorer-edit-stack="${index}"
            style="--explorer-source-gutter-width: ${explorerEditGutterWidthCss(draft, language)};"
        >
            <div class="explorer-edit-underlay" aria-hidden="true">${explorerEditUnderlayRowsHtml(model)}</div>
            ${textareaHtml}
        </div>
    `;
    pane._explorerEditOverlayDraft = draft;
    /* The line texts in the renderer's own terms (a trailing newline opens a
       final empty line, a stray CR is not part of the line), so a splice
       compares like with like against what is on screen. */
    pane._explorerEditOverlayLines = model.records.map(record => record.text);
    /* What the rows on screen are actually coloured with, so the first settle
       repaints only what its answer moves. Null when the mount had no real
       runs to paint (the worker is still tokenizing): every row is then an
       interim fallback colour and the settle rebuilds in one pass. */
    pane._explorerEditUnderlayPaintKeys = explorerEditUnderlayPaintKeys(model);

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
    if (mountRuns === null && explorerEditWorkerEligible(draft, normalizeExplorerLanguage(language))) {
        scheduleExplorerEditUnderlaySettle(index);
    }
}

function explorerEditRepaintPolicy() {
    return (typeof window !== 'undefined' && window.GridVibeExplorerRepaint) || null;
}

/* One row's paint key: what explorerSourceRowCodeHtml() would emit for it,
   reduced to the two things that can differ between two settles of the same
   draft — the canonical (length, class name) sequence of its Highlight.js
   runs, and its Markdown heading level, because a heading row is wrapped in a
   span and a plain one is not.

   Absolute run offsets are deliberately absent, and that is legal *only here*.
   The underlay always renders with empty search ranges —
   explorerEditUnderlayRowsHtml() and spliceExplorerEditUnderlayRows() both
   pass `[]`, and the find paints through the CSS Custom Highlight API rather
   than into the markup — so `run.start` never reaches the output. Inserting one character shifts every
   offset below it while a preserved row's markup is unchanged, which is
   exactly the comparison this key has to get right. The read-only Source view
   *does* render its ranges into the markup, so this key would be wrong there.

   The numeric worker class id is absent for its own reason: ids are assigned
   in first-encounter order, so they are not comparable across two answers.
   HighlightLines.lineKey() reads the class names off the typed arrays without
   materializing a single run. */
function explorerEditRowPaintKey(runs, row) {
    const line = row.record.number;
    let key = '';
    if (typeof runs.lineKey === 'function') {
        key = runs.lineKey(line);
    } else {
        (runs.get(line) || []).forEach(run => {
            key += `${String(run.text || '').length}:${run.className || ''}|`;
        });
    }
    return `${row.headingLevel || 0}#${key}`;
}

/* The keys for every row a model would paint, or null when it carries no
   Highlight.js runs at all — those rows are coloured by the per-line fallback
   lexer, which is an interim state the next settle has to repaint whatever
   their run shapes look like. */
function explorerEditUnderlayPaintKeys(model) {
    if (!model || !model.runs) {
        return null;
    }
    return model.rows.map(row => explorerEditRowPaintKey(model.runs, row));
}

/* The splice, applied to the paint keys as well as to the rows: the preserved
   prefix and the shifted suffix keep their keys because they kept their nodes,
   and every row the splice built gets the unknown sentinel. That is what
   represents the real hybrid surface — fallback-coloured rows standing beside
   preserved Highlight.js ones — and it is why replacing one identifier
   character with another still repaints that row at settle even though its run
   shape never moved. */
function spliceExplorerEditUnderlayPaintKeys(keys, plan, rows) {
    if (!Array.isArray(keys)) {
        return null;
    }
    const next = keys.slice(0, plan.start)
        .concat(new Array(plan.inserted).fill(null))
        .concat(keys.slice(plan.start + plan.removed));
    return next.length === rows ? next : null;
}

/* The settle's paint when the rows on screen are already this draft's rows:
   replace the code cells whose colour actually changed and leave every other
   node standing. Returns false when it cannot — no comparable keys, or a delta
   wide enough that one bulk write is cheaper — which sends the caller back to
   the whole-underlay write it has always done. */
function repaintExplorerEditUnderlayColours(pane, underlay, model) {
    const policy = explorerEditRepaintPolicy();
    if (!policy?.highlightRepaintPlan || !model.runs) {
        return false;
    }
    const container = underlay.querySelector('.explorer-source-lines');
    if (!container || container.children.length !== model.rows.length) {
        return false;
    }
    const nextKeys = explorerEditUnderlayPaintKeys(model);
    const plan = policy.highlightRepaintPlan({
        previousKeys: pane._explorerEditUnderlayPaintKeys,
        nextKeys,
        rowCount: model.rows.length
    });
    if (plan.mode === 'full') {
        return false;
    }
    /* Resolved before anything is written: the underlay's rows are contiguous
       (its fold controls are suppressed, so no row is ever omitted) and the
       length matched above, but a half-applied repaint would be worse than a
       rebuild. */
    const cells = [];
    for (let at = 0; at < plan.lines.length; at += 1) {
        const cell = container.children[plan.lines[at] - 1]?.querySelector(':scope > code');
        if (!cell) {
            return false;
        }
        cells.push(cell);
    }
    plan.lines.forEach((line, at) => {
        cells[at].innerHTML = explorerSourceRowCodeHtml(model, model.rows[line - 1], []);
    });
    pane._explorerEditUnderlayPaintKeys = nextKeys;
    return true;
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

/* `full` means "this paint may use real Highlight.js runs": the settle pass
   asks for it. It is no longer a synonym for the whole-document rebuild — when
   the rows on screen are already this draft's rows, the settle repaints only
   the code cells whose colour moved, which for an ordinary keystroke is the
   one row the splice inserted. The rebuild remains the answer for everything
   the splice cannot express (a first paint, a change too wide to be a splice,
   a container that is not the shape the plan assumed) and for a colour delta
   wide enough that one bulk write is cheaper. */
function paintExplorerEditUnderlay(index, { full = false, runs } = {}) {
    const pane = terminals[index];
    const state = pane && pane._explorerEdit;
    const stack = document.querySelector(`[data-explorer-edit-stack="${index}"]`);
    const underlay = stack && stack.querySelector('.explorer-edit-underlay');
    if (!state || !underlay) {
        return;
    }
    const draft = String(state.draft == null ? '' : state.draft);
    const language = explorerEditLanguage(pane);
    if (pane._explorerEditHighlightPending?.draft !== draft) {
        cancelExplorerEditHighlight(pane);
    }
    stack.style.setProperty(
        '--explorer-source-gutter-width', explorerEditGutterWidthCss(draft, language)
    );

    /* The splice path's model is built with a null token map — "no
       Highlight.js runs, lex each line" — because it must not tokenize. The
       settle path was handed a real one (or, for a buffer the worker never
       took, tokenizes once here) and paints colours from it. */
    const model = explorerEditUnderlayModel(pane, draft, language, full ? runs : null);
    const lines = model.records.map(record => record.text);
    const policy = explorerEditRepaintPolicy();
    const plan = policy
        ? policy.lineSplicePlan(pane._explorerEditOverlayLines, lines)
        : { mode: 'full' };
    if (!full && plan.mode === 'none') {
        return;
    }

    let painted = false;
    if (full) {
        /* `none` is the settle's own precondition restated in the splice
           plan's terms: the rows on screen are this draft's rows already, so
           the only thing the answer can move is their colour. */
        if (plan.mode === 'none') {
            painted = repaintExplorerEditUnderlayColours(pane, underlay, model);
        }
    } else if (plan.mode === 'splice') {
        painted = spliceExplorerEditUnderlayRows(underlay, model, {
            ...plan,
            before: (pane._explorerEditOverlayLines || []).length
        });
        if (painted) {
            pane._explorerEditUnderlayPaintKeys = spliceExplorerEditUnderlayPaintKeys(
                pane._explorerEditUnderlayPaintKeys, plan, lines.length
            );
        }
    }
    if (!painted) {
        /* A wide paste still needs an immediate geometry-correct paint, but it
           does not need a main-thread whole-document tokenization. The edit is
           shown with fallback colour now and the same settle path as a small
           splice supplies real runs after the worker (or the small-file sync
           path) finishes. */
        underlay.innerHTML = explorerEditUnderlayRowsHtml(model);
        pane._explorerEditUnderlayPaintKeys = explorerEditUnderlayPaintKeys(model);
    }
    if (full) {
        cancelExplorerEditUnderlaySettle(pane);
    } else {
        scheduleExplorerEditUnderlaySettle(index);
    }
    pane._explorerEditOverlayDraft = draft;
    pane._explorerEditOverlayLines = lines;
    /* Every node this replaced — a spliced row, a repainted code cell, the
       whole underlay — detached the live Ranges the find and the occurrence
       tint had painted onto it, while ranges on the rows it left alone
       survive. explorer-edit-find.js drops the lot and re-derives from the
       draft that is now on screen: that is one scan of the buffer, and
       narrowing it to match a partial paint would have to reason about which
       Ranges are still attached, which is exactly what it cannot see. */
    window.repaintExplorerEditFind?.(index);
}

/* How long the underlay may show fallback colours before it repaints with real
   Highlight.js tokens. A one-shot debounce, never an interval: it is armed by
   a splice and disarmed by the next full paint or by teardown. */
const EXPLORER_EDIT_UNDERLAY_SETTLE_MS = 180;

function explorerEditWorkerEligible(draft, normalizedLanguage) {
    const workers = (typeof window !== 'undefined' && window.GridVibeExplorerWorkers) || null;
    const grammar = EXPLORER_HLJS_LANGUAGE[normalizedLanguage];
    return Boolean(grammar && workers?.canHighlight?.(String(draft == null ? '' : draft)));
}

function cancelExplorerEditHighlight(pane) {
    if (!pane?._explorerEditHighlightPending) {
        return;
    }
    pane._explorerEditHighlightPending = null;
    cancelExplorerRequestSlot(pane, 'editHighlight');
}

function settleExplorerEditUnderlay(index) {
    const pane = terminals[index];
    const state = pane?._explorerEdit;
    if (!state) {
        return;
    }
    const draft = String(state.draft == null ? '' : state.draft);
    const language = explorerEditLanguage(pane);
    const normalizedLanguage = normalizeExplorerLanguage(language);
    const workers = (typeof window !== 'undefined' && window.GridVibeExplorerWorkers) || null;
    const grammar = EXPLORER_HLJS_LANGUAGE[normalizedLanguage];
    if (!grammar || !workers?.canHighlight?.(draft)) {
        paintExplorerEditUnderlay(index, { full: true });
        return;
    }
    const previous = pane._explorerEditHighlightPending;
    if (previous && previous.draft === draft && previous.language === normalizedLanguage) {
        return;
    }
    cancelExplorerEditHighlight(pane);
    const pending = { draft, language: normalizedLanguage };
    pane._explorerEditHighlightPending = pending;
    workers.highlight(draft, grammar, {
        signal: explorerRequestSignal(pane, 'editHighlight')
    }).then(runs => {
        if (pane._explorerEditHighlightPending !== pending) {
            return;
        }
        pane._explorerEditHighlightPending = null;
        if (!pane._explorerEdit || String(pane._explorerEdit.draft || '') !== draft) {
            return;
        }
        paintExplorerEditUnderlay(index, { full: true, runs });
    }).catch(error => {
        if (pane._explorerEditHighlightPending !== pending) {
            return;
        }
        pane._explorerEditHighlightPending = null;
        if (!explorerIsAbortError(error)) {
            console.error('[GridVibe Sessions] Explorer edit highlight worker failed:', error);
        }
        /* Fallback-coloured rows are already on screen and stay usable. */
    });
}

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
            settleExplorerEditUnderlay(index);
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
        pane._explorerEditUnderlayPaintKeys = null;
        pane._explorerEditRecordCache = null;
        cancelExplorerEditUnderlaySettle(pane);
        cancelExplorerEditHighlight(pane);
    }
    // The find's paint and its cached ranges were resolved against a draft
    // that is about to stop existing.
    window.clearExplorerEditFind?.(index);
    document.getElementById(`explorer-code-${index}`)?.classList.remove('editor-focused');
}
