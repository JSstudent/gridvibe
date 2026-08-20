/* GridVibe explorer Diff view — extracted from explorer-viewer.js by the
   move-only Diff-domain split (guardrail 6's standing extraction trigger:
   "the next change to the Diff view extracts explorer-diff.js").

   Owns the file viewer's Diff panel end to end: the Diff2HtmlUI configuration,
   the wrapped-row height sync and its ResizeObserver, the diff parse used by
   both the panel and the Source gutter's change marks, the per-line and
   per-block undo controls, the Diff2Html renderer and the handwritten
   side-by-side fallback, the empty-diff fallback, the split toggle, and the
   bounded `git/diff` load itself.

   It *began* as a move, not a rewrite: at the extraction commit every function
   below was byte-for-byte the one that stood in explorer-viewer.js, including
   its four-space indentation, so that one diff read as a relocation and the
   Diff-domain tests passed untouched — which is the standard guardrail 6 sets
   for a pure move. That is history, not a description of the file: later
   commits gave it the tiered renderers and the worker-backed large-diff parse,
   so roughly a third of its top-level functions have changed in place and
   several (explorerDiffTierBannerHtml, explorerDiffHunkStart,
   renderExplorerLargeDiff, paintExplorerSideBySideDiff, the two worker
   lookups) never stood in the viewer at all. Do not read a function here as
   evidence of what the viewer used to do; read git history for that.

   Both files are plain classic scripts sharing one global scope, so the split
   costs no accessor plumbing — the viewer still calls renderExplorerDiff() and
   loadExplorerDiff(), explorer-overview.js still calls
   explorerDiffChangeBlocks(), and this file still calls applyExplorerSearch()
   exactly as before. Loaded directly after explorer-viewer.js, and after
   explorer-worker-core.js, whose parseSideBySideDiff() backs the fallback
   renderer here (guarded — see explorerParsedDiffModel()).

   What deliberately stayed behind: explorerHasGitDiff(),
   explorerDiffCacheKey(), explorerDiffSidebarStatusHtml(),
   explorerGitOpenCommitDiff() and ensureExplorerDiffExpandedCommits() — those
   name a diff but belong to the Git *sidebar*, whose own extraction is a
   separate cut. */

    /* Diff2HtmlUI configuration.
       `matching: 'words'` + `diffStyle: 'char'` give character-level intraline
       emphasis and LCS-based line matching instead of the fallback renderer's
       FIFO pairing; the comparison limits are explicit so pathological diffs
       stay responsive (Diff2Html documents line matching as the main cost). */
    function explorerDiff2HtmlConfig(tier) {
        const overrides = explorerTierPolicy()?.diffTierConfigOverrides(tier) || {};
        return {
            outputFormat: 'side-by-side',
            drawFileList: false,
            fileContentToggle: false,
            matching: 'words',
            diffStyle: 'char',
            highlight: true,
            synchronisedScroll: false,
            matchingMaxComparisons: 1500,
            /* Diff2Html's own default. A line longer than this gets a plain
               red/green block and no intraline ins/del at all, so the previous
               2000 silently dropped emphasis on generated SQL, minified assets,
               and long single-statement lines. The char diff is O(n·d) in the
               edit distance, which stays cheap for the usual small edit inside
               a long line; the bounded diff payload (256 KiB / 4,000 lines)
               caps how many pairs can reach it. */
            maxLineLengthHighlight: 10000,
            /* The size tier's degradations, last so they win. `matching`,
               `diffStyle` and `highlight` are the three keys that degrade
               *gracefully* inside diff2html: rows, line numbers, side-by-side
               and — the one that matters — the per-line and per-block undo
               buttons wired onto those rows all survive, and only the
               intraline emphasis and the syntax colour go.

               `diffMaxChanges` / `diffMaxLineLength` are deliberately absent.
               They read like a tier and are a refusal: exceeding either sets
               isTooBig, zeroes the line counts, empties `blocks` and renders
               "Diff too big to be displayed" — and the fallback guard below
               still finds a `.d2h-file-wrapper` afterwards, so the handwritten
               renderer would never even be reached. */
            ...overrides
        };
    }

    /* Both size banners for the Diff panel, in the order the reader needs
       them: what is missing from the *content* first (truncation), then what
       is missing from its *presentation* (the tier). Same role="status"
       in-pane shape as the Source tier's notice, for the same reason — an
       unexplained loss of colour reads as a bug. */
    function explorerDiffTierBannerHtml(tier) {
        const notice = explorerTierPolicy()?.diffTierNotice(tier);
        if (!notice) {
            return '';
        }
        return '<div class="explorer-diff-tier-notice" role="status">'
            + `<strong>${escHtml(notice.title)}</strong> ${escHtml(notice.detail)}`
            + '</div>';
    }

    function explorerDiffTruncationBannerHtml(pane) {
        if (!pane || !pane._explorerDiffTruncated) {
            return '';
        }
        return '<div class="explorer-diff-truncated" role="status">'
            + 'Diff truncated to 256 KiB / 4,000 lines — the change shown is incomplete.'
            + '</div>';
    }

    function synchroniseExplorerDiffScrollbars(host) {
        const sides = host?._explorerDiffSides || [];
        const spacers = host?._explorerDiffScrollSpacers || [];
        sides.forEach((side, sideIndex) => {
            const spacer = spacers[sideIndex];
            if (spacer) {
                spacer.style.width = `${Math.max(side.clientWidth, side.scrollWidth)}px`;
            }
        });
    }

    function synchroniseExplorerDiffWrappedRows(host) {
        const sides = host?._explorerDiffSides || [];
        const rowsBySide = sides.map(side => [
            ...side.querySelectorAll('.d2h-diff-tbody > tr')
        ]);
        rowsBySide.flat().forEach(row => {
            row.style.height = '';
        });
        if (sides.length !== 2
            || !host.closest('.explorer-diff-content')?.classList.contains('wrap-lines')) {
            return;
        }

        const rowCount = Math.max(...rowsBySide.map(rows => rows.length), 0);
        /* Read every height, then write every height — two passes, never one.
           A row's own natural height does not depend on its neighbours', so
           the values are identical either way; what differs is the cost.
           Reading `getBoundingClientRect()` flushes pending layout, and
           writing `style.height` invalidates it again, so a single loop that
           read a pair and wrote it before moving on forced a synchronous
           reflow *per row pair* — the stutter a wrapped diff had on every
           resize and every search repaint. */
        const heightsByRow = [];
        for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
            heightsByRow.push(rowsBySide.map(rows => (
                rows[rowIndex] ? rows[rowIndex].getBoundingClientRect().height : null
            )));
        }
        for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
            const heights = heightsByRow[rowIndex];
            const maxHeight = Math.max(...heights.filter(height => height !== null), 0);
            rowsBySide.forEach((rows, sideIndex) => {
                const row = rows[rowIndex];
                const height = heights[sideIndex];
                if (row && height !== null && maxHeight - height > 0.5) {
                    row.style.height = `${maxHeight}px`;
                }
            });
        }
    }

    function scheduleExplorerDiffScrollbarSync(host) {
        if (!host || host._explorerDiffScrollbarFrame) {
            return;
        }
        const sync = () => {
            host._explorerDiffScrollbarFrame = null;
            if (host.isConnected) {
                synchroniseExplorerDiffWrappedRows(host);
                synchroniseExplorerDiffScrollbars(host);
            }
        };
        if (typeof window.requestAnimationFrame === 'function') {
            host._explorerDiffScrollbarFrame = window.requestAnimationFrame(sync);
        } else {
            sync();
        }
    }

    function observeExplorerDiffLayout(host) {
        const filesDiff = host?.querySelector('.d2h-files-diff');
        const sides = filesDiff
            ? [...filesDiff.querySelectorAll(':scope > .d2h-file-side-diff')]
            : [];
        if (sides.length !== 2) {
            return;
        }

        const scrollbars = document.createElement('div');
        scrollbars.className = 'explorer-diff-horizontal-scrollbars';
        scrollbars.setAttribute('aria-hidden', 'true');
        const tracks = sides.map((side, sideIndex) => {
            const track = document.createElement('div');
            track.className = 'explorer-diff-horizontal-scroll';
            track.dataset.explorerDiffSide = sideIndex === 0 ? 'left' : 'right';
            const spacer = document.createElement('div');
            spacer.className = 'explorer-diff-horizontal-scroll-spacer';
            track.appendChild(spacer);
            track.addEventListener('scroll', () => {
                side.scrollLeft = track.scrollLeft;
            });
            scrollbars.appendChild(track);
            return { track, spacer };
        });
        host.appendChild(scrollbars);
        host._explorerDiffSides = sides;
        host._explorerDiffScrollTracks = tracks.map(item => item.track);
        host._explorerDiffScrollSpacers = tracks.map(item => item.spacer);
        scheduleExplorerDiffScrollbarSync(host);

        sides.forEach((side, sideIndex) => {
            side.addEventListener('wheel', event => {
                const horizontalDelta = event.deltaX || (event.shiftKey ? event.deltaY : 0);
                if (!horizontalDelta) {
                    return;
                }
                event.preventDefault();
                tracks[sideIndex].track.scrollLeft += horizontalDelta;
            }, { passive: false });
        });

        if (typeof window.ResizeObserver === 'function') {
            const observer = new window.ResizeObserver(entries => {
                const width = entries[0]?.contentRect?.width;
                if (Number.isFinite(width)
                    && Math.abs(width - (host._explorerDiffObservedWidth || 0)) > 0.5) {
                    host._explorerDiffObservedWidth = width;
                    scheduleExplorerDiffScrollbarSync(host);
                }
            });
            host._explorerDiffObservedWidth = filesDiff.getBoundingClientRect().width;
            observer.observe(filesDiff);
            host._explorerDiffResizeObserver = observer;
        }
    }

    function disconnectExplorerDiffLayout(host) {
        host?._explorerDiffResizeObserver?.disconnect();
        if (host?._explorerDiffScrollbarFrame && typeof window.cancelAnimationFrame === 'function') {
            window.cancelAnimationFrame(host._explorerDiffScrollbarFrame);
        }
    }

    /* A file opened from the Changes sidebar carries an explicit worktree mode.
       A file opened directly uses the legacy HEAD diff; that view is equally
    safe for line undo only when the index is clean, because HEAD→worktree
       then contains exactly the unstaged worktree changes. */
    function explorerDiffShowsOnlyWorktreeChanges(pane) {
        if (pane && pane._explorerDiffMode === 'worktree') {
            return true;
        }
        if (!pane || (pane._explorerDiffMode && pane._explorerDiffMode !== 'head')) {
            return false;
        }
        const git = pane._explorerGit;
        if (!git || git.status === 'conflicted') {
            return false;
        }
        const indexCode = git.index_status || ' ';
        const worktreeCode = git.worktree_status || ' ';
        return explorerGitCodeUnmodified(indexCode)
            && !explorerGitCodeUnmodified(worktreeCode);
    }

    function explorerCanUndoDiffLine(pane) {
        return Boolean(
            pane
            && pane._explorerMode === 'file'
            && explorerDiffShowsOnlyWorktreeChanges(pane)
            && !pane._explorerDiffCommit
            && pane._explorerFileEditable
            && !pane._explorerFileTruncated
            && !pane._explorerDiffTruncated
            && !pane._explorerDiffUndoBusy
            && !pane._explorerEdit
            && pane._explorerFileRevision
            && !String(pane._explorerDiffContent || '').includes('\\ No newline at end of file')
        );
    }

    /* Record the current worktree insertion point for every deleted line. A
       deleted line has no new-side line number of its own, so this small map is
       what lets a one-line restore put it back at the exact hunk position. */
    function explorerDiffDeletionInsertions(diff) {
        const insertions = new Map();
        let oldLine = 0;
        let newLine = 0;
        String(diff || '').split(/\r?\n/).forEach(line => {
            const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
            if (hunk) {
                oldLine = Number(hunk[1]);
                newLine = Number(hunk[2]);
                return;
            }
            if (!oldLine && !newLine) {
                return;
            }
            if (line.startsWith('-') && !line.startsWith('---')) {
                insertions.set(oldLine, newLine);
                oldLine += 1;
            } else if (line.startsWith('+') && !line.startsWith('+++')) {
                newLine += 1;
            } else if (line.startsWith(' ')) {
                oldLine += 1;
                newLine += 1;
            }
        });
        return insertions;
    }

    /* Group the patch into change blocks: a maximal run of consecutive changed
       lines with no context line between them — the "paragraph" a reader sees
       as one edit. Undoing a block swaps its worktree lines (`expected`) back
       to the HEAD lines it replaced (`replacement`) in a single save, so a
       20-line rewrite is one click instead of twenty. `line` is the 1-based
       worktree line the run starts at (for a pure deletion, where the removed
       lines go back in); `oldLine` is the matching HEAD line. */
    /* Where a hunk's side actually starts.

       A unified-diff range of length zero names the line *before* the change
       rather than the line the change sits at: `@@ -19,0 +20,2 @@` is two
       lines added after old line 19, and `@@ -31 +32,0 @@` is old line 31
       removed from after new line 31. Every non-empty range names its own
       first line, and with context present the counters walk the context
       lines and reach the right number by themselves — which is why this only
       ever mattered once the change marks started asking for `-U0`, where a
       pure insertion or deletion is the whole hunk and the header is the only
       thing that states the position.

       Normalizing it here is what makes the narrow read describe the same
       blocks as the wide one; without it a deletion wedge sat one row high on
       a -U0 patch and one row lower on the -U3 patch the Diff panel caches,
       so the same change marked two different places depending on which
       surface had been opened first. A whole-file create or delete (`-0,0` /
       `+0,0`) has no line to point past and stays at 0. */
    function explorerDiffHunkStart(rawStart, rawCount) {
        const start = Number(rawStart);
        if (!Number.isFinite(start)) {
            return 0;
        }
        const count = rawCount === undefined ? 1 : Number(rawCount);
        return start && count === 0 ? start + 1 : start;
    }

    function explorerDiffChangeBlocks(diff) {
        const blocks = [];
        let oldLine = 0;
        let newLine = 0;
        let run = null;
        const startRun = () => {
            if (!run) {
                run = { kind: 'block', line: newLine, oldLine, expected: [], replacement: [] };
            }
            return run;
        };
        const flushRun = () => {
            if (run && (run.expected.length || run.replacement.length)) {
                blocks.push(run);
            }
            run = null;
        };
        String(diff || '').split(/\r?\n/).forEach(line => {
            const hunk = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/);
            if (hunk) {
                flushRun();
                oldLine = explorerDiffHunkStart(hunk[1], hunk[2]);
                newLine = explorerDiffHunkStart(hunk[3], hunk[4]);
                return;
            }
            if (!oldLine && !newLine) {
                return;
            }
            if (line.startsWith('-') && !line.startsWith('---')) {
                startRun().replacement.push(line.slice(1));
                oldLine += 1;
                return;
            }
            if (line.startsWith('+') && !line.startsWith('+++')) {
                startRun().expected.push(line.slice(1));
                newLine += 1;
                return;
            }
            flushRun();
            if (line.startsWith(' ')) {
                oldLine += 1;
                newLine += 1;
            }
        });
        flushRun();
        return blocks;
    }

    /* Index every changed line of every block by its rendered line number so a
       diff row can be mapped back to the block it belongs to. */
    function explorerDiffBlockLineIndex(blocks) {
        const byNewLine = new Map();
        const byOldLine = new Map();
        blocks.forEach((block, position) => {
            block.id = String(position + 1);
            block.rows = Math.max(block.expected.length, block.replacement.length);
            block.expected.forEach((_, offset) => byNewLine.set(block.line + offset, block));
            block.replacement.forEach((_, offset) => byOldLine.set(block.oldLine + offset, block));
        });
        return { byNewLine, byOldLine };
    }

    function explorerDiffRowBlock(blockIndex, oldLine, newLine) {
        if (newLine?.type === 'add') {
            const block = blockIndex.byNewLine.get(newLine.number);
            if (block) {
                return block;
            }
        }
        if (oldLine?.type === 'delete') {
            return blockIndex.byOldLine.get(oldLine.number) || null;
        }
        return null;
    }

    function explorerRenderedDiffLine(row, {
        cellSelector,
        numberSelector,
        codeSelector,
        addClass,
        deleteClass
    }) {
        const cell = row?.querySelector(cellSelector);
        const numberCell = cell?.matches(numberSelector)
            ? cell
            : cell?.querySelector(numberSelector);
        const number = Number.parseInt(numberCell?.textContent || '', 10);
        if (!cell || !numberCell || !Number.isFinite(number)) {
            return null;
        }
        const typeHost = cell.matches(numberSelector) ? row : cell;
        const type = typeHost.querySelector(`.${addClass}`)
            || typeHost.classList.contains(addClass)
            ? 'add'
            : (
                typeHost.querySelector(`.${deleteClass}`)
                || typeHost.classList.contains(deleteClass)
                    ? 'delete'
                    : 'context'
            );
        return {
            type,
            number,
            text: (cell.querySelector(codeSelector) || row.querySelector(codeSelector))?.textContent || '',
            numberCell
        };
    }

    function explorerDiffUndoAction(oldLine, newLine, deletionInsertions) {
        const deleted = oldLine?.type === 'delete';
        const added = newLine?.type === 'add';
        if (deleted && added) {
            return {
                kind: 'replace',
                line: newLine.number,
                expected: newLine.text,
                replacement: oldLine.text
            };
        }
        if (added) {
            return {
                kind: 'remove',
                line: newLine.number,
                expected: newLine.text,
                replacement: ''
            };
        }
        if (deleted) {
            const insertLine = deletionInsertions.get(oldLine.number);
            if (Number.isFinite(insertLine) && insertLine > 0) {
                return {
                    kind: 'insert',
                    line: insertLine,
                    expected: '',
                    replacement: oldLine.text
                };
            }
        }
        return null;
    }

    function registerExplorerDiffUndoAction(pane, actionId, action) {
        if (!(pane._explorerDiffUndoActions instanceof Map)) {
            pane._explorerDiffUndoActions = new Map();
        }
        pane._explorerDiffUndoActions.set(actionId, action);
        return actionId;
    }

    function attachExplorerDiffUndoButton(index, numberCell, action) {
        const pane = terminals[index];
        if (!pane || !numberCell || !action) {
            return;
        }
        if (!(pane._explorerDiffUndoActions instanceof Map)) {
            pane._explorerDiffUndoActions = new Map();
        }
        const actionId = registerExplorerDiffUndoAction(
            pane,
            String(pane._explorerDiffUndoActions.size + 1),
            action
        );
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'explorer-diff-undo-line';
        button.dataset.explorerDiffUndoLine = actionId;
        button.title = 'Undo this unstaged line change';
        button.setAttribute('aria-label', 'Undo this unstaged line change');
        button.innerHTML = EXPLORER_GIT_REVERT_ICON;
        button.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            undoExplorerDiffChange(index, actionId);
        });
        numberCell.appendChild(button);
    }

    /* Tag every rendered row of a multi-line block, and hang one "Undo block"
       pill off the block's first row. Single-line blocks are left to the
       per-line button — a pill there would say the same thing twice. */
    function attachExplorerDiffUndoBlockButton(index, block, rows, numberCell) {
        const pane = terminals[index];
        if (!pane || !block || block.rows < 2) {
            return;
        }
        rows.filter(Boolean).forEach(row => {
            row.dataset.explorerDiffBlock = block.id;
        });
        const actionId = `block-${block.id}`;
        if (!numberCell || pane._explorerDiffUndoActions?.has(actionId)) {
            return;
        }
        registerExplorerDiffUndoAction(pane, actionId, block);
        const label = `Undo this block of ${block.rows} unstaged line changes`;
        const pill = document.createElement('button');
        pill.type = 'button';
        pill.className = 'explorer-diff-undo-block';
        pill.dataset.explorerDiffUndoBlock = block.id;
        pill.dataset.explorerDiffUndoAction = actionId;
        pill.title = label;
        pill.setAttribute('aria-label', label);
        pill.innerHTML = `${EXPLORER_GIT_REVERT_ICON}<span>Undo block (${block.rows})</span>`;
        pill.addEventListener('click', event => {
            event.preventDefault();
            event.stopPropagation();
            undoExplorerDiffChange(index, actionId);
        });
        numberCell.classList.add('has-block-undo');
        numberCell.appendChild(pill);
    }

    /* Hovering anywhere inside a block reveals that block's pill, which lives
       on a different row (and, side by side, a different table) — so this is a
       pair of delegated listeners on the diff root rather than CSS. */
    function setExplorerDiffHoveredBlock(root, blockId) {
        if (root._explorerDiffHoveredBlock === blockId) {
            return;
        }
        root._explorerDiffHoveredBlock = blockId;
        root.querySelectorAll('[data-explorer-diff-undo-block]').forEach(pill => {
            pill.classList.toggle(
                'is-visible',
                Boolean(blockId) && pill.dataset.explorerDiffUndoBlock === blockId
            );
        });
    }

    function wireExplorerDiffBlockHover(root) {
        /* The fallback renderer re-wires the same persistent container on every
           render, so the listeners are attached once per element. */
        if (root._explorerDiffBlockHoverWired) {
            root._explorerDiffHoveredBlock = '';
            return;
        }
        root._explorerDiffBlockHoverWired = true;
        root.addEventListener('mouseover', event => {
            const row = event.target?.closest?.('[data-explorer-diff-block]');
            setExplorerDiffHoveredBlock(root, row?.dataset.explorerDiffBlock || '');
        });
        root.addEventListener('mouseleave', () => {
            setExplorerDiffHoveredBlock(root, '');
        });
    }

    function wireExplorerDiffUndoControls(index, root) {
        const pane = terminals[index];
        if (pane) {
            pane._explorerDiffUndoActions = new Map();
        }
        if (!root || !explorerCanUndoDiffLine(pane)) {
            return;
        }
        const deletionInsertions = explorerDiffDeletionInsertions(pane._explorerDiffContent);
        const blockIndex = explorerDiffBlockLineIndex(
            explorerDiffChangeBlocks(pane._explorerDiffContent)
        );
        wireExplorerDiffBlockHover(root);
        const diff2HtmlSides = root._explorerDiffSides || [];
        if (diff2HtmlSides.length === 2) {
            const rowsBySide = diff2HtmlSides.map(side => [
                ...side.querySelectorAll('.d2h-diff-tbody > tr')
            ]);
            const rowCount = Math.max(rowsBySide[0].length, rowsBySide[1].length);
            for (let rowIndex = 0; rowIndex < rowCount; rowIndex += 1) {
                const oldLine = explorerRenderedDiffLine(rowsBySide[0][rowIndex], {
                    cellSelector: '.d2h-code-side-linenumber',
                    numberSelector: '.d2h-code-side-linenumber',
                    codeSelector: '.d2h-code-line-ctn',
                    addClass: 'd2h-ins',
                    deleteClass: 'd2h-del'
                });
                const newLine = explorerRenderedDiffLine(rowsBySide[1][rowIndex], {
                    cellSelector: '.d2h-code-side-linenumber',
                    numberSelector: '.d2h-code-side-linenumber',
                    codeSelector: '.d2h-code-line-ctn',
                    addClass: 'd2h-ins',
                    deleteClass: 'd2h-del'
                });
                const action = explorerDiffUndoAction(oldLine, newLine, deletionInsertions);
                attachExplorerDiffUndoButton(index, (newLine || oldLine)?.numberCell, action);
                attachExplorerDiffUndoBlockButton(
                    index,
                    explorerDiffRowBlock(blockIndex, oldLine, newLine),
                    [rowsBySide[0][rowIndex], rowsBySide[1][rowIndex]],
                    (newLine || oldLine)?.numberCell
                );
            }
            return;
        }

        root.querySelectorAll('.explorer-diff-row').forEach(row => {
            const options = {
                numberSelector: '.explorer-diff-line-number',
                codeSelector: '.explorer-diff-line-code',
                addClass: 'add',
                deleteClass: 'delete'
            };
            const oldLine = explorerRenderedDiffLine(row, {
                ...options,
                cellSelector: '.explorer-diff-cell.old'
            });
            const newLine = explorerRenderedDiffLine(row, {
                ...options,
                cellSelector: '.explorer-diff-cell.new'
            });
            const action = explorerDiffUndoAction(oldLine, newLine, deletionInsertions);
            attachExplorerDiffUndoButton(index, (newLine || oldLine)?.numberCell, action);
            attachExplorerDiffUndoBlockButton(
                index,
                explorerDiffRowBlock(blockIndex, oldLine, newLine),
                [row],
                (newLine || oldLine)?.numberCell
            );
        });
    }

    function explorerDiffUndoContent(content, action) {
        const lines = String(content == null ? '' : content)
            .replace(/\r\n/g, '\n')
            .replace(/\r/g, '\n')
            .split('\n');
        const lineIndex = Number(action?.line) - 1;
        if (!Number.isInteger(lineIndex) || lineIndex < 0) {
            return null;
        }
        if (action.kind === 'block') {
            const expected = Array.isArray(action.expected) ? action.expected : [];
            const replacement = Array.isArray(action.replacement) ? action.replacement : [];
            if (lineIndex + expected.length > lines.length) {
                return null;
            }
            const stale = expected.some((text, offset) => lines[lineIndex + offset] !== text);
            if (stale) {
                return null;
            }
            lines.splice(lineIndex, expected.length, ...replacement);
            return lines.join('\n');
        }
        if (action.kind === 'insert') {
            if (lineIndex > lines.length) {
                return null;
            }
            lines.splice(lineIndex, 0, action.replacement);
            return lines.join('\n');
        }
        if (lineIndex >= lines.length || lines[lineIndex] !== action.expected) {
            return null;
        }
        if (action.kind === 'replace') {
            lines[lineIndex] = action.replacement;
        } else if (action.kind === 'remove') {
            lines.splice(lineIndex, 1);
        } else {
            return null;
        }
        return lines.join('\n');
    }

    function setExplorerDiffUndoBusy(index, busy) {
        const pane = terminals[index];
        if (pane) {
            pane._explorerDiffUndoBusy = Boolean(busy);
        }
        document.querySelectorAll(
            `#explorer-diff-code-${index} :is([data-explorer-diff-undo-line], [data-explorer-diff-undo-block])`
        ).forEach(button => {
            button.disabled = Boolean(busy);
            button.classList.toggle('is-busy', Boolean(busy));
        });
    }

    function explorerDiffUndoCopy(action) {
        if (action.kind === 'block') {
            return {
                title: 'Undo block of changes?',
                copy: `Undo these ${action.rows} unstaged line changes starting at line ${action.line}?`,
                confirmLabel: 'Undo block',
                toast: `Undid ${action.rows} line changes`
            };
        }
        return {
            title: 'Undo line change?',
            copy: `Undo this unstaged change on line ${action.line}?`,
            confirmLabel: 'Undo line',
            toast: 'Undid line change'
        };
    }

    async function undoExplorerDiffChange(index, actionId) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const action = pane?._explorerDiffUndoActions?.get(String(actionId));
        if (!pane || !sessionId || !action || !explorerCanUndoDiffLine(pane)) {
            return;
        }
        const content = explorerDiffUndoContent(pane._explorerFileContent, action);
        if (content === null) {
            showTerminalToast('This diff is stale. Refresh the file and try again.', 'error');
            return;
        }
        const copy = explorerDiffUndoCopy(action);
        const filePath = pane._explorerFilePath;
        const baseRevision = pane._explorerFileRevision;
        const confirmed = await openGenericConfirmModal({
            title: copy.title,
            copy: copy.copy,
            note: 'The file is saved immediately. Staged changes are preserved.',
            confirmLabel: copy.confirmLabel,
            danger: true
        });
        if (
            !confirmed
            || !explorerCanUndoDiffLine(pane)
            || pane._explorerFilePath !== filePath
            || pane._explorerFileRevision !== baseRevision
        ) {
            return;
        }

        const scrollState = captureExplorerFileScroll(index);
        const activeTabId = pane._explorerActiveTabId;
        setExplorerDiffUndoBusy(index, true);
        try {
            const response = await fetch(`/api/explorer/${encodeURIComponent(sessionId)}/file`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    path: filePath,
                    content,
                    base_revision: baseRevision
                })
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) {
                throw new Error(data.error || 'Could not undo this change.');
            }
            setExplorerDiffUndoBusy(index, false);
            const hasRemainingDiff = explorerHasGitDiff(data.git);
            const applied = updateExplorerFileInPlace(index, data, scrollState);
            if (!applied) {
                renderExplorerFile(index, data, {
                    scrollState,
                    openDiff: hasRemainingDiff,
                    diffMode: 'worktree',
                    tab: activeTabId
                });
            }
            if (pane._explorerGitSidebarOpen) {
                invalidateExplorerGitRepo(index);
                loadExplorerGitRepo(index);
            }
            if (pane._explorerTreeSidebarOpen) {
                reloadExplorerTree(index);
            }
            showTerminalToast(`${copy.toast} in ${data.name || pane._explorerFileName || 'file'}`, 'success');
        } catch (error) {
            console.error('[GridVibe Sessions] Explorer diff undo failed:', error);
            setExplorerDiffUndoBusy(index, false);
            showTerminalToast(error.message || 'Could not undo this change.', 'error');
        }
    }

    /* Render the patch with the pinned Diff2Html build, reusing the pinned
       Highlight.js instance for syntax colour. Returns false — so the caller
       falls back to the tolerant handwritten side-by-side renderer — when the
       assets are missing, Diff2Html throws, or it parses the patch to nothing
       (e.g. a partial patch without a file header). */
    function renderExplorerDiffWithDiff2Html(index, code, diff, banner, tier) {
        if (typeof window === 'undefined' || !window.Diff2HtmlUI || !window.hljs) {
            return false;
        }
        try {
            const host = document.createElement('div');
            host.className = 'explorer-diff2html';
            const ui = new window.Diff2HtmlUI(host, diff, explorerDiff2HtmlConfig(tier), window.hljs);
            // draw() already runs highlightCode() because the config sets
            // `highlight: true`. Calling it a second time re-highlights markup
            // that is already highlighted, which nests a duplicate hljs span
            // inside every existing one.
            ui.draw();
            if (!host.querySelector('.d2h-diff-table, .d2h-code-line, .d2h-file-wrapper')) {
                return false;
            }
            code.innerHTML = banner;
            code.appendChild(host);
            observeExplorerDiffLayout(host);
            wireExplorerDiffUndoControls(index, host);
            return true;
        } catch (error) {
            console.error('[GridVibe Sessions] Diff2Html render failed:', error);
            return false;
        }
    }

    function explorerDiffWorkerClient() {
        return (typeof window !== 'undefined' && window.GridVibeExplorerWorkers) || null;
    }

    function explorerDiffWorkerCore() {
        return (typeof window !== 'undefined' && window.GridVibeExplorerWorkerCore) || null;
    }

    /* The handwritten side-by-side parse, wherever it runs on this thread. It
       lives in explorer-worker-core.js so the worker and the page share one
       implementation, which makes that module a dependency of the *fallback*
       renderer — the path taken whenever Diff2Html is unavailable or the
       worker failed. Dereferencing the lookup directly turned a page that
       somehow loaded without it into a TypeError on the one path that exists
       to survive a missing dependency, so it degrades here like every other
       looked-up policy in this change set: null, and the callers paint the
       empty-diff message instead of throwing. */
    function explorerParsedDiffModel(diff) {
        const core = explorerDiffWorkerCore();
        return core ? core.parseSideBySideDiff(diff) : null;
    }

    /* Reuses the empty-diff surface rather than introducing a second one, but
       says something different: there *is* a patch and this build cannot lay
       it out. "No Git diff for selected file" would be a lie the reader would
       act on by looking for changes that are there. */
    const EXPLORER_DIFF_UNPARSEABLE_HTML =
        '<span class="explorer-diff-empty">Diff view unavailable — the diff renderer failed to load. Reload the page to try again.</span>';

    function paintExplorerSideBySideDiff(index, code, banner, model) {
        code.innerHTML = banner + renderExplorerSideBySideDiffModel(index, model);
        wireExplorerDiffUndoControls(index, code);
    }

    /* Only the large tier comes here: small/medium diffs still use Diff2Html's
       DOM renderer synchronously. The worker owns the handwritten parse, while
       the page retains HTML creation and the undo wiring. A matching pending
       job is shared by repaint/search callers; a different patch aborts it.
       The cache is the parsed model, never rendered HTML or user state. */
    function renderExplorerLargeDiff(index, pane, code, diff, banner) {
        const cached = pane._explorerDiffModelCache;
        if (cached && cached.diff === diff) {
            paintExplorerSideBySideDiff(index, code, banner, cached.model);
            return Promise.resolve(true);
        }
        const previous = pane._explorerDiffParsePending;
        if (previous && previous.diff === diff) {
            return previous.promise;
        }
        const workers = explorerDiffWorkerClient();
        if (!workers?.available?.()) {
            if (previous) {
                pane._explorerDiffParsePending = null;
                cancelExplorerRequestSlot(pane, 'diffParse');
            }
            const model = explorerParsedDiffModel(diff);
            if (!model) {
                code.innerHTML = banner + EXPLORER_DIFF_UNPARSEABLE_HTML;
                return Promise.resolve(false);
            }
            pane._explorerDiffModelCache = { diff, model };
            paintExplorerSideBySideDiff(index, code, banner, model);
            return Promise.resolve(true);
        }

        const pending = { diff, promise: null };
        pane._explorerDiffParsePending = pending;
        code.innerHTML = banner
            + '<span class="explorer-diff-empty">Rendering large diff…</span>';
        pending.promise = workers.parseDiff(diff, {
            signal: explorerRequestSignal(pane, 'diffParse')
        }).then(model => {
            if (pane._explorerDiffParsePending !== pending) {
                return false;
            }
            pane._explorerDiffParsePending = null;
            if (pane._explorerDiffContent !== diff
                || document.getElementById(`explorer-diff-code-${index}`) !== code) {
                return false;
            }
            pane._explorerDiffModelCache = { diff, model };
            paintExplorerSideBySideDiff(index, code, banner, model);
            return true;
        }).catch(error => {
            if (pane._explorerDiffParsePending !== pending) {
                return false;
            }
            pane._explorerDiffParsePending = null;
            if (explorerIsAbortError(error)) {
                return false;
            }
            console.error('[GridVibe Sessions] Explorer diff worker failed:', error);
            if (pane._explorerDiffContent !== diff
                || document.getElementById(`explorer-diff-code-${index}`) !== code) {
                return false;
            }
            const model = explorerParsedDiffModel(diff);
            if (!model) {
                code.innerHTML = banner + EXPLORER_DIFF_UNPARSEABLE_HTML;
                return false;
            }
            pane._explorerDiffModelCache = { diff, model };
            paintExplorerSideBySideDiff(index, code, banner, model);
            return true;
        });
        return pending.promise;
    }

    function renderExplorerDiff(index) {
        const pane = terminals[index];
        const code = document.getElementById(`explorer-diff-code-${index}`);
        if (!pane || !code) {
            return Promise.resolve(false);
        }
        disconnectExplorerDiffLayout(code.querySelector('.explorer-diff2html'));
        const wrapLines = explorerLineWrapPreference(index, 'diff');
        code.classList.toggle('wrap-lines', wrapLines);
        const diff = pane._explorerDiffContent || '';
        if (!diff) {
            if (pane._explorerDiffParsePending) {
                pane._explorerDiffParsePending = null;
                cancelExplorerRequestSlot(pane, 'diffParse');
            }
            code.innerHTML = '<span class="explorer-diff-empty">No Git diff for selected file.</span>';
            return Promise.resolve(true);
        }
        /* The tier is recomputed from the patch on every render rather than
           cached: a bounded diff is at most 256 KiB, so counting its lines
           costs nothing next to what follows, and a stale tier would be a
           silently wrong presentation. It is published on the pane because
           explorerDiffLanguage() — several calls down, once per rendered
           cell — needs it and threading it through every cell renderer would
           be worse. */
        const tier = explorerTierPolicy()?.diffTierForContent(diff) || 'small';
        pane._explorerDiffTier = tier;
        const banner = explorerDiffTruncationBannerHtml(pane) + explorerDiffTierBannerHtml(tier);
        /* The large tier goes straight to GridVibe's own side-by-side markup:
           not because diff2html would fail, but because at this size its parse,
           word matching and highlighting are the freeze. Crucially this is the
           *fallback* renderer, not a plain <pre> — it emits .explorer-diff-row,
           which is what per-line and per-block undo are wired onto, so the
           degradation costs emphasis and colour and never a mutation
           affordance. */
        if (tier === 'large') {
            return renderExplorerLargeDiff(index, pane, code, diff, banner);
        }
        if (pane._explorerDiffParsePending) {
            pane._explorerDiffParsePending = null;
            cancelExplorerRequestSlot(pane, 'diffParse');
        }
        if (!renderExplorerDiffWithDiff2Html(index, code, diff, banner, tier)) {
            code.innerHTML = banner + renderExplorerSideBySideDiff(index, diff);
            wireExplorerDiffUndoControls(index, code);
        }
        return Promise.resolve(true);
    }

    function explorerDiffLanguage(index) {
        const pane = terminals[index];
        // No language in the large tier, which reduces highlightExplorerCode()
        // to escaping — the point of the tier, since that lexer runs once per
        // rendered cell.
        if (pane?._explorerDiffTier === 'large') {
            return '';
        }
        const filePath = pane?._explorerFilePath || '';
        return normalizeExplorerLanguage(pane?._explorerFileLanguage || '') || explorerCodeLanguage(filePath);
    }

    function explorerDiffLineCodeHtml(index, text) {
        return highlightExplorerCode(String(text || ''), explorerDiffLanguage(index)) || '&nbsp;';
    }

    function explorerDiffCellHtml(index, cell, side) {
        if (!cell) {
            return `
                <div class="explorer-diff-cell empty ${side}">
                    <span class="explorer-diff-line-number"></span>
                    <span class="explorer-diff-line-code"></span>
                </div>
            `;
        }
        return `
            <div class="explorer-diff-cell ${escHtml(cell.type || 'context')} ${side}">
                <span class="explorer-diff-line-number">${cell.number ? escHtml(String(cell.number)) : ''}</span>
                <span class="explorer-diff-line-code">${explorerDiffLineCodeHtml(index, cell.text || '')}</span>
            </div>
        `;
    }

    function explorerDiffRowHtml(index, left, right) {
        if (left?.type === 'hunk') {
            return `
                <div class="explorer-diff-row">
                    <div class="explorer-diff-cell hunk">${escHtml(left.text || '')}</div>
                </div>
            `;
        }
        return `
            <div class="explorer-diff-row">
                ${explorerDiffCellHtml(index, left, 'old')}
                ${explorerDiffCellHtml(index, right, 'new')}
            </div>
        `;
    }

    function renderExplorerSideBySideDiffModel(index, model) {
        const rows = Array.isArray(model?.rows) ? model.rows : [];
        if (!rows.length) {
            return '<span class="explorer-diff-empty">No Git diff for selected file.</span>';
        }
        return `<div class="explorer-side-by-side-diff">${rows
            .map(row => explorerDiffRowHtml(index, row.left, row.right)).join('')}</div>`;
    }

    function renderExplorerSideBySideDiff(index, diff) {
        const model = explorerParsedDiffModel(diff);
        return model
            ? renderExplorerSideBySideDiffModel(index, model)
            : EXPLORER_DIFF_UNPARSEABLE_HTML;
    }

    /* Undoing the last hunk (or discarding the file's changes from the Git
       sidebar) can leave the Diff view with nothing in it. When the file's Git
       status stays non-clean — a partially staged file whose *staged* version
       survives the discard — the in-place refresh keeps the Diff panel mounted
       and the user is stranded on "No Git diff for selected file". Bounce back
       to the file's own content view instead, honouring the sticky
       source/preview preference, and hide the now-pointless Diff toggle until a
       later load finds a patch again. Commit diffs are historical and never
       empty out this way, so they are left alone. */
    function explorerFallbackFromEmptyDiff(index) {
        const pane = terminals[index];
        const list = document.getElementById(`explorer-list-${index}`);
        if (!pane || !list || pane._explorerDiffCommit) {
            return false;
        }
        if (String(pane._explorerDiffContent || '').trim()) {
            return false;
        }
        if (activeExplorerFileView(index) !== 'diff') {
            return false;
        }
        const hasSource = Boolean(document.getElementById(`explorer-code-${index}`));
        const hasPreview = Boolean(document.getElementById(`explorer-preview-${index}`));
        const preferred = pane._explorerLastFileView === 'preview' && hasPreview
            ? 'preview'
            : (hasSource ? 'source' : (hasPreview ? 'preview' : ''));
        if (!preferred) {
            return false;
        }
        // The diff panel is not being shown, so its stashed scroll would only
        // be restored later against unrelated content.
        pane._explorerPendingDiffScroll = null;
        setExplorerDiffToggleHidden(index, true);
        setExplorerFileView(index, preferred);
        return true;
    }

    function setExplorerDiffToggleHidden(index, hidden) {
        const list = document.getElementById(`explorer-list-${index}`);
        // `hidden` (not `disabled`): setExplorerEditChromeDisabled owns the
        // disabled flag on every file-view button while the editor is open.
        list?.querySelectorAll('[data-explorer-file-view="diff"]').forEach(button => {
            button.hidden = Boolean(hidden);
        });
    }

    function setExplorerDiffSplit(index, open) {
        const pane = terminals[index];
        if (!pane) {
            return;
        }
        setExplorerFileView(index, open ? 'diff' : (pane._explorerLastFileView || 'source'));
    }

    function toggleExplorerDiffSplit(index) {
        const pane = terminals[index];
        setExplorerDiffSplit(index, !pane?._explorerDiffSplit);
    }

    async function loadExplorerDiff(index) {
        const pane = terminals[index];
        const sessionId = sessionIds[index];
        const code = document.getElementById(`explorer-diff-code-${index}`);
        const diffPath = pane?._explorerFilePath || '';
        const commit = pane?._explorerDiffCommit || '';
        // Changed-file rows request a section-specific diff (worktree vs staged)
        // so a partially staged file never shows the other section's hunks;
        // commit-history rows and legacy callers fall back to the HEAD diff.
        const diffMode = commit ? 'commit' : (pane?._explorerDiffMode || 'head');
        const cacheKey = explorerDiffCacheKey(diffPath, commit, diffMode);
        if (!pane || !sessionId || !diffPath || !code) {
            await renderExplorerDiff(index);
            return;
        }
        if (pane._explorerDiffLoaded && pane._explorerDiffCacheKey === cacheKey) {
            await renderExplorerDiff(index);
            if (explorerFallbackFromEmptyDiff(index)) {
                return;
            }
            applyExplorerPendingDiffScroll(index);
            return;
        }
        /* The cache above only answers once a load has *finished*. Opening a
           file with the Diff panel up asks for the same patch twice inside one
           frame — renderExplorerFile()'s `keepDiffSplit` load, then the scroll
           restore's setExplorerFileView(index, 'diff') — and the second call
           aborted the first and refetched the identical URL, so every click on
           a changed-file row cost two requests (one always cancelled) and two
           server-side `git diff` runs. An identical in-flight load is joined,
           not superseded; a load for a *different* identity still supersedes,
           which is what the abort slot is for. */
        const inFlight = pane._explorerDiffLoadInFlight;
        if (inFlight && inFlight.key === cacheKey) {
            await inFlight.promise;
            return;
        }

        code.textContent = 'Loading diff...';
        const load = (async () => {
            try {
                const params = new URLSearchParams({
                    path: diffPath,
                    mode: diffMode
                });
                if (commit) {
                    params.set('commit', commit);
                }
                const response = await fetch(
                    `/api/explorer/${encodeURIComponent(sessionId)}/git/diff?${params.toString()}`,
                    { signal: explorerRequestSignal(pane, 'diff') }
                );
                const data = await response.json();
                if (!response.ok) {
                    throw new Error(data.error || 'Failed to load Git diff');
                }
                pane._explorerDiffLoaded = true;
                pane._explorerDiffCacheKey = cacheKey;
                pane._explorerDiffContent = data.diff || '';
                // The backend already bounds diffs to 256 KiB / 4,000 lines and
                // reports truncation; keep
                // the flag so the rendered patch is never mistaken for the whole change.
                pane._explorerDiffTruncated = Boolean(data.truncated);
                await renderExplorerDiff(index);
                const renderedTab = explorerFindTab(
                    pane,
                    pane._explorerRenderedTabId || pane._explorerActiveTabId
                );
                const restoredDiffView = explorerMatchingTabView(
                    renderedTab,
                    explorerCurrentContentRevisions(pane)
                );
                const restoredDiffScroll = restoredDiffView?.scroll?.panels?.diff;
                if (restoredDiffScroll) {
                    applyScrollMetrics(
                        explorerPanelScrollTarget(
                            document.getElementById(`explorer-diff-panel-${index}`)
                        ),
                        restoredDiffScroll
                    );
                }
                // A patch is back (or was there all along): re-expose the toggle a
                // previous empty-diff fallback may have hidden.
                setExplorerDiffToggleHidden(index, false);
                if (explorerFallbackFromEmptyDiff(index)) {
                    return;
                }
                applyExplorerPendingDiffScroll(index);
                if (activeExplorerFileView(index) === 'diff') {
                    applyExplorerSearch(index);
                }
            } catch (error) {
                if (explorerIsAbortError(error)) {
                    // Superseded by a newer diff load, which owns this panel and
                    // its 'Loading diff...' placeholder now.
                    return;
                }
                console.error('[GridVibe Sessions] Explorer Git diff failed:', error);
                code.innerHTML = `<span class="explorer-diff-empty">${escHtml(error.message || 'Failed to load Git diff.')}</span>`;
            }
        })();
        pane._explorerDiffLoadInFlight = { key: cacheKey, promise: load };
        try {
            await load;
        } finally {
            if (pane._explorerDiffLoadInFlight?.promise === load) {
                pane._explorerDiffLoadInFlight = null;
            }
        }
    }
