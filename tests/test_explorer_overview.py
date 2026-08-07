"""Source-view change overview: ``web/static/js/explorer-overview.js`` owns
the HEAD-relative change model for the open file, paints gutter markers
(added / modified bars, deletion wedges) on the rendered Source rows
(Phase 1), and docks the overview column beside them — row geometry, the
change lane, the viewport box and the click / drag / wheel navigation that
makes it the Source panel's scrollbar (Phase 2, ``ruler`` mode).

There is no JS test runner, so the suite is contract-level on the served
assets — the allowed kinds only: script wiring and load order, rendered
markup hooks (classes, ``data-*``, ``aria-*``), named functions and
constants, CSS selectors and custom properties. The behavioural half (that a
``mode=head`` diff's new-side line numbers address the worktree) is covered
by the ``git/diff`` tests in ``tests/test_api.py``; Phase 2 adds no backend
surface of its own.
"""

import re
import unittest

import api


class ExplorerOverviewTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path: str) -> str:
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _overview(self) -> str:
        return self._static("js/explorer-overview.js")

    def test_terminals_page_loads_the_overview_script_in_order(self):
        html = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn("/static/js/explorer-overview.js", html)
        # After explorer-viewer.js (it calls explorerDiffChangeBlocks and the
        # viewer calls back) and before terminals.js.
        self.assertLess(
            html.index("js/explorer-viewer.js"),
            html.index("js/explorer-overview.js"),
        )
        self.assertLess(
            html.index("js/explorer-overview.js"),
            html.index("js/terminals.js"),
        )

    def test_overview_script_exposes_only_the_four_entry_points(self):
        overview = self._overview()
        for name in (
            "loadExplorerChangeMarks",
            "applyExplorerChangeMarks",
            "refreshExplorerOverview",
            "teardownExplorerOverview",
        ):
            self.assertIn(f"function {name}(", overview)

    def test_change_model_fetches_the_head_diff_once_per_path_and_head(self):
        overview = self._overview()
        # The bounded read-only endpoint, HEAD mode only (marks are
        # HEAD-relative; commit diffs would address the wrong lines).
        self.assertIn("/git/diff?", overview)
        self.assertIn("mode: 'head'", overview)
        # Cached on the pane as key + model, separate from the Diff panel's
        # view-scoped slots; the key is `path \n git-revision`.
        self.assertIn("pane._explorerChangeMarksKey", overview)
        self.assertIn("pane._explorerChangeMarks", overview)
        self.assertIn("_explorerGitContext?.head", overview)
        # A hit short-circuits the fetch; the explicit force flag is what the
        # refresh triggers use after a save / undo / revision change.
        self.assertIn("pane._explorerChangeMarksKey === key", overview)
        self.assertIn("{ force = false } = {}", overview)

    def test_change_model_reuses_the_diff_parser_and_classifies_blocks(self):
        overview = self._overview()
        # The diff-undo parser is reused unchanged, and the block shape
        # (expected = worktree lines, replacement = HEAD lines) classifies
        # into added / modified line marks plus deletion wedges.
        self.assertIn("explorerDiffChangeBlocks(diff)", overview)
        self.assertIn("removed ? 'modified' : 'added'", overview)
        self.assertIn("deletions.push({ atLine: block.line, count: removed })", overview)

    def test_change_model_skips_the_surfaces_that_cannot_carry_marks(self):
        overview = self._overview()
        eligible = overview[
            overview.index("function explorerChangeMarksEligible(pane)"):
            overview.index("function explorerChangeMarksKey(pane)")
        ]
        # Not in a Git worktree, a commit-diff tab, and an active editor all
        # opt out before any request is made.
        self.assertIn("pane._explorerGitContext?.available", eligible)
        self.assertIn("!pane._explorerDiffCommit", eligible)
        self.assertIn("!pane._explorerEdit", eligible)
        # The Diff panel's already-fetched HEAD diff is reused instead of
        # re-fetching, but only on an exact mode-scoped cache-key match.
        self.assertIn("explorerDiffCacheKey(path, '', 'head')", overview)

    def test_gutter_marks_are_emitted_as_data_hooks(self):
        overview = self._overview()
        # Marks land on the rendered rows, addressable by line number, as
        # data-explorer-change hooks the CSS paints — never as inline styles.
        self.assertIn(".explorer-source-line[data-explorer-line]", overview)
        self.assertIn("row.dataset.explorerChange = kind;", overview)
        self.assertIn("row.dataset.explorerChange = 'deleted';", overview)

    def test_gutter_marker_rules_exist_for_all_three_kinds(self):
        css = self._static("css/terminals.css")
        # Phase 5 moved the paint from the number cell's box-shadow / ::before
        # onto the marker button itself: the bar / wedge *is* the click
        # target, painted with currentColor from the per-kind colour rules.
        self.assertIn(
            '.explorer-source-line[data-explorer-change="added"] .explorer-change-marker',
            css,
        )
        self.assertIn(
            '.explorer-source-line[data-explorer-change="modified"] .explorer-change-marker',
            css,
        )
        self.assertIn(
            '.explorer-change-marker[data-explorer-change-marker-kind="deleted"]::before',
            css,
        )
        # The colours come from the shared tokens, not palette literals.
        self.assertIn("var(--gv-diff-add)", css)
        self.assertIn("var(--gv-diff-modified)", css)
        self.assertIn("var(--gv-diff-delete)", css)

    def test_diff_marker_tokens_are_declared_for_both_themes(self):
        tokens = self._static("css/tokens.css")
        root = tokens[tokens.index(":root {"):]
        root = root[: root.index("}")]
        light = tokens[tokens.index('[data-theme="light"] {'):]
        light = light[: light.index("}")]
        for token in ("--gv-diff-add", "--gv-diff-modified", "--gv-diff-delete"):
            self.assertIn(f"{token}:", root)
            self.assertIn(f"{token}:", light)

    def test_refresh_triggers_are_wired_into_the_existing_render_paths(self):
        viewer = self._static("js/explorer-viewer.js")
        # Marks re-apply (cheap, no fetch) after every Source rebuild.
        render_source = viewer[
            viewer.index("function renderExplorerSource(index, searchRanges = [])"):
            viewer.index("function explorerPreviewBlockLanguage(code)")
        ]
        self.assertIn("applyExplorerChangeMarks(index);", render_source)
        # A full file render kicks off the load; an in-place refresh (save,
        # undo, quiet watcher refresh) forces it past the path + HEAD key.
        self.assertIn("loadExplorerChangeMarks(index);", viewer)
        self.assertIn("loadExplorerChangeMarks(index, { force: true });", viewer)
        # The directory listing and the image viewer drop the cached model.
        self.assertIn("teardownExplorerOverview(index);", viewer)

    def test_git_watch_carries_an_overview_consumer(self):
        watch = self._static("js/explorer-git-watch.js")
        # The existing /git/state poll detects a terminal-made commit and
        # refreshes the marks — no new polling of its own.
        self.assertIn("function explorerOverviewWatchConsumer(pane)", watch)
        self.assertIn("refreshExplorerOverview(index, 'git-revision')", watch)
        self.assertIn("pane._explorerOverviewWatchRevision = revision;", watch)


class ExplorerOverviewColumnTestCase(unittest.TestCase):
    """Phase 2 — the overview column itself: the ``<aside>`` docked in the
    source frame, its geometry over rendered rows, the change lane, the
    viewport box, and the click / drag / wheel navigation."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path: str) -> str:
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _overview(self) -> str:
        return self._static("js/explorer-overview.js")

    def test_source_frame_renders_the_overview_column(self):
        viewer = self._static("js/explorer-viewer.js")
        # The column is a sibling of the scroller inside the fixed frame, so
        # it takes real layout width instead of overlaying the code, and it
        # hides with the panel.
        self.assertIn(
            '<div class="explorer-source-view" id="explorer-code-${index}"></div>'
            "${explorerOverviewHtml(index)}",
            viewer,
        )
        self.assertIn("function explorerOverviewHtml(", self._overview())

    def test_overview_markup_carries_its_hooks_and_scrollbar_semantics(self):
        overview = self._overview()
        for hook in (
            'class="explorer-source-overview"',
            'data-explorer-overview="${index}"',
            'data-explorer-overview-mode="ruler"',
            'class="explorer-overview-canvas"',
            'class="explorer-overview-viewport"',
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, overview)
        # It really is the Source panel's scrollbar, so it says so: the
        # control it drives, its orientation, and a live position.
        self.assertIn('role="scrollbar"', overview)
        self.assertIn('aria-controls="explorer-code-${index}"', overview)
        self.assertIn('aria-orientation="vertical"', overview)
        self.assertIn("setAttribute('aria-valuenow'", overview)
        # The box is decorative; the position is on the aside, not on it.
        self.assertIn('class="explorer-overview-viewport" aria-hidden="true"', overview)

    def test_overview_css_declares_the_column_and_its_fixed_widths(self):
        css = self._static("css/terminals.css")
        for selector in (
            ".explorer-source-overview {",
            ".explorer-source-overview[hidden] {",
            '.explorer-source-overview[data-explorer-overview-mode="ruler"] {',
            ".explorer-overview-canvas {",
            ".explorer-overview-viewport {",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)
        # Settled decision 2: the width is a fixed custom property, not a
        # draggable per-pane value that would need persisting and restoring.
        frame = css[css.index(".explorer-source-frame {"):]
        frame = frame[: frame.index("}")]
        self.assertIn("--explorer-overview-width:", frame)
        self.assertIn("--explorer-overview-ruler-width:", frame)

    def test_overview_geometry_is_measured_over_rendered_rows_and_cached(self):
        overview = self._overview()
        self.assertIn("function explorerOverviewGeometry(", overview)
        geometry = overview[
            overview.index("function explorerOverviewGeometry("):
            overview.index("function explorerOverviewDeletionRow(")
        ]
        # Wrapping is on by default and a Markdown fold removes rows from the
        # DOM, so `line x lineHeight` would be wrong twice over: the pass runs
        # over the rows that are actually rendered.
        self.assertIn(".explorer-source-line[data-explorer-line]", geometry)
        # One uninterrupted read pass, cached against a signature that moves
        # with the content, the folds, the wrap flag and a resize — so a
        # search keystroke re-renders without re-measuring.
        self.assertIn("pane._explorerOverviewGeometry", geometry)
        self.assertIn("cached.signature === signature", geometry)
        self.assertIn("parts.code.scrollHeight", geometry)
        self.assertIn("parts.code.clientWidth", geometry)
        self.assertIn("wrapped ? 1 : 0", geometry)
        # Above the cap the per-row pass is skipped for a uniform stand-in
        # rather than paying a long layout.
        self.assertIn("const EXPLORER_OVERVIEW_MAX_ROWS = 20000;", overview)
        self.assertIn("count > EXPLORER_OVERVIEW_MAX_ROWS", geometry)

    def test_overview_marker_colours_come_from_the_shared_tokens(self):
        overview = self._overview()
        colors = overview[
            overview.index("function explorerOverviewColors("):
            overview.index("function paintExplorerOverview(")
        ]
        # Guardrail 7: the canvas cannot use a CSS variable directly, so the
        # tokens are read back off the live element — the explorer theme and
        # light/dark then follow with no palette literal in the JS.
        for token in ("--gv-diff-add", "--gv-diff-modified", "--gv-diff-delete"):
            with self.subTest(token=token):
                self.assertIn(token, colors)
        self.assertIn("window.getComputedStyle(aside)", colors)
        self.assertIsNone(
            re.search(r"#[0-9a-fA-F]{3,8}\b", overview),
            "no palette literal may enter explorer-overview.js",
        )

    def test_overview_navigates_by_click_drag_and_wheel(self):
        overview = self._overview()
        wiring = overview[overview.index("function wireExplorerOverview("):]
        wiring = wiring[: wiring.index("function syncExplorerOverview(")]
        # A drag is pointer-captured and released on every way it can end,
        # so the scrub cannot outlive the gesture.
        self.assertIn("setPointerCapture", wiring)
        for event in ("pointerdown", "pointermove", "pointerup", "pointercancel",
                      "lostpointercapture"):
            with self.subTest(event=event):
                self.assertIn(event, wiring)
        # The column is not a second scrollable surface: a wheel over it
        # drives the text, which needs a non-passive listener to suppress.
        self.assertIn("{ passive: false }", wiring)
        # The scroll listener is passive — the box moves in its own frame and
        # must never block the scroll it is following.
        self.assertIn("{ passive: true }", wiring)
        # A marker click is a jump to that change, reusing the existing
        # scroll-and-flash affordance rather than a second one.
        self.assertIn("scrollExplorerSourceToLine(index, line)", wiring)
        self.assertIn("function explorerOverviewMarkerLineAt(", overview)

    def test_overview_repaints_are_gated_and_scrolls_are_transform_only(self):
        overview = self._overview()
        # Guardrail 3: no polling and no per-scroll layout work. A scroll
        # moves the box inside one coalesced frame; the lane repaint is
        # reserved for content / marks / geometry / size changes.
        self.assertIn("function scheduleExplorerOverviewViewport(", overview)
        self.assertIn("function scheduleExplorerOverviewSync(", overview)
        self.assertIn("pane._explorerOverviewViewportFrame", overview)
        self.assertIn("pane._explorerOverviewFrame", overview)
        viewport = overview[
            overview.index("function updateExplorerOverviewViewport("):
            overview.index("function explorerOverviewClampScroll(")
        ]
        self.assertIn("translateY(", viewport)
        # A resize re-wraps the text, so both halves move — the existing
        # ResizeObserver pattern, not a timer.
        self.assertIn("new window.ResizeObserver(", overview)

    def test_overview_stands_down_when_there_is_nothing_to_survey(self):
        overview = self._overview()
        sync = overview[
            overview.index("function syncExplorerOverview("):
            overview.index("function scheduleExplorerOverviewSync(")
        ]
        # An empty file, the editor's textarea, or a panel switched away: the
        # column leaves the layout instead of showing the last file's shape.
        self.assertIn("parts.aside.hidden = !geometry;", sync)
        self.assertIn("parts.frame.hidden", sync)
        # Entering the in-place editor replaces the rows with a textarea, so
        # the same re-apply that drops the gutter marks stands the column down.
        editor = self._static("js/explorer-editor.js")
        render = editor[
            editor.index("function renderExplorerEditTextarea("):
            editor.index("function handleExplorerEditInput(")
        ]
        self.assertIn("applyExplorerChangeMarks(index);", render)

    def test_teardown_releases_the_observer_and_the_queued_frames(self):
        overview = self._overview()
        teardown = overview[
            overview.index("function teardownExplorerOverview("):
            overview.index("function explorerOverviewHtml(")
        ]
        self.assertIn("pane._explorerOverviewGeometry = null;", teardown)
        self.assertIn("pane._explorerOverviewObserver?.disconnect();", teardown)
        self.assertIn("explorerOverviewCancelFrames(pane);", teardown)


class ExplorerChangePeekTestCase(unittest.TestCase):
    """Phase 5 — the clickable gutter change marker and its inline change
    peek: the marker bar / wedge is itself a button, and a click opens a
    compact diff of that block alone, inserted directly under the change.

    The backend is untouched (the peek is a second view of the model the pane
    already holds), so this stays contract-level on the served assets — the
    same allowed kinds as the rest of this file."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _static(self, path: str) -> str:
        response = self.client.get(f"/static/{path}")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        response.close()
        return body

    def _overview(self) -> str:
        return self._static("js/explorer-overview.js")

    def _css_rule(self, css: str, selector: str) -> str:
        self.assertIn(selector, css)
        rule = css[css.index(selector):]
        return rule[: rule.index("}")]

    def test_model_keeps_the_blocks_and_indexes_them_by_line(self):
        overview = self._overview()
        # The classification output is unchanged, but the blocks are kept for
        # the peek, with every marked line — and every deletion's anchor
        # line — mapping to its block for a one-lookup click resolution.
        self.assertIn("blocks: modelBlocks", overview)
        self.assertIn("blockByLine", overview)
        self.assertIn("blockByLine.set(block.line + offset, entry.id)", overview)
        self.assertIn("blockByLine.set(block.line, entry.id)", overview)
        # Peek identity is the start-line pair, which survives an unrelated
        # edit elsewhere; the positional id alone would not.
        self.assertIn("pane._explorerChangePeek = { line: block.line, oldLine: block.oldLine };", overview)
        self.assertIn("block.line === state.line && block.oldLine === state.oldLine", overview)

    def test_marker_button_is_emitted_per_marked_row_with_one_tab_stop_per_block(self):
        overview = self._overview()
        # Injected by the gutter pass that already walks exactly the marked
        # rows — row-level, never a child of the gutter cell (a nested button
        # on a Markdown heading row would be invalid HTML).
        gutter = overview[
            overview.index("function applyExplorerChangeMarkGutter("):
            overview.index("function refreshExplorerOverview(")
        ]
        self.assertIn("explorer-change-marker", gutter)
        self.assertIn("button.dataset.explorerChangeMarker = String(entry.id);", gutter)
        # The deletion wedge is the same button with its own kind hook.
        self.assertIn("button.dataset.explorerChangeMarkerKind = 'deleted';", gutter)
        # One tab stop per block: the first rendered row carries tabindex 0,
        # every other row of the block stays clickable at tabindex -1.
        self.assertIn("button.tabIndex = tabbedBlocks.has(entry.id) ? -1 : 0;", gutter)
        # The button is a real disclosure control for the peek it opens.
        self.assertIn("button.setAttribute('aria-expanded', 'false');", gutter)
        self.assertIn("aria-controls', `explorer-change-peek-${index}`", gutter)
        self.assertIn("aria-label", gutter)
        self.assertIn("button.title = `Show change:", gutter)

    def test_peek_markup_is_a_labelled_region_with_real_line_numbers(self):
        overview = self._overview()
        for hook in (
            'class="explorer-change-peek"',
            'role="region"',
            'aria-label="Change at line ',
            'class="explorer-change-peek-head"',
            'class="explorer-change-peek-stat"',
            'class="explorer-change-peek-range"',
            'class="explorer-change-peek-close"',
            'aria-label="Close change peek"',
            'class="explorer-change-peek-body"',
            'class="explorer-change-peek-line ',
            'class="explorer-change-peek-number"',
        ):
            with self.subTest(hook=hook):
                self.assertIn(hook, overview)
        # Unified, not side by side: HEAD lines stack above worktree lines,
        # numbered oldLine + i / line + i so the peek agrees with the Diff
        # panel and the gutter it hangs off.
        self.assertIn("lines.push({ kind: 'old', number: block.oldLine + offset, text });", overview)
        self.assertIn("lines.push({ kind: 'new', number: block.line + offset, text });", overview)
        # Highlighting reuses the Diff panel's own pair, so no second lexer
        # path appears.
        self.assertIn("highlightExplorerCode(String(text || ''), explorerDiffLanguage(index))", overview)

    def test_peek_css_declares_the_block_and_its_variants(self):
        css = self._static("css/terminals.css")
        for selector in (
            ".explorer-change-peek {",
            ".explorer-change-peek-head {",
            ".explorer-change-peek-stat {",
            ".explorer-change-peek-range {",
            ".explorer-change-peek-close {",
            ".explorer-change-peek-body {",
            ".explorer-change-peek-line {",
            ".explorer-change-peek-line.old {",
            ".explorer-change-peek-line.new {",
            ".explorer-change-peek-number {",
            ".explorer-change-peek-code {",
            ".explorer-change-peek-more {",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, css)
        # The old/new tints come from the same two tokens the Diff panel's
        # cells use — one palette by construction.
        old = self._css_rule(css, ".explorer-change-peek-line.old {")
        new = self._css_rule(css, ".explorer-change-peek-line.new {")
        self.assertIn("var(--gv-diff-delete-bg)", old)
        self.assertIn("var(--gv-diff-add-bg)", new)

    def test_marker_containment_contract_holds(self):
        css = self._static("css/terminals.css")
        # The row carries the positioning context; the marker button is
        # absolute over the gutter's left edge, outside the row's two grid
        # columns — the contract §10.4's no-nested-button argument stands on.
        row = self._css_rule(css, ".explorer-source-line[data-explorer-change] {")
        self.assertIn("position: relative", row)
        marker = self._css_rule(css, ".explorer-change-marker {")
        self.assertIn("position: absolute", marker)
        self.assertIn("left: 0", marker)
        # The wedge variant is sized to the triangle and pinned to the row's
        # edge, with an end-of-file rule hanging it off the bottom.
        wedge = self._css_rule(
            css, '.explorer-change-marker[data-explorer-change-marker-kind="deleted"] {'
        )
        self.assertIn("margin-top: -5px", wedge)
        self.assertIn(
            '.explorer-source-line[data-explorer-change="deleted"].explorer-source-change-after',
            css,
        )

    def test_peek_width_tracks_the_scroller_viewport(self):
        css = self._static("css/terminals.css")
        overview = self._overview()
        # Declared on the scroller, written in px by the existing coalesced
        # sync, and consumed by the peek rule — so wrapped and unwrapped mode
        # agree and the peek never widens the max-content container.
        view = self._css_rule(css, ".explorer-source-view {")
        self.assertIn("--explorer-source-viewport-width:", view)
        peek = self._css_rule(css, ".explorer-change-peek {")
        self.assertIn("position: sticky", peek)
        self.assertIn("left: 0", peek)
        self.assertIn("width: var(--explorer-source-viewport-width)", peek)
        self.assertIn("'--explorer-source-viewport-width'", overview)
        self.assertIn("`${parts.code.clientWidth}px`", overview)

    def test_peek_tint_tokens_are_declared_for_both_themes(self):
        tokens = self._static("css/tokens.css")
        root = tokens[tokens.index(":root {"):]
        root = root[: root.index("}")]
        light = tokens[tokens.index('[data-theme="light"] {'):]
        light = light[: light.index("}")]
        for token in ("--gv-diff-add-bg", "--gv-diff-delete-bg"):
            with self.subTest(token=token):
                self.assertIn(f"{token}:", root)
                self.assertIn(f"{token}:", light)

    def test_diff_cell_tints_migrated_to_the_shared_tokens(self):
        css = self._static("css/terminals.css")
        # Touching the legacy block migrates its literals (guardrail 7): the
        # Diff panel and the peek now share one palette.
        add = self._css_rule(css, ".explorer-diff-cell.add {")
        delete = self._css_rule(css, ".explorer-diff-cell.delete {")
        self.assertNotIn("rgba(", add)
        self.assertNotIn("rgba(", delete)
        self.assertIn("background: var(--gv-diff-add-bg);", add)
        self.assertIn("background: var(--gv-diff-delete-bg);", delete)

    def test_peek_caps_huge_blocks_and_hands_them_to_the_diff_panel(self):
        overview = self._overview()
        self.assertIn("const EXPLORER_CHANGE_PEEK_MAX_LINES = 200;", overview)
        self.assertIn("lines.slice(0, EXPLORER_CHANGE_PEEK_MAX_LINES)", overview)
        self.assertIn("more lines", overview)
        # A genuinely large block goes to the panel built for it.
        self.assertIn("setExplorerFileView(index, 'diff')", overview)

    def test_peek_is_pane_state_reapplied_by_the_render_tail(self):
        overview = self._overview()
        # The Source rebuild destroys the DOM on every search keystroke, wrap
        # toggle and fold — so the open peek is re-inserted by the same tail
        # pass that re-applies the gutter marks, never its own source of truth.
        apply = overview[
            overview.index("function applyExplorerChangeMarks(index)"):
            overview.index("function applyExplorerChangeMarkGutter(")
        ]
        self.assertIn("renderExplorerChangePeek(index);", apply)
        self.assertIn("function renderExplorerChangePeek(", overview)
        # It closes rather than floating loose when the block is gone
        # (reverted, saved over, committed away) or folded out of the DOM.
        render = overview[
            overview.index("function renderExplorerChangePeek("):
            overview.index("function toggleExplorerChangePeek(")
        ]
        self.assertIn("pane._explorerChangePeek = null;", render)
        self.assertIn("explorerChangeMarksEligible(pane)", render)
        # aria-expanded tracks the peek on the markers of the open block.
        self.assertIn("marker.setAttribute('aria-expanded', 'true');", render)

    def test_teardown_clears_the_peek_state(self):
        overview = self._overview()
        teardown = overview[
            overview.index("function teardownExplorerOverview("):
            overview.index("function explorerOverviewHtml(")
        ]
        self.assertIn("pane._explorerChangePeek = null;", teardown)

    def test_geometry_uniform_fast_path_yields_to_an_open_peek(self):
        overview = self._overview()
        geometry = overview[
            overview.index("function explorerOverviewGeometry("):
            overview.index("function explorerOverviewDeletionRow(")
        ]
        # An inserted peek is exactly what the contiguous-equal-height
        # assumption breaks on, so `uniform` is no longer computed from
        # `wrapped` and the row cap alone.
        self.assertIn("pane._explorerChangePeek", geometry)
        self.assertIn("!wrapped && !pane._explorerChangePeek", geometry)
        self.assertIn("count > EXPLORER_OVERVIEW_MAX_ROWS", geometry)

    def test_peek_uses_one_delegated_listener_scoped_to_the_container(self):
        overview = self._overview()
        wiring = overview[overview.index("function wireExplorerChangePeek("):]
        wiring = wiring[: wiring.index("The overview column (Phase 2")]
        # Wired once per element behind a dataset guard — the container
        # survives every innerHTML rewrite, so the per-render cost stays zero.
        self.assertIn("code.dataset.explorerChangePeekBound = 'true';", wiring)
        self.assertIn("toggleExplorerChangePeek(index, marker);", wiring)
        self.assertIn("closeExplorerChangePeek(index, { focus: true });", wiring)
        # Escape closes the peek only while focus is inside it — never a
        # document-level handler, so Ctrl+F's own Escape is untouched.
        self.assertIn("event.key === 'Escape'", wiring)
        self.assertIn("event.target.closest?.('.explorer-change-peek')", wiring)


if __name__ == "__main__":
    unittest.main()
