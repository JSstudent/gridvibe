"""Find and the selected-word occurrence tint inside the in-place editor.

Edit mode used to disable the find widget and lose the tint, because the Source
panel held a bare textarea with nothing to mark. The highlight overlay put real
rendered rows back behind the caret, so both can be painted on them — but not
the way the read-only view paints them. Its `<mark class="explorer-search-match">`
carries a pixel of horizontal padding, and under the overlay padding is glyph
advance: the underlay would wrap at a different column from the textarea above
it. Both therefore paint through the CSS Custom Highlight API, which can only
set colours and so cannot move a glyph.

Two halves are covered here:

* the pure rules in explorer-edit-highlight.js — mapping absolute draft offsets
  onto rows, deciding when a query has to be resolved against the buffer again,
  and deciding what a textarea selection is worth tinting;
* the adapter, run against the viewer's own search machinery, for what it
  paints, what it refuses to paint, and — because the highlight registries are
  page-global while a grid can hold several open editors — that one pane can
  never clear another pane's find.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
HIGHLIGHT_JS = STATIC_JS / "explorer-edit-highlight.js"
FIND_JS = STATIC_JS / "explorer-edit-find.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"

NODE = shutil.which("node")

# The adapter runs against the *real* search machinery — explorer-viewer.js
# evaluated in the same context — so the range finder, the active-match
# resolver and the pane's search state are the ones the read-only find uses.
# Only the row DOM is stubbed, and it is stubbed to the shape the two helpers
# in explorer-search.js return for a rendered source line.
FIND_HARNESS = """
const fs = require('fs');
const vm = require('vm');

const nodes = {};
let nextHandle = 0;
const frames = new Map();

function HighlightStub() {
    this.ranges = [];
    this.priority = 0;
}
HighlightStub.prototype.add = function (range) { this.ranges.push(range); };
HighlightStub.prototype.clear = function () { this.ranges = []; };

const sandbox = {
    console,
    performance,
    document: {
        activeElement: null,
        getElementById: id => nodes[id] || null,
        querySelector: selector => nodes[selector] || null,
        querySelectorAll: () => [],
        addEventListener() {},
        body: { dataset: {}, addEventListener() {} }
    },
    window: {
        addEventListener() {},
        setTimeout,
        clearTimeout,
        Highlight: HighlightStub,
        CSS: { highlights: new Map() },
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        requestAnimationFrame: callback => {
            nextHandle += 1;
            frames.set(nextHandle, callback);
            return nextHandle;
        },
        cancelAnimationFrame: handle => { frames.delete(handle); }
    },
    navigator: {},
    setTimeout,
    clearTimeout,
    terminals: [],
    sessionIds: [],
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value),
    UI_CHEVRON_DOWN_ICON: '<svg></svg>',
    UI_CHEVRON_RIGHT_ICON: '<svg></svg>',
    /* explorer-search.js's row walkers, stubbed to the same contract: the
       nodes of one rendered line with the offset each starts at, and a range
       over a column span of them. The identity string is what the assertions
       read, so a painted range names the row and columns it covers. */
    explorerSourceLineTextNodes: row => ({
        nodes: [{ node: row, start: 0, end: row.text.length }],
        text: row.text
    }),
    explorerSourceLineRange: (walked, start, end) => {
        const entry = walked[0];
        if (!entry || start < entry.start || start >= entry.end || end > entry.end) {
            return null;
        }
        return {
            id: entry.node.line + ':' + start + '-' + end,
            getBoundingClientRect: () => ({
                top: 0, left: 0, right: 1, bottom: 1, width: 1, height: 1
            })
        };
    }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});
// In a page `window` *is* the global object; the stub above is a separate one.
sandbox.window.GridVibeExplorerEditHighlight = sandbox.GridVibeExplorerEditHighlight;

/* One open editor: a pane holding a draft, and an overlay whose rows show
   exactly that draft. `overlayDraft` lets a test hold the rows one keystroke
   behind the buffer, which is what really happens between an input event and
   the animation frame that repaints them. */
function openEditor(index, draft, { overlayDraft = draft, stacked = true } = {}) {
    const rows = new Map();
    draft.split('\\n').forEach((text, offset) => {
        rows.set(offset + 1, { line: offset + 1, text });
    });
    const underlay = {
        querySelector: selector => {
            const match = /data-explorer-line="(\\d+)"/.exec(selector);
            return match ? (rows.get(Number(match[1])) || null) : null;
        }
    };
    if (stacked) {
        nodes['[data-explorer-edit-stack="' + index + '"]'] = {
            querySelector: selector =>
                (selector === '.explorer-edit-underlay' ? underlay : null)
        };
    } else {
        delete nodes['[data-explorer-edit-stack="' + index + '"]'];
    }
    nodes['explorer-code-' + index] = {
        scrollTop: 0,
        scrollLeft: 0,
        getBoundingClientRect: () => ({
            top: 0, left: 0, right: 100, bottom: 100, width: 100, height: 100
        })
    };
    const pane = {
        _explorerMode: 'file',
        _explorerEdit: { draft },
        _explorerEditOverlayDraft: overlayDraft
    };
    sandbox.terminals[index] = pane;
    return pane;
}

/* The match scroll is deferred to an animation frame so it measures a laid-out
   range; nothing runs the stubbed frames on its own. */
function runFrames() {
    const queued = Array.from(frames.values());
    frames.clear();
    queued.forEach(callback => callback());
}

function painted(name) {
    const highlight = sandbox.window.CSS.highlights.get(name);
    return highlight ? highlight.ranges.map(range => range.id) : [];
}

function paintState() {
    return {
        find: painted('explorer-edit-find'),
        active: painted('explorer-edit-find-active'),
        occurrence: painted('explorer-edit-occurrence')
    };
}

async function find(index, query, options) {
    const pane = sandbox.terminals[index];
    sandbox.ensureExplorerSearchState(pane).query = query;
    await sandbox.applyExplorerEditFind(index, options || {});
}

/* A pane's paint survives until its editor is torn down — that is the point
   of the records — so a block that wants a clean page has to tear the
   previous block's editors down rather than just emptying the registries. */
function resetPaint() {
    [0, 1].forEach(index => sandbox.clearExplorerEditFind(index));
    sandbox.window.CSS.highlights.clear();
    sandbox.terminals.length = 0;
}

const results = {};

(async () => {
    // 1. A find over the draft paints every match on its own row, with the one
    //    the reader is standing on in its own registry so it can read stronger.
    {
        openEditor(0, 'alpha\\nbeta alpha\\ngamma');
        await find(0, 'alpha', { resetActive: true });
        const search = sandbox.ensureExplorerSearchState(sandbox.terminals[0]);
        results.painted = Object.assign(paintState(), {
            matchCount: search.matchCount,
            activeIndex: search.activeIndex
        });
    }

    // 2. Stepping to the next match repaints from the ranges already computed:
    //    no second scan of the buffer, and no rebuild of the rows.
    {
        const realFinder = sandbox.explorerFindRangesAsync;
        let scans = 0;
        sandbox.explorerFindRangesAsync = (...args) => {
            scans += 1;
            return realFinder(...args);
        };
        sandbox.ensureExplorerSearchState(sandbox.terminals[0]).activeIndex = 1;
        await find(0, 'alpha');
        results.stepped = Object.assign(paintState(), { scans });
        sandbox.explorerFindRangesAsync = realFinder;
    }

    // 3. Two panes editing at once. The registries are page-global, so tearing
    //    one editor down must not take the other's find with it.
    {
        resetPaint();
        openEditor(0, 'alpha\\n');
        openEditor(1, 'alpha\\nbeta\\n');
        await find(0, 'alpha', { resetActive: true });
        await find(1, 'beta', { resetActive: true });
        const both = paintState();
        sandbox.clearExplorerEditFind(0);
        results.twoPanes = { both, afterOneCleared: paintState() };
    }

    // 4. Teardown also drops the cached ranges. They were resolved against a
    //    draft that no longer exists, and the read-only find this pane falls
    //    back to would otherwise reuse them against the file on disk.
    {
        resetPaint();
        openEditor(0, 'alpha\\n');
        await find(0, 'alpha', { resetActive: true });
        sandbox.clearExplorerEditFind(0);
        const search = sandbox.terminals[0]._explorerSearch;
        results.cacheDropped = {
            resultQuery: search.resultQuery,
            ranges: search.ranges.length
        };
    }

    // 5. The degraded path: no overlay, so no rows to paint on. The find is
    //    reported unavailable (which is what keeps its input disabled) and
    //    nothing is painted.
    {
        resetPaint();
        openEditor(0, 'alpha\\n', { stacked: false });
        const available = sandbox.explorerEditFindAvailable(0);
        await find(0, 'alpha', { resetActive: true });
        results.degraded = Object.assign(paintState(), { available });
    }

    // 6. Rows one keystroke behind the buffer: the offsets would land on the
    //    wrong columns, so nothing is painted until the frame that catches
    //    them up. The ranges themselves are still resolved and cached.
    {
        resetPaint();
        openEditor(0, 'alpha alpha\\n', { overlayDraft: 'alph\\n' });
        await find(0, 'alpha', { resetActive: true });
        results.staleRows = Object.assign(paintState(), {
            matchCount: sandbox.ensureExplorerSearchState(
                sandbox.terminals[0]
            ).matchCount
        });
    }

    // 7. The occurrence tint reads the textarea's own selection — the document
    //    selection the read-only tint uses is empty while editing — and leaves
    //    the selected text alone, since it already carries the ::selection
    //    wash.
    {
        resetPaint();
        openEditor(0, 'total\\nsubtotal total\\n');
        const textarea = {
            id: 'explorer-edit-textarea-0',
            value: 'total\\nsubtotal total\\n',
            selectionStart: 0,
            selectionEnd: 5
        };
        nodes['explorer-edit-textarea-0'] = textarea;
        sandbox.document.activeElement = textarea;
        sandbox.refreshExplorerEditOccurrenceTint();
        results.occurrence = paintState();

        // A multi-line selection is a block, not a word: nothing to tint.
        textarea.selectionEnd = 12;
        sandbox.refreshExplorerEditOccurrenceTint();
        results.occurrenceMultiline = paintState();

        // And it follows focus: leaving the editor leaves no tint behind.
        textarea.selectionEnd = 5;
        sandbox.refreshExplorerEditOccurrenceTint();
        const refocused = paintState();
        sandbox.document.activeElement = null;
        sandbox.refreshExplorerEditOccurrenceTint();
        results.occurrenceBlurred = { refocused, blurred: paintState() };
    }

    // 8. The selection survives the swap into the editor: what is captured
    //    from the textarea resolves back onto it, and the tint derives from
    //    the restored selection exactly as it would from a fresh one.
    {
        resetPaint();
        openEditor(0, 'total\\nsubtotal total\\n');
        const textarea = {
            id: 'explorer-edit-textarea-0',
            value: 'total\\nsubtotal total\\n',
            selectionStart: 15,
            selectionEnd: 20,
            setSelectionRange(start, end) {
                this.selectionStart = start;
                this.selectionEnd = end;
            }
        };
        nodes['explorer-edit-textarea-0'] = textarea;
        sandbox.document.activeElement = textarea;
        const carry = sandbox.explorerEditorSelectionCarry(0);
        textarea.selectionStart = 0;
        textarea.selectionEnd = 0;
        const restored = sandbox.restoreExplorerEditorSelection(0, carry);
        sandbox.refreshExplorerEditOccurrenceTint();
        results.carried = {
            carry,
            restored,
            selection: [textarea.selectionStart, textarea.selectionEnd],
            occurrence: painted('explorer-edit-occurrence')
        };
    }

    // 9. Navigating a find moves the view to the active match; repainting one
    //    never does. Entering and leaving edit mode both re-resolve the same
    //    query onto a rebuilt surface, and so does every keystroke under an
    //    open find — pulling the view to match 5 of 8 each time is what threw
    //    the reader across the file on a swap between Source and Edit.
    {
        resetPaint();
        openEditor(0, 'alpha\\nbeta alpha\\n');
        const view = nodes['explorer-code-0'];
        await find(0, 'alpha', { resetActive: true, scroll: false });
        runFrames();
        const repainted = view.scrollTop;
        await find(0, 'alpha');
        runFrames();
        results.scroll = { repainted, navigated: view.scrollTop };
    }

    // 10. Ctrl+F over a word highlighted in the editor looks that word up, the
    //     way it always has in the Source view. The document selection the
    //     read-only seed reads is empty inside a textarea, so the editor
    //     answers for its own pane — and answers even when it has nothing, so
    //     the two sources never both speak.
    {
        resetPaint();
        openEditor(0, 'alpha\\nbeta alpha\\n');
        const textarea = {
            id: 'explorer-edit-textarea-0',
            value: 'alpha\\nbeta alpha\\n',
            selectionStart: 11,
            selectionEnd: 16
        };
        nodes['explorer-edit-textarea-0'] = textarea;
        const selected = sandbox.explorerEditSelectionSeed(0);
        textarea.selectionStart = 4;
        textarea.selectionEnd = 4;
        const collapsed = sandbox.explorerEditSelectionSeed(0);
        sandbox.terminals[0]._explorerEdit = null;
        const readOnly = sandbox.explorerEditSelectionSeed(0);
        results.seed = { selected, collapsed, readOnly };
    }

    process.stdout.write(JSON.stringify(results));
})().catch(error => {
    console.error(error);
    process.exit(1);
});
"""


class NodeHarnessMixin:
    def _run_node(self, harness: str):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(HIGHLIGHT_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


@unittest.skipUnless(NODE, "Node.js is required for explorer edit find tests")
class LineSpanMappingTestCase(NodeHarnessMixin, unittest.TestCase):
    """Absolute draft offsets → the row and columns a painter can address."""

    def _spans(self, draft, ranges):
        return self._run_node(
            """
            const policy = require(process.argv[2]);
            const input = %s;
            process.stdout.write(JSON.stringify(
                policy.lineSpansForRanges(input.draft, input.ranges)
            ));
            """
            % json.dumps({"draft": draft, "ranges": ranges})
        )

    def test_offsets_resolve_to_the_row_they_fall_on(self):
        # "beta" at 6 opens line 2, whose own text starts at offset 6.
        self.assertEqual(
            self._spans("alpha\nbeta\n", [{"start": 6, "end": 10}]),
            [{"line": 2, "start": 0, "end": 4, "active": False}],
        )

    def test_a_match_on_the_first_row_needs_no_offset(self):
        self.assertEqual(
            self._spans("alpha\nbeta\n", [{"start": 0, "end": 5}]),
            [{"line": 1, "start": 0, "end": 5, "active": False}],
        )

    def test_the_active_flag_survives_the_mapping(self):
        # It is what splits the painted ranges into the two find registries.
        (span,) = self._spans("alpha\n", [{"start": 0, "end": 5, "active": True}])
        self.assertTrue(span["active"])

    def test_rows_far_down_a_file_land_on_the_right_line(self):
        draft = "".join(f"line{n}\n" for n in range(1000))
        # The 500th row starts after 499 rows; find its own text offset.
        start = draft.index("line500")
        (span,) = self._spans(draft, [{"start": start, "end": start + 7}])
        self.assertEqual(span["line"], 501)
        self.assertEqual((span["start"], span["end"]), (0, 7))

    def test_a_range_is_clipped_to_the_row_it_opens_on(self):
        # No caller produces one — the find input is single-line and the tint
        # refuses a multi-line selection — but one that leaked through would
        # otherwise address a column past the end of its row.
        self.assertEqual(
            self._spans("ab\ncd\n", [{"start": 1, "end": 5}]),
            [{"line": 1, "start": 1, "end": 2, "active": False}],
        )

    def test_empty_and_out_of_bounds_ranges_are_dropped(self):
        self.assertEqual(
            self._spans(
                "alpha\n",
                [
                    {"start": 2, "end": 2},
                    {"start": 4, "end": 1},
                    {"start": -1, "end": 3},
                    {"start": 99, "end": 100},
                    {"start": "x", "end": 3},
                ],
            ),
            [],
        )

    def test_a_trailing_newline_leaves_an_addressable_final_row(self):
        # The renderer opens a final empty row for a trailing newline, so the
        # line numbering here has to agree with it.
        self.assertEqual(
            self._spans("a\nb\n", [{"start": 2, "end": 3}]),
            [{"line": 2, "start": 0, "end": 1, "active": False}],
        )


@unittest.skipUnless(NODE, "Node.js is required for explorer edit find tests")
class FindRefreshDecisionTestCase(NodeHarnessMixin, unittest.TestCase):
    """When a query has to be resolved against the buffer again."""

    def _decisions(self, cases):
        return self._run_node(
            """
            const policy = require(process.argv[2]);
            const cases = %s;
            process.stdout.write(JSON.stringify(
                cases.map(one => policy.findRefreshDecision(one))
            ));
            """
            % json.dumps(cases)
        )

    def test_a_new_query_is_resolved_against_the_draft(self):
        self.assertEqual(
            self._decisions([{"enabled": True, "query": "a", "draft": "abc"}]),
            ["recompute"],
        )

    def test_the_same_query_on_the_same_buffer_reuses_its_ranges(self):
        # This is what makes stepping between matches free: Enter re-enters
        # here and only the active index moved.
        self.assertEqual(
            self._decisions(
                [
                    {
                        "enabled": True,
                        "query": "a",
                        "draft": "abc",
                        "resultQuery": "a",
                        "resultDraft": "abc",
                    }
                ]
            ),
            ["skip"],
        )

    def test_a_moved_buffer_invalidates_the_same_query(self):
        # The whole difference from the read-only find: there the content is
        # fixed while the file is open, here every keystroke moves it and the
        # old offsets would point at the wrong columns.
        self.assertEqual(
            self._decisions(
                [
                    {
                        "enabled": True,
                        "query": "a",
                        "draft": "abcd",
                        "resultQuery": "a",
                        "resultDraft": "abc",
                    }
                ]
            ),
            ["recompute"],
        )

    def test_an_emptied_query_clears_the_paint(self):
        self.assertEqual(
            self._decisions(
                [{"enabled": True, "query": "", "draft": "abc", "resultQuery": "a"}]
            ),
            ["clear"],
        )

    def test_without_rows_there_is_no_find(self):
        # The degraded bare textarea has nothing to paint on, so the find stays
        # the disabled control it has always been while editing.
        self.assertEqual(
            self._decisions([{"enabled": False, "query": "a", "draft": "abc"}]),
            ["off"],
        )

    def test_a_never_resolved_query_never_reads_as_resolved(self):
        # An editor that has computed nothing yet carries no draft marker; that
        # must not read as "already resolved against the empty buffer".
        self.assertEqual(
            self._decisions([{"enabled": True, "query": "a", "draft": ""}]),
            ["recompute"],
        )


@unittest.skipUnless(NODE, "Node.js is required for explorer edit find tests")
class SelectionOccurrenceTestCase(NodeHarnessMixin, unittest.TestCase):
    """What a textarea selection is worth tinting, and where else it appears."""

    def _queries(self, cases):
        return self._run_node(
            """
            const policy = require(process.argv[2]);
            const cases = %s;
            process.stdout.write(JSON.stringify(
                cases.map(one => policy.selectionOccurrenceQuery(one))
            ));
            """
            % json.dumps(cases)
        )

    def _spans(self, cases):
        return self._run_node(
            """
            const policy = require(process.argv[2]);
            const cases = %s;
            process.stdout.write(JSON.stringify(
                cases.map(one => policy.occurrenceSpans(one))
            ));
            """
            % json.dumps(cases)
        )

    def test_a_selected_word_is_a_query(self):
        (result,) = self._queries(
            [{"value": "total here", "selectionStart": 0, "selectionEnd": 5}]
        )
        self.assertEqual(result["query"], "total")
        self.assertEqual((result["start"], result["end"]), (0, 5))

    def test_a_collapsed_selection_is_nothing(self):
        self.assertEqual(
            self._queries(
                [{"value": "total", "selectionStart": 2, "selectionEnd": 2}]
            ),
            [None],
        )

    def test_a_selection_crossing_rows_is_a_block_not_a_word(self):
        # It would also map to a column past the end of its opening row.
        self.assertEqual(
            self._queries(
                [{"value": "one\ntwo", "selectionStart": 0, "selectionEnd": 7}]
            ),
            [None],
        )

    def test_whitespace_alone_is_not_a_word(self):
        self.assertEqual(
            self._queries(
                [{"value": "a    b", "selectionStart": 1, "selectionEnd": 5}]
            ),
            [None],
        )

    def test_an_over_long_selection_is_refused(self):
        self.assertEqual(
            self._queries(
                [
                    {
                        "value": "x" * 40,
                        "selectionStart": 0,
                        "selectionEnd": 40,
                        "maxQueryLength": 20,
                    }
                ]
            ),
            [None],
        )

    def test_an_identifier_matches_whole_words_only(self):
        # Double-clicking `id` must not light up every `width` in the buffer.
        (spans,) = self._spans(
            [{"draft": "id width id_x paid id", "query": "id"}]
        )
        self.assertEqual([span["start"] for span in spans], [0, 19])

    def test_a_non_identifier_selection_matches_anywhere(self):
        # `a.b` is not a bare identifier, so the whole-word rule does not apply
        # and a substring hit is a legitimate occurrence.
        (spans,) = self._spans([{"draft": "xa.by a.b", "query": "a.b"}])
        self.assertEqual([span["start"] for span in spans], [1, 6])

    def test_the_readers_own_selection_is_left_alone(self):
        # It already carries the textarea's ::selection wash; tinting it too
        # would just be two coats on the one thing that needs neither.
        (spans,) = self._spans(
            [
                {
                    "draft": "total total total",
                    "query": "total",
                    "selectionStart": 6,
                    "selectionEnd": 11,
                }
            ]
        )
        self.assertEqual([span["start"] for span in spans], [0, 12])

    def test_matching_is_case_insensitive_and_non_overlapping(self):
        # `a.a` is not a bare identifier, so nothing here is filtered by the
        # whole-word rule: the second hit at 4 rather than 2 is the search
        # resuming past the first, and the third is the case fold.
        (spans,) = self._spans([{"draft": "a.a.a.a A.A", "query": "a.a"}])
        self.assertEqual([span["start"] for span in spans], [0, 4, 8])

    def test_the_tint_is_capped(self):
        (spans,) = self._spans(
            [{"draft": "x " * 50, "query": "x", "maxMatches": 5}]
        )
        self.assertEqual(len(spans), 5)


@unittest.skipUnless(NODE, "Node.js is required for explorer edit find tests")
class CarriedSelectionTestCase(NodeHarnessMixin, unittest.TestCase):
    """A selection has to survive the swap between the rows and the textarea.

    The occurrence tint is derived from the selection every time and keeps no
    state, so when the surface holding that selection is replaced the tint goes
    out with it. What crosses is a description — row, column, and the selected
    text — re-resolved against whatever buffer is now there.
    """

    def _resolve(self, cases):
        return self._run_node(
            """
            const policy = require(process.argv[2]);
            const cases = %s;
            process.stdout.write(JSON.stringify(
                cases.map(one => policy.carriedSelectionRange(one))
            ));
            """
            % json.dumps(cases)
        )

    def test_an_unchanged_buffer_resolves_at_the_recorded_spot(self):
        self.assertEqual(
            self._resolve(
                [{"text": "alpha\nbeta gamma\n", "line": 2, "column": 5, "needle": "gamma"}]
            ),
            [{"start": 11, "end": 16}],
        )

    def test_the_text_wins_when_the_column_has_drifted(self):
        # The two buffers are not always the same string — a discarded draft, a
        # CRLF file's normalized newlines — so a column alone would silently
        # select the wrong thing rather than nothing.
        self.assertEqual(
            self._resolve(
                [{"text": "alpha\n    beta\n", "line": 2, "column": 0, "needle": "beta"}]
            ),
            [{"start": 10, "end": 14}],
        )

    def test_a_selection_starting_on_an_element_boundary_still_resolves(self):
        # No column to record; the needle is looked for on the row instead.
        self.assertEqual(
            self._resolve(
                [{"text": "alpha\nbeta\n", "line": 2, "column": -1, "needle": "beta"}]
            ),
            [{"start": 6, "end": 10}],
        )

    def test_it_never_wanders_onto_another_row(self):
        # A selection that silently reappears somewhere else is worse than one
        # that does not come back: the tint would anchor to a word the reader
        # never picked.
        self.assertEqual(
            self._resolve(
                [{"text": "beta\nalpha\n", "line": 2, "column": 0, "needle": "beta"}]
            ),
            [None],
        )

    def test_a_vanished_needle_carries_nothing(self):
        self.assertEqual(
            self._resolve(
                [{"text": "alpha\nbeta\n", "line": 2, "column": 0, "needle": "gamma"}]
            ),
            [None],
        )

    def test_a_row_that_no_longer_exists_carries_nothing(self):
        self.assertEqual(
            self._resolve([{"text": "alpha\n", "line": 9, "column": 0, "needle": "alpha"}]),
            [None],
        )

    def test_a_match_may_not_run_past_the_end_of_its_row(self):
        # Guards the join: without the row bound, a needle spanning the newline
        # would resolve and then select across two rendered rows.
        self.assertEqual(
            self._resolve(
                [{"text": "ab\ncd\n", "line": 1, "column": 0, "needle": "ab\ncd"}]
            ),
            [None],
        )

    def test_the_last_row_of_a_file_without_a_trailing_newline_resolves(self):
        self.assertEqual(
            self._resolve([{"text": "alpha\nbeta", "line": 2, "column": 0, "needle": "beta"}]),
            [{"start": 6, "end": 10}],
        )


@unittest.skipUnless(NODE, "Node.js is required for explorer edit find tests")
class EditFindAdapterTestCase(unittest.TestCase):
    """What the adapter paints, refuses to paint, and releases."""

    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(FIND_HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(VIEWER_JS), str(HIGHLIGHT_JS), str(FIND_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise AssertionError(f"node harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_every_match_is_painted_on_its_own_row(self):
        painted = self.results["painted"]
        self.assertEqual(painted["matchCount"], 2)
        # "alpha" on row 1 at columns 0-5, and on row 2 at columns 5-10 — the
        # offsets are the draft's, the columns are the row's.
        self.assertEqual(painted["active"], ["1:0-5"])
        self.assertEqual(painted["find"], ["2:5-10"])

    def test_the_active_match_is_painted_separately(self):
        # It has to read stronger than the rest, and a Custom Highlight cannot
        # draw the ring its <mark> counterpart uses, so it gets its own hue and
        # therefore its own registry.
        painted = self.results["painted"]
        self.assertEqual(painted["activeIndex"], 0)
        self.assertNotIn("1:0-5", painted["find"])

    def test_stepping_between_matches_costs_no_second_scan(self):
        stepped = self.results["stepped"]
        self.assertEqual(stepped["scans"], 0)
        # The paint moved, though: the second match is now the active one.
        self.assertEqual(stepped["active"], ["2:5-10"])
        self.assertEqual(stepped["find"], ["1:0-5"])

    def test_one_editor_never_clears_another_editors_find(self):
        # The highlight registries are page-global while a grid can hold
        # several open editors, so the paint is rebuilt from every pane's own
        # record rather than cleared by whichever pane repainted last.
        two = self.results["twoPanes"]
        self.assertEqual(sorted(two["both"]["active"]), ["1:0-5", "2:0-4"])
        self.assertEqual(two["afterOneCleared"]["active"], ["2:0-4"])

    def test_teardown_drops_the_ranges_resolved_against_the_draft(self):
        # The read-only find this pane falls back to must resolve its own
        # against the file; reusing draft offsets would mark the wrong columns
        # after a discarded edit.
        cache = self.results["cacheDropped"]
        self.assertEqual(cache["resultQuery"], "")
        self.assertEqual(cache["ranges"], 0)

    def test_without_the_overlay_the_find_is_unavailable_and_paints_nothing(self):
        degraded = self.results["degraded"]
        self.assertFalse(degraded["available"])
        self.assertEqual(degraded["find"], [])
        self.assertEqual(degraded["active"], [])

    def test_rows_a_keystroke_behind_the_buffer_are_not_painted(self):
        # Between an input event and the frame that repaints the underlay, its
        # rows still describe the previous draft — painting the new offsets on
        # them would put matches on the wrong columns.
        stale = self.results["staleRows"]
        self.assertEqual(stale["find"], [])
        self.assertEqual(stale["active"], [])
        # The scan still happened, so the frame that catches the rows up has
        # its answer waiting.
        self.assertEqual(stale["matchCount"], 2)

    def test_the_tint_reads_the_textareas_selection_and_skips_it(self):
        occurrence = self.results["occurrence"]["occurrence"]
        # "total" selected on row 1; the other whole-word occurrence is on row
        # 2 at columns 9-14, and "subtotal" is not one.
        self.assertEqual(occurrence, ["2:9-14"])

    def test_a_multi_line_selection_tints_nothing(self):
        self.assertEqual(self.results["occurrenceMultiline"]["occurrence"], [])

    def test_a_selection_carries_into_the_editor_with_its_tint(self):
        # The bug this closes: clicking Edit replaced the rows the reader's
        # selection was anchored to, so a double-clicked word went dark and had
        # to be picked again inside the editor.
        carried = self.results["carried"]
        self.assertEqual(
            carried["carry"], {"line": 2, "column": 9, "needle": "total"}
        )
        self.assertTrue(carried["restored"])
        self.assertEqual(carried["selection"], [15, 20])
        # And the tint is back on the other occurrence — not on "subtotal",
        # which the whole-word rule still excludes, nor on the selection itself.
        self.assertEqual(carried["occurrence"], ["1:0-5"])

    def test_the_tint_follows_focus(self):
        # A blurred textarea keeps its selectionStart, so without this the tint
        # would outlive the selection that justified it.
        results = self.results["occurrenceBlurred"]
        self.assertEqual(results["refocused"]["occurrence"], ["2:9-14"])
        self.assertEqual(results["blurred"]["occurrence"], [])

    def test_a_repainted_find_leaves_the_view_where_the_reader_left_it(self):
        # Swapping between Source and Edit re-resolves the same query onto the
        # other surface, and so does every keystroke while a find is open. None
        # of those is the reader asking to go anywhere.
        self.assertEqual(self.results["scroll"]["repainted"], 0)

    def test_navigating_a_find_still_moves_the_view_to_its_match(self):
        # Enter, prev/next and a freshly typed query are the reader asking.
        self.assertNotEqual(self.results["scroll"]["navigated"], 0)

    def test_ctrl_f_seeds_from_the_editors_own_selection(self):
        seed = self.results["seed"]["selected"]
        self.assertEqual(seed["query"], "alpha")
        # And it opens on the match under the reader rather than the file's
        # first one: the offset is the start of row 2, where the word sits.
        self.assertEqual(seed["offset"], 6)

    def test_an_editor_with_nothing_selected_still_answers(self):
        # An empty query means "no seed", not "fall through to the document
        # selection" — which is always empty inside a textarea anyway.
        self.assertEqual(self.results["seed"]["collapsed"], {"query": "", "offset": None})

    def test_a_pane_with_no_editor_leaves_the_seed_to_the_source_view(self):
        self.assertIsNone(self.results["seed"]["readOnly"])


if __name__ == "__main__":
    unittest.main()
