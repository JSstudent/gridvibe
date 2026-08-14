/* Explorer in-place editor — find and the occurrence tint, painted onto the
   highlight overlay's rows. Loaded after explorer-edit-overlay.js (whose
   underlay it paints) and before explorer-editor.js; explorer-viewer.js
   delegates to it from applyExplorerSearch() while an edit is open.

   Edit mode used to disable the find widget outright, because there was
   nothing to find in: the Source panel held a bare textarea. With the overlay
   there are real rendered rows behind the caret, and both things the reader
   lost — the in-file find and the selected-word occurrence tint — can be put
   back on them.

   Neither is painted the way the read-only view paints it. There the find
   wraps matches in `<mark class="explorer-search-match">`, and that mark
   carries a pixel of horizontal padding on each side. In the read-only view
   that padding is decoration; under the overlay it is *glyph advance*. Every
   match would push the rest of its line right in the underlay while the
   textarea above it stayed put, and with wrapping on it would move the wrap
   column outright — hazard 1, the same progressive desync the whole geometry
   argument exists to prevent.

   So both paint through the CSS Custom Highlight API, exactly as the read-only
   occurrence tint and the repository-search hit paint already do. A highlight
   accepts colour properties and nothing else, so it cannot change a metric
   even in principle. Two things fall out of that for free: the underlay's HTML
   is never rewritten for a find (so stepping between matches costs no
   whole-document render and no tokenizing pass, unlike the read-only path),
   and a browser without the API simply gets no paint — in which case the find
   stays the disabled control it has always been while editing.

   Nothing here is persisted, and the search state it drives is the pane's
   existing one, so the find input, the match counter and Enter/Shift+Enter all
   behave as they do in the read-only view. */

const EXPLORER_EDIT_FIND_HIGHLIGHT = 'explorer-edit-find';
const EXPLORER_EDIT_FIND_ACTIVE_HIGHLIGHT = 'explorer-edit-find-active';
const EXPLORER_EDIT_OCCURRENCE_HIGHLIGHT = 'explorer-edit-occurrence';

/* The ranges each pane has contributed, keyed by pane index.
   `window.CSS.highlights` is page-global while an explorer grid can hold
   several open editors at once, so a pane must never be able to clear another
   pane's paint: each repaint rebuilds all three registries from every record
   here. Bounded by the grid, and a pane's record is dropped on teardown. */
const _explorerEditPaint = new Map();

let _explorerEditOccurrenceTimer = null;

function explorerEditFindPolicy() {
    return window.GridVibeExplorerEditHighlight || null;
}

/* The rendered rows behind the textarea, but only while they are showing
   exactly the draft the caller's offsets were computed against. The underlay
   is repainted one animation frame behind the draft, so between a keystroke
   and that frame its rows describe the previous buffer — mapping offsets onto
   them then would put a match on the wrong column. Returning nothing leaves
   the paint for `repaintExplorerEditFind`, which runs at the tail of that very
   frame. */
function explorerEditUnderlayFor(index, draft) {
    const pane = terminals[index];
    if (!pane || !pane._explorerEdit || pane._explorerEditOverlayDraft !== draft) {
        return null;
    }
    const stack = document.querySelector(`[data-explorer-edit-stack="${index}"]`);
    return stack ? stack.querySelector('.explorer-edit-underlay') : null;
}

/* Whether this pane's editor can host a find at all: it needs the overlay's
   rows and it needs the highlight API to paint them with. Both are silent
   enhancements, so failing either simply leaves the find widget disabled for
   the duration of the edit — which is exactly what it was before. */
function explorerEditFindAvailable(index) {
    if (!explorerEditFindPolicy() || !explorerNamedHighlight(EXPLORER_EDIT_FIND_HIGHLIGHT)) {
        return false;
    }
    const stack = document.querySelector(`[data-explorer-edit-stack="${index}"]`);
    return Boolean(stack && stack.querySelector('.explorer-edit-underlay'));
}

function explorerEditPaintRecord(index) {
    let record = _explorerEditPaint.get(index);
    if (!record) {
        record = { find: [], findActive: [], occurrence: [], activeRange: null };
        _explorerEditPaint.set(index, record);
    }
    return record;
}

/* Spans → live DOM Ranges over the underlay's rows. The row helpers are
   explorer-search.js's, which already walk a rendered source line's text nodes
   for the repository-hit paint; a match can straddle several syntax spans, so
   the walk is the only honest way to address a column range.

   Spans arrive in buffer order, so consecutive ones on the same row share a
   single walk — a thousand matches on one long line cost one. */
function explorerEditSpanRanges(root, spans) {
    const ranges = [];
    let cachedLine = -1;
    let cachedNodes = null;
    spans.forEach(span => {
        if (span.line !== cachedLine) {
            const row = root.querySelector(
                `.explorer-source-line[data-explorer-line="${span.line}"]`
            );
            cachedNodes = row ? explorerSourceLineTextNodes(row).nodes : null;
            cachedLine = span.line;
        }
        if (!cachedNodes || !cachedNodes.length) {
            return;
        }
        const range = explorerSourceLineRange(cachedNodes, span.start, span.end);
        if (range) {
            ranges.push({ range, active: Boolean(span.active) });
        }
    });
    return ranges;
}

/* Rebuild all three registries from every pane's record. Priority is what
   orders them where they overlap — the match Enter is sitting on has to read
   over the rest of the find, and the find over the selected-word tint — since
   insertion order across separate registries means nothing. */
function syncExplorerEditHighlights() {
    const find = explorerNamedHighlight(EXPLORER_EDIT_FIND_HIGHLIGHT);
    const active = explorerNamedHighlight(EXPLORER_EDIT_FIND_ACTIVE_HIGHLIGHT);
    const occurrence = explorerNamedHighlight(EXPLORER_EDIT_OCCURRENCE_HIGHLIGHT);
    if (!find || !active || !occurrence) {
        return;
    }
    find.clear();
    active.clear();
    occurrence.clear();
    occurrence.priority = 0;
    find.priority = 1;
    active.priority = 2;
    _explorerEditPaint.forEach(record => {
        record.find.forEach(range => find.add(range));
        record.findActive.forEach(range => active.add(range));
        record.occurrence.forEach(range => occurrence.add(range));
    });
}

function paintExplorerEditFindRanges(index, draft, ranges, activeIndex) {
    const record = explorerEditPaintRecord(index);
    record.find = [];
    record.findActive = [];
    record.activeRange = null;
    const policy = explorerEditFindPolicy();
    const root = explorerEditUnderlayFor(index, draft);
    if (root && policy && ranges.length) {
        const spans = policy.lineSpansForRanges(
            draft, decorateExplorerSearchRanges(ranges, activeIndex)
        );
        explorerEditSpanRanges(root, spans).forEach(entry => {
            if (entry.active) {
                record.findActive.push(entry.range);
                record.activeRange = record.activeRange || entry.range;
            } else {
                record.find.push(entry.range);
            }
        });
    }
    syncExplorerEditHighlights();
}

/* Bring the active match into view. A Custom Highlight has no element to
   scrollIntoView, so the range's own rect is measured against the scroller —
   which, inside the overlay, is the same .explorer-source-view both layers sit
   in. Vertically it always centres, matching the read-only find; horizontally
   it moves only when the match is genuinely outside the box, so stepping down
   a wrapped file does not drift sideways.

   The caret is deliberately left where the reader put it. Find is a way of
   looking around the buffer, and silently relocating the insertion point of a
   buffer with unsaved changes — in a textarea that is not even focused while
   the find input is — would be a nasty surprise on the next keystroke. */
function scrollExplorerEditFindMatch(index) {
    const record = _explorerEditPaint.get(index);
    const view = document.getElementById(`explorer-code-${index}`);
    const range = record && record.activeRange;
    if (!view || !range || typeof window.requestAnimationFrame !== 'function') {
        return;
    }
    window.requestAnimationFrame(() => {
        const rect = range.getBoundingClientRect();
        const box = view.getBoundingClientRect();
        // Zero on both axes means the rows were rebuilt under us; the repaint
        // that replaced them will scroll to the match it paints.
        if (!rect.width && !rect.height) {
            return;
        }
        view.scrollTop += (rect.top - box.top) - Math.max(0, (box.height - rect.height) / 2);
        if (rect.left < box.left || rect.right > box.right) {
            view.scrollLeft += (rect.left - box.left)
                - Math.max(0, (box.width - rect.width) / 2);
        }
    });
}

/* The edit-mode half of applyExplorerSearch(), which delegates here whenever a
   pane has an open editor. It drives the pane's own search state, so the input
   value, the match counter, Enter/Shift+Enter and Escape are the same controls
   doing the same things — only the haystack (the live draft, not the file on
   disk) and the paint (highlights, not <mark> wrappers) differ. */
async function applyExplorerEditFind(index, { resetActive = false } = {}) {
    const pane = terminals[index];
    const editState = pane && pane._explorerEdit;
    const policy = explorerEditFindPolicy();
    if (!pane || !editState || !policy) {
        return;
    }
    const search = ensureExplorerSearchState(pane);
    if (resetActive) {
        search.activeIndex = 0;
    }
    const query = search.query || '';
    const draft = String(editState.draft == null ? '' : editState.draft);
    const decision = policy.findRefreshDecision({
        enabled: explorerEditFindAvailable(index),
        query,
        draft,
        resultQuery: search.resultQuery,
        /* The draft the ranges were resolved against lives on the *edit*
           state, not the pane's search state: it has to die with the editor.
           A second edit session must never trust the first one's marker, and
           the read-only find must never inherit ranges computed against a
           draft that was cancelled. */
        resultDraft: editState.findDraft
    });

    if (decision === 'off' || decision === 'clear') {
        search.ranges = [];
        search.resultQuery = '';
        search.matchCount = 0;
        search.matchCapped = false;
        search.activeIndex = 0;
        editState.findDraft = '';
        paintExplorerEditFindRanges(index, draft, [], 0);
        updateExplorerSearchControls(index, query, 0, 0);
        return;
    }

    let ranges = Array.isArray(search.ranges) ? search.ranges : [];
    if (decision === 'recompute') {
        cancelExplorerSearch(index);
        const token = { cancelled: false };
        pane._explorerSearchToken = token;
        updateExplorerSearchControls(index, query, 0, 0);
        const result = await explorerFindRangesAsync(draft, query, token);
        /* The buffer can move while a large scan is in flight — a keystroke,
           a dictated transcript, a discard — and the ranges describe whatever
           it held when the scan started. The keystroke that moved it has
           already scheduled its own pass. */
        if (
            token.cancelled
            || pane._explorerSearchToken !== token
            || terminals[index] !== pane
            || pane._explorerEdit !== editState
            || editState.draft !== draft
        ) {
            return;
        }
        pane._explorerSearchToken = null;
        ranges = result.ranges;
        ranges.capped = result.capped;
        search.ranges = ranges;
        search.resultQuery = query;
        editState.findDraft = draft;
    }

    search.matchCount = ranges.length;
    search.matchCapped = Boolean(ranges.capped);
    search.activeIndex = explorerResolveSearchActiveIndex(search, ranges);
    paintExplorerEditFindRanges(index, draft, ranges, search.activeIndex);
    updateExplorerSearchControls(
        index, query, search.activeIndex, search.matchCount, search.matchCapped
    );
    if (search.matchCount) {
        scrollExplorerEditFindMatch(index);
    }
}

/* The pane index whose edit textarea currently holds focus, or -1. Only one
   editor can be focused, so only one can own a selection worth tinting. */
function explorerEditFocusedIndex() {
    const active = document.activeElement;
    const match = /^explorer-edit-textarea-(\d+)$/.exec((active && active.id) || '');
    if (!match) {
        return -1;
    }
    const index = Number.parseInt(match[1], 10);
    return terminals[index] && terminals[index]._explorerEdit ? index : -1;
}

/* Re-derive the selected-word tint for whichever editor is focused, and drop
   it everywhere else.

   The read-only tint cannot do this job: a textarea's selection is not in the
   document's selection, so `window.getSelection()` reports nothing to paint
   and that listener correctly clears its own registry. This one owns a
   separate registry for exactly that reason — neither can wipe the other. */
function refreshExplorerEditOccurrenceTint() {
    const focused = explorerEditFocusedIndex();
    const policy = explorerEditFindPolicy();
    _explorerEditPaint.forEach((record, index) => {
        if (index !== focused) {
            record.occurrence = [];
        }
    });
    if (focused >= 0 && policy) {
        const record = explorerEditPaintRecord(focused);
        record.occurrence = [];
        const textarea = document.getElementById(`explorer-edit-textarea-${focused}`);
        const value = textarea ? textarea.value : '';
        const root = textarea ? explorerEditUnderlayFor(focused, value) : null;
        const selection = root && policy.selectionOccurrenceQuery({
            value,
            selectionStart: textarea.selectionStart,
            selectionEnd: textarea.selectionEnd,
            maxQueryLength: EXPLORER_OCCURRENCE_MAX_QUERY
        });
        if (selection) {
            const spans = policy.lineSpansForRanges(value, policy.occurrenceSpans({
                draft: value,
                query: selection.query,
                selectionStart: selection.start,
                selectionEnd: selection.end,
                maxMatches: EXPLORER_OCCURRENCE_MAX_MATCHES
            }));
            record.occurrence = explorerEditSpanRanges(root, spans)
                .map(entry => entry.range);
        }
    }
    syncExplorerEditHighlights();
}

/* Called at the tail of every underlay repaint. The rebuilt rows dropped the
   nodes every painted range pointed at, so all three registries are emptied
   for this pane; the tint is re-derived immediately (it is one scan of the
   draft) and the find is rescheduled through the same debounce the read-only
   find uses, because it is a whole-buffer scan and the reader is mid-word. */
function repaintExplorerEditFind(index) {
    const record = _explorerEditPaint.get(index);
    if (record) {
        record.find = [];
        record.findActive = [];
        record.occurrence = [];
        record.activeRange = null;
    }
    refreshExplorerEditOccurrenceTint();
    const pane = terminals[index];
    if (pane && pane._explorerEdit && ensureExplorerSearchState(pane).query) {
        scheduleExplorerSearch(index);
    }
}

/* One added line in teardownExplorerEditOverlay(), which every teardown route
   already funnels through. The cached ranges go with the paint: they were
   resolved against a draft that no longer exists, and the read-only find this
   pane is about to fall back to must resolve its own against the file. */
function clearExplorerEditFind(index) {
    const pane = terminals[index];
    if (pane && pane._explorerSearch) {
        pane._explorerSearch.ranges = [];
        pane._explorerSearch.resultQuery = '';
    }
    _explorerEditPaint.delete(index);
    syncExplorerEditHighlights();
}

/* ── Carrying the selection across the edit swap ──────────────────────────
   The occurrence tint has no state of its own: it is derived from whatever is
   selected, every time. That is what makes it cheap, and it is also why it
   used to die at both edges of edit mode — entering replaced the rows the
   reader's selection was anchored to, and leaving destroyed the textarea whose
   selection had replaced it, so a double-clicked word went dark on the way in
   and had to be picked again on the way back.

   Neither surface can hold the other's selection, so what crosses is a
   description of it — the row, the column on that row, and the selected text —
   which the policy module re-resolves against whichever buffer is now there.
   The tint then falls out of the restored selection exactly as it does from a
   fresh one; nothing about how it is derived changes, and nothing is
   persisted. */
function explorerSourceSelectionCarry(index) {
    const selection = window.getSelection?.();
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
        return null;
    }
    const needle = selection.toString();
    if (!needle || /[\r\n]/.test(needle)) {
        return null;
    }
    const code = document.getElementById(`explorer-code-${index}`);
    const range = selection.getRangeAt(0);
    const row = explorerElementForNode(range.startContainer)?.closest('[data-explorer-line]');
    if (!code || !row || !code.contains(row)) {
        return null;
    }
    /* The column is a hint, not the answer — a selection that starts on an
       element boundary rather than inside a text node has none, and the
       resolver falls back to looking for the text on that row. */
    const entry = explorerSourceLineTextNodes(row).nodes
        .find(node => node.node === range.startContainer);
    return {
        line: Number(row.dataset.explorerLine || 0),
        column: entry ? entry.start + range.startOffset : -1,
        needle
    };
}

function explorerEditorSelectionCarry(index) {
    const pane = terminals[index];
    const policy = explorerEditFindPolicy();
    const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
    if (!pane || !pane._explorerEdit || !policy || !textarea) {
        return null;
    }
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    if (!Number.isInteger(start) || !Number.isInteger(end) || end <= start) {
        return null;
    }
    const needle = textarea.value.slice(start, end);
    if (/[\r\n]/.test(needle)) {
        return null;
    }
    const [span] = policy.lineSpansForRanges(textarea.value, [{ start, end }]);
    return span ? { line: span.line, column: span.start, needle } : null;
}

function restoreExplorerEditorSelection(index, carry) {
    const pane = terminals[index];
    const policy = explorerEditFindPolicy();
    const textarea = document.getElementById(`explorer-edit-textarea-${index}`);
    if (!carry || !pane || !pane._explorerEdit || !policy || !textarea) {
        return false;
    }
    const resolved = policy.carriedSelectionRange({
        text: textarea.value,
        line: carry.line,
        column: carry.column,
        needle: carry.needle
    });
    if (!resolved) {
        return false;
    }
    textarea.setSelectionRange(resolved.start, resolved.end);
    return true;
}

/* The row's own text is the haystack here, so the resolver runs over one line
   and the carried column keeps its meaning. */
function restoreExplorerSourceSelection(index, carry) {
    const policy = explorerEditFindPolicy();
    const code = document.getElementById(`explorer-code-${index}`);
    const row = carry && code && code.querySelector(
        `.explorer-source-line[data-explorer-line="${carry.line}"]`
    );
    const selection = window.getSelection?.();
    if (!policy || !row || !selection) {
        return false;
    }
    const { nodes, text } = explorerSourceLineTextNodes(row);
    if (!nodes.length) {
        return false;
    }
    const resolved = policy.carriedSelectionRange({
        text, line: 1, column: carry.column, needle: carry.needle
    });
    const range = resolved && explorerSourceLineRange(nodes, resolved.start, resolved.end);
    if (!range) {
        return false;
    }
    selection.removeAllRanges();
    selection.addRange(range);
    return true;
}

/* Debounced for the same reason the read-only tint is: a drag-select fires on
   every mouse move. `focusout` is listened for too because leaving the
   textarea leaves its selection behind — Chromium keeps `selectionStart` on a
   blurred textarea and fires no `selectionchange` for the blur, so without
   this the tint would outlive the selection that justified it. */
function scheduleExplorerEditOccurrenceTint() {
    /* Both listeners are page-wide, so this runs on every selection and every
       focus change anywhere in the workspace — including the overwhelmingly
       common case of no editor being open at all. Nothing to clear and nobody
       focused means there is no work, and no reason to bring the highlight
       registries into existence to discover that. */
    if (!_explorerEditPaint.size && explorerEditFocusedIndex() < 0) {
        return;
    }
    window.clearTimeout(_explorerEditOccurrenceTimer);
    _explorerEditOccurrenceTimer = window.setTimeout(() => {
        _explorerEditOccurrenceTimer = null;
        refreshExplorerEditOccurrenceTint();
    }, EXPLORER_OCCURRENCE_DEBOUNCE_MS);
}

function installExplorerEditOccurrenceTint() {
    document.addEventListener('selectionchange', scheduleExplorerEditOccurrenceTint);
    document.addEventListener('focusout', scheduleExplorerEditOccurrenceTint);
}

installExplorerEditOccurrenceTint();
