/* GridVibeExplorerEditHighlight — the two decisions behind the in-place
   editor's highlight overlay, with no DOM in sight.

   The overlay paints the read-only row renderer behind a transparent-background
   textarea so entering edit mode stops moving the text. Two things have to be
   decided, and both are pure:

   * viability — is the overlay on at all for this buffer? It is an
     enhancement, never a precondition for editing, so an oversized buffer
     silently drops back to the bare textarea edit mode has always used. No
     notice, no second global sink.
   * refresh — the underlay has to be rebuilt as the draft changes (the gutter
     grows a line, a wrapped line grows a row), and that rebuild is a whole
     document render. It is coalesced into one animation frame, and a draft
     that has not moved since the last render is not re-rendered at all: arrow
     keys, a click, and a no-op keystroke all reach the same input path.

   DOM-free and require()-able from Node so both rules are executed by tests
   rather than asserted as source text. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeExplorerEditHighlight = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* Above the bound the underlay stands down: it is the same whole-document
       row render the read-only view does, and repeating it inside a frame
       budget on every keystroke is what the performance guardrail forbids.
       The caller passes the bound (the viewer's existing plain-preview
       threshold) rather than this module inventing a second number. */
    function overlayViability(options) {
        const settings = options || {};
        const length = Number(settings.contentLength);
        const max = Number(settings.maxContentLength);
        if (!Number.isFinite(length) || length < 0) {
            return { enabled: false, reason: 'unknown' };
        }
        if (Number.isFinite(max) && max > 0 && length > max) {
            return { enabled: false, reason: 'oversized' };
        }
        return { enabled: true, reason: '' };
    }

    /* `off`      — the overlay stood down for this buffer; nothing to refresh.
       `skip`     — the draft is byte-identical to what the underlay already
                    shows, so the render would repaint the same rows.
       `coalesce` — a frame is already queued; it reads the draft when it runs,
                    so this input needs no second frame.
       `schedule` — queue one frame. */
    function refreshDecision(state) {
        const current = state || {};
        if (!current.enabled) {
            return 'off';
        }
        if (String(current.draft == null ? '' : current.draft)
            === String(current.rendered == null ? '' : current.rendered)) {
            return 'skip';
        }
        return current.pending ? 'coalesce' : 'schedule';
    }

    return {
        overlayViability,
        refreshDecision
    };
}));
