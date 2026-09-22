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

   The same file also owns the one question a *restored* layout asks before any
   of that: the grid it was saved on is not necessarily the grid this build
   draws, and a layout written at a coarser base cell reaches the integer floor
   several halvings early. That migration is `planSnapshotRescale()` below.

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

    /* ── A snapshot laid out on a coarser grid ──────────────────────────────

       A saved layout keeps the coordinates it was saved with, and the base cell
       has not always been the same size: before 2026-07-24 a layout was written
       at 2 grid units per cell rather than 8. One split already took such a
       pane to a span of 1 — the floor of an integer grid, where a half cannot
       be written down at all — so it could never be split side by side again,
       in that session or any session restored from it, however wide the window
       was. The refusal was permanent and invisible: the layout renders normally
       and the pane can still be stacked.

       A split subdivides one rectangle and never grows the box around it, so a
       snapshot's box is still the base layout's own size in cells times the
       unit it was laid out at, and dividing names that unit. The base's size in
       cells is the caller's to state, because the layout class a record was
       built under is not part of the record — every shape the pane count could
       have been built from is offered, and a box that fits more than one of
       them is left alone rather than guessed at.

       Rescaling is a uniform multiplication, so it cannot reproportion the
       window: it changes how finely the same arrangement is addressed and
       nothing else. Each track becomes `factor` tracks of the *same* weight
       rather than a `factor`-th of it — `fr` is relative to the axis total, so
       repeating preserves every pane's share of the axis exactly, while
       dividing would walk a dragged weight down past the floor a save clamps it
       to and bring it back as a different layout. The one thing the finer grid
       cannot reproduce to the pixel is a divider somebody had dragged: it
       carries `factor` times as many gap lines, and a pane whose weight share
       differs from its track share pays a few of those pixels once. A layout
       nobody has dragged is exact. */

    function isPositiveInteger(value) {
        return Number.isInteger(value) && value > 0;
    }

    /* The grid a rectangle list occupies — the same `max(x + w - 1)` the page
       publishes as `--split-grid-columns` — or null when any rectangle is not
       whole and positive. All-or-nothing, like the server's own reading of a
       stored record (`web/session_presentation.py`): a list holding one
       unreadable rectangle is not a layout this can reason about. */
    function snapshotGridBox(rects) {
        if (!Array.isArray(rects) || rects.length === 0) {
            return null;
        }
        let columns = 0;
        let rows = 0;
        for (const rect of rects) {
            const x = Number(rect?.x);
            const y = Number(rect?.y);
            const w = Number(rect?.w);
            const h = Number(rect?.h);
            if (![x, y, w, h].every(isPositiveInteger)) {
                return null;
            }
            columns = Math.max(columns, x + w - 1);
            rows = Math.max(rows, y + h - 1);
        }
        return { columns, rows };
    }

    /* The base-cell unit a snapshot was laid out at, or 0 when its box does not
       divide by exactly one of the base shapes it could have been built from.
       Two shapes answering differently is an ambiguous record, and so is a box
       that divides by none — both are left at 0, which asks the caller to
       change nothing. `cellShapes` are `{ columns, rows }` in cells. */
    function inferSnapshotUnit(rects, cellShapes) {
        const box = snapshotGridBox(rects);
        if (!box) {
            return 0;
        }
        const units = new Set();
        (Array.isArray(cellShapes) ? cellShapes : []).forEach(shape => {
            const columns = Number(shape?.columns);
            const rows = Number(shape?.rows);
            if (!isPositiveInteger(columns) || !isPositiveInteger(rows)) {
                return;
            }
            if (box.columns % columns !== 0 || box.rows % rows !== 0) {
                return;
            }
            const unit = box.columns / columns;
            if (unit === box.rows / rows) {
                units.add(unit);
            }
        });
        return units.size === 1 ? Array.from(units)[0] : 0;
    }

    /* One track per `factor`, carrying the weight the original track carried.
       A caller holding no weights at all gets null back and normalizes the new
       track count to ones itself, which is the same layout: an axis nobody has
       dragged is uniform whatever its resolution. */
    function expandTrackWeights(weights, count, factor) {
        if (!Array.isArray(weights)) {
            return null;
        }
        const expanded = [];
        for (let index = 0; index < count; index++) {
            const value = Number(weights[index]);
            const weight = Number.isFinite(value) && value > 0 ? value : 1;
            for (let copy = 0; copy < factor; copy++) {
                expanded.push(weight);
            }
        }
        return expanded;
    }

    /* The whole migration, or null when there is nothing to do — a snapshot
       already at `unit`, a unit that cannot be read off the box, or one whose
       rescaled box would not survive the trip to disk.

         · `rects`                 the snapshot's rectangles.
         · `columnWeights`/`rowWeights`  its stored track weights, or null.
         · `cellShapes`            the base shapes this pane count could have
                                   been built from, in cells.
         · `unit`                  the grid units per base cell this build lays
                                   out at.
         · `maxGridLine`           the persisted coordinate ceiling, or 0 for
                                   no ceiling. `_normalize_workspace_layout()`
                                   drops a geometry record all-or-nothing, so a
                                   rescale that overshot the bound would cost
                                   the layout entirely on the next save — it is
                                   declined here instead. */
    function planSnapshotRescale({
        rects,
        columnWeights = null,
        rowWeights = null,
        cellShapes = [],
        unit,
        maxGridLine = 0
    } = {}) {
        const target = Number(unit);
        const box = snapshotGridBox(rects);
        if (!isPositiveInteger(target) || !box) {
            return null;
        }
        const inferred = inferSnapshotUnit(rects, cellShapes);
        if (!inferred || inferred >= target || target % inferred !== 0) {
            return null;
        }

        const factor = target / inferred;
        const columns = box.columns * factor;
        const rows = box.rows * factor;
        const ceiling = Number(maxGridLine) || 0;
        if (ceiling > 0 && (columns > ceiling || rows > ceiling)) {
            return null;
        }

        return {
            unit: inferred,
            factor,
            columns,
            rows,
            rects: rects.map(rect => ({
                ...rect,
                x: (rect.x - 1) * factor + 1,
                y: (rect.y - 1) * factor + 1,
                w: rect.w * factor,
                h: rect.h * factor,
            })),
            columnWeights: expandTrackWeights(columnWeights, box.columns, factor),
            rowWeights: expandTrackWeights(rowWeights, box.rows, factor),
        };
    }

    return {
        MIN_TRACK_WEIGHT,
        MAX_TRACK_WEIGHT,
        trackSpan,
        foreignEdgeOffsets,
        planSplit,
        snapshotGridBox,
        inferSnapshotUnit,
        expandTrackWeights,
        planSnapshotRescale
    };
}));
