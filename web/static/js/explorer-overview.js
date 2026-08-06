/* Explorer source-view change marking — Phase 1 of the source-view change
   overview plan: the HEAD-relative change model for the open file plus the
   gutter markers it paints on the rendered Source rows. Loaded after
   explorer-git-watch.js and before terminals.js.

   Owns the `git/diff?mode=head` fetch + cache for the *Source* panel —
   deliberately separate from the view-scoped, mode-specific
   `_explorerDiffContent` / `_explorerDiffCacheKey` slots, which belong to the
   Diff panel and would otherwise fight over the same pane state. The diff is
   parsed by the reused explorerDiffChangeBlocks() (built for diff-undo) and
   classified into added / modified line marks plus deletion wedges.

   Marks re-apply cheaply after every Source rebuild (search keystrokes, wrap
   toggles and Markdown folds all re-render the rows); the fetch only happens
   on the refresh triggers wired by the caller, never on a timer of its own.
   Nothing here mutates the filesystem: `git/diff` is an established bounded,
   read-only endpoint.

   Phase 2 adds the overview column itself: the fixed <aside> docked in the
   source frame's second grid column, its row geometry, the change lane it
   paints from the same model, the viewport box, and the click / drag / wheel
   navigation that makes it the Source panel's scrollbar. It renders in
   `ruler` mode — marker lanes and viewport box, no glyphs; the canvas glyph
   paint and the Appearance-menu mode toggle arrive in Phase 3.

   Entry points — the only functions other modules may call:
   - loadExplorerChangeMarks(index)          fetch + cache + paint
   - applyExplorerChangeMarks(index)         cheap re-paint after a re-render
   - refreshExplorerOverview(index, reason)  invalidate + reload
   - teardownExplorerOverview(index)         drop the model, unwire the column
   - explorerOverviewHtml(index)             the column's markup, for the
                                             source frame's render            */

/* Above this many rendered rows the per-row geometry pass is skipped and a
   uniform row height stands in — the overview is then approximate rather
   than slow. */
const EXPLORER_OVERVIEW_MAX_ROWS = 20000;
/* Ruler-mode lane widths, in CSS pixels, painted from the aside's left edge.
   Kept in step with --explorer-overview-ruler-width in terminals.css. */
const EXPLORER_OVERVIEW_LANE_WIDTH = 4;
// A one-line change on a long file would otherwise round away to nothing.
const EXPLORER_OVERVIEW_MARK_MIN_HEIGHT = 2;
const EXPLORER_OVERVIEW_VIEWPORT_MIN_HEIGHT = 12;
// Pointer slop around a painted marker, so a 2 px band is still clickable.
const EXPLORER_OVERVIEW_HIT_SLOP = 4;
// deltaMode 1 is "lines"; the source rows are ~1.45em of a ~12px font.
const EXPLORER_OVERVIEW_WHEEL_LINE_HEIGHT = 16;

/* Marks are HEAD-relative worktree marks, so they are meaningless — and would
   lie — on a commit-history diff tab (whose new-side line numbers address a
   past commit), and premature while the in-place editor owns the buffer. A
   pane outside a Git worktree never fetches at all. */
function explorerChangeMarksEligible(pane) {
    return Boolean(
        pane
        && pane._explorerMode === 'file'
        && pane._explorerFilePath
        && pane._explorerGitContext?.available
        && !pane._explorerDiffCommit
        && !pane._explorerEdit
    );
}

/* `path \n git-revision`, keyed on the pane: the marks for a different file,
   or for a different HEAD after a commit, are a different model. Staging does
   not change `git diff HEAD`, so the index state is correctly absent here. */
function explorerChangeMarksKey(pane) {
    return `${String(pane?._explorerFilePath || '')}\n${String(pane?._explorerGitContext?.head || '')}`;
}

/* Classify the reused change blocks into gutter marks. A block is a maximal
   run of changed lines with no context between them (see
   explorerDiffChangeBlocks): added-only runs mark their lines `added`,
   mixed runs `modified`, and removed-only runs leave no line of their own —
   they become a wedge anchored above the line the deleted block used to
   precede (`block.line`, the 1-based worktree line the run starts at). */
function explorerChangeMarksModel(blocks, truncated) {
    const marks = new Map();
    const deletions = [];
    (Array.isArray(blocks) ? blocks : []).forEach(block => {
        const added = block.expected.length;
        const removed = block.replacement.length;
        if (added) {
            const kind = removed ? 'modified' : 'added';
            for (let offset = 0; offset < added; offset += 1) {
                marks.set(block.line + offset, kind);
            }
        } else if (removed) {
            deletions.push({ atLine: block.line, count: removed });
        }
    });
    // Kept for the truncated-diff indicator (later phase); the model already
    // records it so no second fetch is ever needed to find out.
    return { marks, deletions, truncated: Boolean(truncated) };
}

async function loadExplorerChangeMarks(index, { force = false } = {}) {
    const pane = terminals[index];
    const sessionId = sessionIds[index];
    const path = pane?._explorerFilePath || '';
    if (!pane || !sessionId || !explorerChangeMarksEligible(pane)) {
        return;
    }
    // No Source panel, nothing to mark: the image viewer renders no rows.
    if (!document.getElementById(`explorer-code-${index}`)) {
        return;
    }
    const key = explorerChangeMarksKey(pane);
    if (!force && pane._explorerChangeMarks && pane._explorerChangeMarksKey === key) {
        applyExplorerChangeMarks(index);
        return;
    }

    let diff = '';
    let truncated = false;
    // Reuse the Diff panel's HEAD diff when loadExplorerDiff() already cached
    // exactly one for this path — its cache is mode-scoped, so only an exact
    // key match qualifies (worktree / staged / commit diffs would mark the
    // wrong lines).
    if (
        pane._explorerDiffLoaded
        && pane._explorerDiffCacheKey === explorerDiffCacheKey(path, '', 'head')
    ) {
        diff = pane._explorerDiffContent || '';
        truncated = Boolean(pane._explorerDiffTruncated);
    } else {
        try {
            const params = new URLSearchParams({ path, mode: 'head' });
            const response = await fetch(
                `/api/explorer/${encodeURIComponent(sessionId)}/git/diff?${params.toString()}`,
                { cache: 'no-store' }
            );
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || 'Failed to load Git diff');
            }
            diff = data.diff || '';
            truncated = Boolean(data.truncated);
        } catch (error) {
            // An untracked file returns an empty diff, but any genuine failure
            // also just means no marks — the Source view stands on its own.
            console.error('[GridVibe Sessions] Explorer change marks failed:', error);
            return;
        }
    }

    // The viewer may have moved to another file (or a commit-diff tab) during
    // the flight; the response describes whatever was open when it started.
    if (
        terminals[index] !== pane
        || sessionIds[index] !== sessionId
        || !explorerChangeMarksEligible(pane)
        || pane._explorerFilePath !== path
        || explorerChangeMarksKey(pane) !== key
    ) {
        return;
    }
    pane._explorerChangeMarksKey = key;
    pane._explorerChangeMarks = explorerChangeMarksModel(explorerDiffChangeBlocks(diff), truncated);
    applyExplorerChangeMarks(index);
}

/* Re-paint the cached model onto the currently rendered rows *and* bring the
   overview column back in step with them. Runs after every
   renderExplorerSource() rebuild — search keystrokes included — so both
   halves stay cheap: the gutter is one querySelectorAll pass of attribute
   writes, and the overview work is deferred to one coalesced frame. */
function applyExplorerChangeMarks(index) {
    applyExplorerChangeMarkGutter(index);
    scheduleExplorerOverviewSync(index);
}

/* The gutter half. Rows are rebuilt from scratch on every render, so there is
   never anything to clean up — a missing model or an ineligible pane simply
   means no marks. No layout reads here: the geometry pass owns those. */
function applyExplorerChangeMarkGutter(index) {
    const pane = terminals[index];
    const code = document.getElementById(`explorer-code-${index}`);
    const model = pane?._explorerChangeMarks;
    if (
        !pane
        || !code
        || !model
        || !explorerChangeMarksEligible(pane)
        || pane._explorerChangeMarksKey !== explorerChangeMarksKey(pane)
        || (!model.marks.size && !model.deletions.length)
    ) {
        return;
    }

    const rows = new Map();
    code.querySelectorAll('.explorer-source-line[data-explorer-line]').forEach(row => {
        const line = Number(row.dataset.explorerLine);
        if (Number.isFinite(line)) {
            rows.set(line, row);
        }
    });
    rows.forEach((row, line) => {
        const kind = model.marks.get(line);
        if (kind) {
            row.dataset.explorerChange = kind;
        }
    });
    model.deletions.forEach(({ atLine }) => {
        /* The wedge sits on the boundary where the deleted lines used to be:
           at the top of the row that follows them, or — for a deletion at end
           of file, which has no following row — at the bottom of the last
           surviving row before it. A boundary hidden by a Markdown fold has
           no rendered row and gets no wedge until the fold opens (geometry is
           over rendered rows). */
        let row = rows.get(atLine);
        let after = false;
        if (!row) {
            let previous = 0;
            rows.forEach((_, line) => {
                if (line < atLine && line > previous) {
                    previous = line;
                }
            });
            row = previous ? rows.get(previous) : null;
            after = Boolean(row);
        }
        // A line mark says more than a wedge; never overwrite one.
        if (!row || row.dataset.explorerChange) {
            return;
        }
        row.dataset.explorerChange = 'deleted';
        row.classList.toggle('explorer-source-change-after', after);
    });
}

/* Invalidate the cached model and reload it. The refresh triggers (watcher
   signals, GridVibe Git actions, in-place saves, diff-undo) call this — the
   file/HEAD moved, so the old key must not short-circuit the fetch. `reason`
   is diagnostic only for now; the overview repaint gating that will consume
   it arrives with the overview element in a later phase. */
function refreshExplorerOverview(index, reason) {
    const pane = terminals[index];
    if (!pane) {
        return;
    }
    pane._explorerChangeMarksKey = '';
    pane._explorerChangeMarks = null;
    return loadExplorerChangeMarks(index);
}

function teardownExplorerOverview(index) {
    const pane = terminals[index];
    if (!pane) {
        return;
    }
    pane._explorerChangeMarksKey = '';
    pane._explorerChangeMarks = null;
    pane._explorerOverviewGeometry = null;
    pane._explorerOverviewMarkers = null;
    pane._explorerOverviewDrag = null;
    /* Listeners die with the <aside> the next render replaces, but a
       ResizeObserver outlives its target, and a queued frame would repaint
       against a pane that has moved on. */
    pane._explorerOverviewObserver?.disconnect();
    pane._explorerOverviewObserver = null;
    explorerOverviewCancelFrames(pane);
}

/* ─────────────────────────────────────────────
   The overview column (Phase 2, `ruler` mode)
   ───────────────────────────────────────────── */

/* Rendered into .explorer-source-frame's second grid column by
   renderExplorerFile. It takes real layout width rather than overlaying the
   text, so it never hides code and never fights the horizontal scrollbar.
   `role="scrollbar"` is the honest description: click, drag and wheel all
   drive the inner .explorer-source-view it points at. The viewport box is
   decorative — the scroll position it shows is already on the aside as
   aria-valuenow. */
function explorerOverviewHtml(index) {
    return `<aside
        class="explorer-source-overview"
        data-explorer-overview="${index}"
        data-explorer-overview-mode="ruler"
        role="scrollbar"
        aria-controls="explorer-code-${index}"
        aria-orientation="vertical"
        aria-label="Source view overview"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow="0"
        hidden
    ><canvas class="explorer-overview-canvas"></canvas><div class="explorer-overview-viewport" aria-hidden="true"></div></aside>`;
}

function explorerOverviewParts(index) {
    const code = document.getElementById(`explorer-code-${index}`);
    const frame = code?.parentElement || null;
    const aside = frame?.querySelector(`[data-explorer-overview="${index}"]`) || null;
    const canvas = aside?.querySelector('.explorer-overview-canvas') || null;
    const viewport = aside?.querySelector('.explorer-overview-viewport') || null;
    if (!code || !frame || !aside || !canvas || !viewport) {
        return null;
    }
    return { code, frame, aside, canvas, viewport };
}

function explorerOverviewCancelFrames(pane) {
    if (typeof window.cancelAnimationFrame === 'function') {
        if (pane._explorerOverviewFrame) {
            window.cancelAnimationFrame(pane._explorerOverviewFrame);
        }
        if (pane._explorerOverviewViewportFrame) {
            window.cancelAnimationFrame(pane._explorerOverviewViewportFrame);
        }
    }
    pane._explorerOverviewFrame = 0;
    pane._explorerOverviewViewportFrame = 0;
}

/* Geometry over *rendered* rows — never `line × lineHeight`. Line wrapping is
   on by default, so row heights are not uniform, and a Markdown fold removes
   rows from the DOM entirely. Everything the column paints is expressed in
   the scroll container's own coordinate space (0 … code.scrollHeight), which
   is the same space the viewport box and every scroll target use, so glyph
   rows, change marks and the box stay aligned by construction. */
function explorerOverviewGeometry(index, parts) {
    const pane = terminals[index];
    const rows = parts.code.querySelectorAll('.explorer-source-line[data-explorer-line]');
    if (!pane) {
        return null;
    }
    if (!rows.length) {
        pane._explorerOverviewGeometry = null;
        return null;
    }
    const wrapped = parts.code.classList.contains('wrap-lines');
    /* scrollHeight moves whenever the content, the font, a fold or the wrap
       flag does, and clientWidth whenever a resize re-wraps: one cheap read
       apiece is enough to know the cached pass is still good. */
    const signature = [
        rows.length,
        parts.code.scrollHeight,
        parts.code.clientWidth,
        wrapped ? 1 : 0
    ].join(':');
    const cached = pane._explorerOverviewGeometry;
    if (cached && cached.signature === signature) {
        return cached;
    }

    const count = rows.length;
    const lines = new Float64Array(count);
    const tops = new Float64Array(count);
    const heights = new Float64Array(count);
    const lineIndex = new Map();
    /* One uninterrupted read pass: two rects, then offsetTop/offsetHeight per
       row with no write in between, so the layout flushes exactly once no
       matter how many rows there are. Unwrapped rows are one line tall each,
       so a single measurement describes all of them; above the row cap the
       same uniform approximation is the deliberate degradation. */
    const first = rows[0];
    const origin = parts.code.scrollTop
        + (first.getBoundingClientRect().top - parts.code.getBoundingClientRect().top);
    const firstOffset = first.offsetTop;
    const uniformHeight = first.offsetHeight || 1;
    const uniform = !wrapped || count > EXPLORER_OVERVIEW_MAX_ROWS;
    for (let i = 0; i < count; i += 1) {
        const row = rows[i];
        const line = Number(row.dataset.explorerLine);
        lines[i] = line;
        lineIndex.set(line, i);
        if (uniform) {
            tops[i] = origin + (i * uniformHeight);
            heights[i] = uniformHeight;
        } else {
            tops[i] = origin + (row.offsetTop - firstOffset);
            heights[i] = row.offsetHeight;
        }
    }

    const geometry = {
        signature,
        lines,
        tops,
        heights,
        lineIndex,
        contentHeight: Math.max(1, parts.code.scrollHeight),
        approximate: wrapped && count > EXPLORER_OVERVIEW_MAX_ROWS
    };
    pane._explorerOverviewGeometry = geometry;
    return geometry;
}

/* A deletion wedge sits on a boundary, and the row that used to follow the
   removed lines may itself be folded away — so fall back to the last rendered
   row above it, exactly as the gutter wedge does. */
function explorerOverviewDeletionRow(geometry, atLine) {
    const exact = geometry.lineIndex.get(atLine);
    if (exact !== undefined) {
        return { row: exact, after: false };
    }
    let low = 0;
    let high = geometry.lines.length - 1;
    let found = -1;
    while (low <= high) {
        const middle = (low + high) >> 1;
        if (geometry.lines[middle] < atLine) {
            found = middle;
            low = middle + 1;
        } else {
            high = middle - 1;
        }
    }
    return found >= 0 ? { row: found, after: true } : null;
}

/* Marker colours are read back from the shared tokens on the live element, so
   the explorer theme and light/dark follow automatically and no palette
   literal enters this file (guardrail 7). */
function explorerOverviewColors(aside) {
    const style = window.getComputedStyle(aside);
    return {
        added: style.getPropertyValue('--gv-diff-add').trim(),
        modified: style.getPropertyValue('--gv-diff-modified').trim(),
        deleted: style.getPropertyValue('--gv-diff-delete').trim()
    };
}

/* Full repaint of the change lane. Only ever runs on a content / marks /
   geometry / size change — a scroll moves the viewport box alone, and a
   search keystroke re-renders rows whose geometry signature is unchanged, so
   neither repaints. Also records the painted bands so a click on a marker can
   be told from a click on the track. */
function paintExplorerOverview(index, parts, geometry) {
    const pane = terminals[index];
    const width = parts.aside.clientWidth;
    const height = parts.aside.clientHeight;
    const context = typeof parts.canvas.getContext === 'function'
        ? parts.canvas.getContext('2d')
        : null;
    if (!pane || !context || !width || !height) {
        return;
    }
    const ratio = window.devicePixelRatio || 1;
    const pixelWidth = Math.max(1, Math.round(width * ratio));
    const pixelHeight = Math.max(1, Math.round(height * ratio));
    if (parts.canvas.width !== pixelWidth || parts.canvas.height !== pixelHeight) {
        parts.canvas.width = pixelWidth;
        parts.canvas.height = pixelHeight;
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const markers = [];
    pane._explorerOverviewMarkers = markers;
    const model = pane._explorerChangeMarks;
    if (!model || !geometry) {
        return;
    }
    const colors = explorerOverviewColors(parts.aside);
    const scale = height / geometry.contentHeight;
    const band = (row, color, line) => {
        const top = geometry.tops[row] * scale;
        const size = Math.max(EXPLORER_OVERVIEW_MARK_MIN_HEIGHT, geometry.heights[row] * scale);
        context.fillStyle = color;
        context.fillRect(0, top, EXPLORER_OVERVIEW_LANE_WIDTH, size);
        markers.push({ line, top, bottom: top + size });
    };
    model.marks.forEach((kind, line) => {
        const row = geometry.lineIndex.get(line);
        if (row === undefined) {
            return;
        }
        band(row, kind === 'added' ? colors.added : colors.modified, line);
    });
    model.deletions.forEach(({ atLine }) => {
        const anchor = explorerOverviewDeletionRow(geometry, atLine);
        if (!anchor) {
            return;
        }
        // The wedge marks a boundary, not a line, so it is painted as a thin
        // rule at the edge the removed lines used to touch.
        const edge = anchor.after
            ? geometry.tops[anchor.row] + geometry.heights[anchor.row]
            : geometry.tops[anchor.row];
        const top = Math.max(0, (edge * scale) - (EXPLORER_OVERVIEW_MARK_MIN_HEIGHT / 2));
        context.fillStyle = colors.deleted;
        context.fillRect(0, top, EXPLORER_OVERVIEW_LANE_WIDTH, EXPLORER_OVERVIEW_MARK_MIN_HEIGHT);
        markers.push({
            line: geometry.lines[anchor.row],
            top,
            bottom: top + EXPLORER_OVERVIEW_MARK_MIN_HEIGHT
        });
    });
}

/* The viewport box, and the scroll position the aside reports. Cheap enough
   to run on every scroll frame: a height write and a transform, no repaint
   and no layout read beyond the scroller's own metrics. */
function updateExplorerOverviewViewport(parts) {
    const track = parts.aside.clientHeight;
    const content = Math.max(1, parts.code.scrollHeight);
    const visible = parts.code.clientHeight;
    if (!track) {
        return;
    }
    const boxHeight = Math.min(
        track,
        Math.max(EXPLORER_OVERVIEW_VIEWPORT_MIN_HEIGHT, (visible / content) * track)
    );
    const top = Math.max(0, Math.min(track - boxHeight, (parts.code.scrollTop / content) * track));
    parts.viewport.style.height = `${boxHeight}px`;
    parts.viewport.style.transform = `translateY(${top}px)`;
    const maxScroll = Math.max(0, content - visible);
    const progress = maxScroll > 0 ? parts.code.scrollTop / maxScroll : 0;
    parts.aside.setAttribute('aria-valuenow', String(Math.round(progress * 100)));
}

function explorerOverviewClampScroll(parts, target) {
    const maxScroll = Math.max(0, parts.code.scrollHeight - parts.code.clientHeight);
    parts.code.scrollTop = Math.max(0, Math.min(maxScroll, target));
}

/* A click or drag anywhere on the track centres that content position, which
   is what makes the column read as a scrollbar rather than a legend. */
function explorerOverviewScrubTo(parts, clientY) {
    const rect = parts.aside.getBoundingClientRect();
    const track = rect.height || 1;
    const y = Math.max(0, Math.min(track, clientY - rect.top));
    const content = Math.max(1, parts.code.scrollHeight);
    explorerOverviewClampScroll(parts, ((y / track) * content) - (parts.code.clientHeight / 2));
}

/* Returns the line of the change marker under the pointer, or 0. Only the
   change lane (plus slop) can register a hit, so the rest of the column stays
   a plain scrub track. */
function explorerOverviewMarkerLineAt(index, parts, event) {
    const markers = terminals[index]?._explorerOverviewMarkers;
    if (!markers?.length) {
        return 0;
    }
    const rect = parts.aside.getBoundingClientRect();
    if ((event.clientX - rect.left) > EXPLORER_OVERVIEW_LANE_WIDTH + EXPLORER_OVERVIEW_HIT_SLOP) {
        return 0;
    }
    const y = event.clientY - rect.top;
    let line = 0;
    let closest = EXPLORER_OVERVIEW_HIT_SLOP + 1;
    markers.forEach(marker => {
        const distance = y < marker.top
            ? marker.top - y
            : (y > marker.bottom ? y - marker.bottom : 0);
        if (distance <= EXPLORER_OVERVIEW_HIT_SLOP && distance < closest) {
            closest = distance;
            line = marker.line;
        }
    });
    return line;
}

function releaseExplorerOverviewDrag(index, parts, event) {
    const pane = terminals[index];
    if (!pane || pane._explorerOverviewDrag === null || pane._explorerOverviewDrag === undefined) {
        return;
    }
    const pointerId = pane._explorerOverviewDrag;
    pane._explorerOverviewDrag = null;
    parts.aside.classList.remove('is-scrubbing');
    // lostpointercapture arrives *because* the capture is already gone.
    if (event?.type !== 'lostpointercapture' && parts.aside.hasPointerCapture?.(pointerId)) {
        parts.aside.releasePointerCapture(pointerId);
    }
}

function wireExplorerOverview(index, parts) {
    const pane = terminals[index];
    if (!pane || parts.aside.dataset.bound === 'true') {
        return;
    }
    parts.aside.dataset.bound = 'true';

    // Passive: the box moves in its own frame, it never blocks the scroll.
    parts.code.addEventListener('scroll', () => {
        scheduleExplorerOverviewViewport(index);
    }, { passive: true });

    parts.aside.addEventListener('pointerdown', event => {
        if (event.button !== 0) {
            return;
        }
        event.preventDefault();
        /* A marker click is a jump to that change, not a scrub to that
           fraction of the file: scroll the row into view and flash it, the
           same affordance a repository-search hit already uses. */
        const line = explorerOverviewMarkerLineAt(index, parts, event);
        if (line) {
            scrollExplorerSourceToLine(index, line);
            return;
        }
        pane._explorerOverviewDrag = event.pointerId;
        parts.aside.classList.add('is-scrubbing');
        parts.aside.setPointerCapture?.(event.pointerId);
        explorerOverviewScrubTo(parts, event.clientY);
    });

    parts.aside.addEventListener('pointermove', event => {
        if (pane._explorerOverviewDrag !== event.pointerId) {
            return;
        }
        event.preventDefault();
        explorerOverviewScrubTo(parts, event.clientY);
    });

    ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(type => {
        parts.aside.addEventListener(type, event => {
            releaseExplorerOverviewDrag(index, parts, event);
        });
    });

    // The column is not a second scrollable surface; a wheel over it belongs
    // to the text it stands beside.
    parts.aside.addEventListener('wheel', event => {
        const delta = event.deltaMode === 1
            ? event.deltaY * EXPLORER_OVERVIEW_WHEEL_LINE_HEIGHT
            : event.deltaY;
        if (!delta) {
            return;
        }
        event.preventDefault();
        explorerOverviewClampScroll(parts, parts.code.scrollTop + delta);
    }, { passive: false });

    // A pane resize re-wraps the text: both the geometry and the box move.
    pane._explorerOverviewObserver?.disconnect();
    pane._explorerOverviewObserver = null;
    if (typeof window.ResizeObserver === 'function') {
        const observer = new window.ResizeObserver(() => scheduleExplorerOverviewSync(index));
        observer.observe(parts.frame);
        pane._explorerOverviewObserver = observer;
    }
}

function syncExplorerOverview(index) {
    const parts = explorerOverviewParts(index);
    const pane = terminals[index];
    if (!parts || !pane) {
        return;
    }
    wireExplorerOverview(index, parts);
    /* A panel switched to Preview or Diff is `hidden`, so every offset inside
       it reads 0 — measuring there would cache a geometry describing nothing.
       Coming back re-renders the source, which syncs again. */
    if (parts.frame.hidden) {
        return;
    }
    const geometry = explorerOverviewGeometry(index, parts);
    /* Nothing rendered to survey — an empty file, the in-place editor's
       textarea, a panel switched away — so the column leaves the layout
       instead of standing there showing the last file's shape. */
    parts.aside.hidden = !geometry;
    if (!geometry) {
        return;
    }
    paintExplorerOverview(index, parts, geometry);
    updateExplorerOverviewViewport(parts);
}

function scheduleExplorerOverviewSync(index) {
    const pane = terminals[index];
    if (!pane) {
        return;
    }
    if (typeof window.requestAnimationFrame !== 'function') {
        syncExplorerOverview(index);
        return;
    }
    if (pane._explorerOverviewFrame) {
        return;
    }
    pane._explorerOverviewFrame = window.requestAnimationFrame(() => {
        pane._explorerOverviewFrame = 0;
        syncExplorerOverview(index);
    });
}

function scheduleExplorerOverviewViewport(index) {
    const pane = terminals[index];
    if (!pane) {
        return;
    }
    const update = () => {
        const parts = explorerOverviewParts(index);
        if (parts && !parts.aside.hidden) {
            updateExplorerOverviewViewport(parts);
        }
    };
    if (typeof window.requestAnimationFrame !== 'function') {
        update();
        return;
    }
    if (pane._explorerOverviewViewportFrame) {
        return;
    }
    pane._explorerOverviewViewportFrame = window.requestAnimationFrame(() => {
        pane._explorerOverviewViewportFrame = 0;
        update();
    });
}
