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

   Entry points — the only functions other modules may call:
   - loadExplorerChangeMarks(index)          fetch + cache + paint
   - applyExplorerChangeMarks(index)         cheap re-paint after a re-render
   - refreshExplorerOverview(index, reason)  invalidate + reload
   - teardownExplorerOverview(index)         drop the cached model          */

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

/* Re-paint the cached model onto the currently rendered rows. Runs after
   every renderExplorerSource() rebuild, so it must stay cheap: one
   querySelectorAll pass, attribute writes only, no layout reads. Rows are
   rebuilt from scratch on every render, so there is never anything to clean
   up — a missing model or an ineligible pane simply means no marks. */
function applyExplorerChangeMarks(index) {
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
}
