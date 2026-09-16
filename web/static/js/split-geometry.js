/* GridVibeSplitGeometry — where a split lands, and what it costs the panes
   around it.

   Splitting a pane and adding one look like the same gesture and produce the
   same thing, so they are read as the same promise: two halves. Adding keeps
   it for free — a new pane rebuilds the whole grid from its fixed layout
   class, so every pane is an exact fraction of the window. A split does not
   rebuild anything: it subdivides the one rectangle the pane already occupies,
   inside a track grid whose proportions are whatever the window has been left
   in. Two things in that grid make `floor(span / 2)` something other than half:

     · **An odd span.** A rectangle three tracks wide has no middle track line.
       One child gets one track and the other two — a third against two thirds,
       not a half. Spans reach odd widths through a close (a pane absorbs its
       neighbour and inherits the sum of two spans) and through layouts saved
       before the base cell had room to halve.
     · **A lopsided interior.** Track weights are per track, and a divider drag
       rescales every track on its side of the line — so a pane that later
       absorbs its neighbour inherits both sides, and the middle *line* of its
       span is no longer the middle of its *width*. Splitting there reproduces
       the divider the user had dragged, which is exactly the uneven pair they
       are looking at.

   So the offset is chosen in pixels rather than in track counts, and the
   weights inside the span are rewritten to make the two halves equal —
   including the compensation an odd span needs, where the child holding fewer
   tracks also holds fewer of the gaps between them and must be given that
   width back in track space.

   The one thing a split may not do is move a pane it was not asked about. The
   rewrite keeps the span's total weight exactly, so every pane that contains
   the span or lies beside it renders where it did. A pane whose own edge falls
   *inside* the span is the case that cannot be had both ways: its divider is
   the line this split would have to move. There the weights are left alone and
   the split lands on the line nearest the middle — never further from it than
   `floor(span / 2)` would have been, and never at the price of a neighbour
   jumping.

   DOM-free and require()-able from Node: the arithmetic is executed by tests
   rather than asserted as source text. */
(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.GridVibeSplitGeometry = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    /* The bounds a stored weight is normalized to (`web/session_presentation.py`).
       A rewrite that lands outside them would be clamped on the way to disk and
       come back as a different layout, so it is refused here instead. */
    const MIN_TRACK_WEIGHT = 0.01;
    const MAX_TRACK_WEIGHT = 100;

    function isPositiveNumber(value) {
        return Number.isFinite(value) && value > 0;
    }

    /* Rendered size of `span` tracks starting at zero-based `index`: the tracks
       themselves plus the gaps between them, which belong to the pane that
       spans them. The same sum `terminals.js` measures a pane with. */
    function trackSpan(sizes, index, span, gap) {
        let total = 0;
        for (let offset = 0; offset < span; offset++) {
            total += Number(sizes[index + offset]) || 0;
        }
        return total + Math.max(0, span - 1) * gap;
    }

    /* Every line strictly inside [start, start + span) that some other
       rectangle begins or ends on — the dividers this split is not allowed to
       move. Offsets are relative to the span, so 1 is the line after its first
       track. `intervals` are the other rectangles' extents on the one axis
       being split: `{ start, span }`, 1-based like the rect coordinates. */
    function foreignEdgeOffsets(intervals, start, span) {
        const edges = new Set();
        (Array.isArray(intervals) ? intervals : []).forEach(interval => {
            const from = Number(interval?.start);
            const size = Number(interval?.span);
            if (!Number.isInteger(from) || !Number.isInteger(size) || size < 1) {
                return;
            }
            [from, from + size].forEach(line => {
                if (line > start && line < start + span) {
                    edges.add(line - start);
                }
            });
        });
        return Array.from(edges).sort((left, right) => left - right);
    }

    /* Where to cut, and what the axis weights must become for the cut to be a
       half. Returns the split offset in tracks and, when the weights need
       rewriting, a whole new axis array — never a partial one, so the caller
       publishes a single generation of the layout rather than patching the live
       one in place.

         · `start` / `span`   the rectangle's extent on this axis (1-based).
         · `weights`          the axis' current per-track weights.
         · `sizes`            the px each track renders at, or null when the
                              grid cannot be measured yet.
         · `gap`              the px between two adjacent tracks.
         · `foreignEdges`     offsets from `foreignEdgeOffsets()`.

       With no measurements this answers exactly what the page did before it
       existed: the middle track line, weights untouched. A grid that cannot be
       measured is one that has not been painted, and its tracks are uniform. */
    function planSplit({ start, span, weights, sizes, gap = 0, foreignEdges = [] } = {}) {
        const tracks = Number(span);
        const middle = Math.floor(tracks / 2);
        const fallback = { firstSpan: middle, weights: null, even: false };
        if (!Number.isInteger(tracks) || tracks < 2 || !Number.isInteger(Number(start)) || start < 1) {
            return fallback;
        }
        if (!Array.isArray(sizes) || !Array.isArray(weights)) {
            return fallback;
        }

        const index = start - 1;
        const gapSize = Math.max(0, Number(gap) || 0);
        const paneSize = trackSpan(sizes, index, tracks, gapSize);
        /* Two halves and the gap that separates them. A pane thinner than its
           own gap has no halves to speak of; it is also unsplittable long
           before this, so the arithmetic simply declines rather than inventing
           a negative track. */
        const half = (paneSize - gapSize) / 2;
        if (!isPositiveNumber(half)) {
            return fallback;
        }

        /* A divider inside the span belongs to another pane. The offset whose
           first child is closest to half, measured in px, is the whole answer
           there: rewriting the weights would move that pane, and a split must
           not resize a pane nobody asked about. The chosen line is never
           further from the middle than the middle *track* line would have
           been, because that line is one of the offsets considered. */
        if (Array.isArray(foreignEdges) && foreignEdges.length > 0) {
            let nearest = middle;
            let bestDistance = Infinity;
            for (let offset = 1; offset < tracks; offset++) {
                const distance = Math.abs(trackSpan(sizes, index, offset, gapSize) - half);
                if (distance < bestDistance - 1e-9) {
                    bestDistance = distance;
                    nearest = offset;
                }
            }
            return { firstSpan: nearest, weights: null, even: bestDistance <= 1e-6 };
        }

        const firstSpan = middle;
        const secondSpan = tracks - firstSpan;
        /* Track space each child must hold for both to render at `half`: its
           half, less the gaps it carries inside itself. An odd span gives the
           children different gap counts, and this is where that difference is
           paid back in track width. */
        const firstTrackSize = half - (firstSpan - 1) * gapSize;
        const secondTrackSize = half - (secondSpan - 1) * gapSize;
        if (!isPositiveNumber(firstTrackSize) || !isPositiveNumber(secondTrackSize)) {
            return fallback;
        }

        let spanWeight = 0;
        let spanSize = 0;
        for (let offset = 0; offset < tracks; offset++) {
            spanWeight += Number(weights[index + offset]) || 0;
            spanSize += Number(sizes[index + offset]) || 0;
        }
        if (!isPositiveNumber(spanWeight) || !isPositiveNumber(spanSize)) {
            return fallback;
        }

        /* px -> weight. One ratio serves the whole span because every track on
           an axis renders at the same weight-to-pixel scale; taking it from the
           span itself means the caller need not hand over the axis total. The
           two children's track space sums to the span's, so the span's weight
           is preserved to the last decimal and nothing outside it moves. */
        const perPixel = spanWeight / spanSize;
        const firstWeight = (firstTrackSize * perPixel) / firstSpan;
        const secondWeight = (secondTrackSize * perPixel) / secondSpan;
        const rewritten = weights.slice();
        let moved = false;
        for (let offset = 0; offset < tracks; offset++) {
            const weight = offset < firstSpan ? firstWeight : secondWeight;
            moved = moved || Math.abs(weight - (Number(weights[index + offset]) || 0)) > 1e-9;
            rewritten[index + offset] = weight;
        }
        /* A span that was already even needs no new weights, and saying so
           keeps the ordinary split — a fresh pane on an untouched grid — a
           rectangle change and nothing more. */
        if (!moved) {
            return { firstSpan, weights: null, even: true };
        }
        if (!rewritten.every(weight => (
            Number.isFinite(weight) && weight >= MIN_TRACK_WEIGHT && weight <= MAX_TRACK_WEIGHT
        ))) {
            /* Outside what a stored layout can hold. The uneven split is the
               lesser wrong: it survives a save. */
            return { firstSpan, weights: null, even: false };
        }

        return { firstSpan, weights: rewritten, even: true };
    }

    return {
        MIN_TRACK_WEIGHT,
        MAX_TRACK_WEIGHT,
        trackSpan,
        foreignEdgeOffsets,
        planSplit
    };
}));
