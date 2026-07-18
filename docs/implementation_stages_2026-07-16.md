# GridVibe Staged Implementation Plan

Last updated: 2026-07-18

This document groups the currently **open** issues in
[`testing_issues.md`](testing_issues.md) into implementation **stages**. Each
stage bundles issues that touch the same code surface (the same render
functions, the same backend module, the same settings pipeline) so they can be
designed and implemented as one coherent chunk instead of one-off edits that
repeatedly rework the same functions.

Stages are ordered so that foundational refactors land before the features that
depend on them. Within a stage, issues share files and can be built together.

## Open issues at a glance

| Stage | Theme | Issues |
| --- | --- | --- |
| 1 ✅ | Explorer tree rows & Git sidebar (**done 2026-07-16**) | 024, 028, 023, 018 |
| 2 ✅ | Explorer tabbed file viewer (**done 2026-07-16**) | 014, 016, 015 |
| 3 ✅ | Markdown preview presentation (**done 2026-07-16**) | 017, 030 |
| 4 ✅ | Explorer large-file / log preview (**done 2026-07-17**) | 020 |
| 5 ✅ | Terminal close & layout integrity (**done 2026-07-17**) | 022, 027 |
| 6 ✅ | Terminal input focus & targeting (**done 2026-07-17**) | 025, 026 |
| 7 ✅ | Settings & launcher configuration (**done 2026-07-18**) | 029, 013, 031 |

All 17 issues are covered and **every stage is now implemented and closed**:
Stage 1 (024, 028, 023, 018), Stage 2 (014, 016, 015), Stage 3 (017, 030),
Stage 4 (020), Stage 5 (022, 027), Stage 6 (025, 026), and Stage 7 (031, 029,
013). This plan is complete; issues reported after it was authored (032–036)
are tracked in [`testing_issues.md`](testing_issues.md) only.

---

## Stage 1 — Explorer tree rows & Git sidebar

> **Status: Implemented (2026-07-16).** All four issues below are closed. See
> **Implementation notes** at the end of this section for what shipped.

**Goal:** finish the per-row Explorer experience — file-type recognition,
right-click actions, and a complete Git review/staging workflow — while all the
code that renders tree and Git rows is open in one pass.

**Issues**
- **ISSUE-2026-024** — File and Git trees lack file type icons ✅
- **ISSUE-2026-028** — Explorer file tree has no right-click "Copy path" context menu ✅
- **ISSUE-2026-023** — Git change rows open source instead of diff view ✅
- **ISSUE-2026-018** — Add a revert action for unstaged Git changes ✅

**Shared surface**
- `web/static/js/terminals.js`: `explorerTreeRowHtml()`,
  `renderExplorerGitFileRows()`, `explorerGitOpenFile()`,
  `openExplorerFile()`, the existing clipboard helper +
  `_copyTextFallback()`, and the existing
  `EXPLORER_LANGUAGE_BY_EXTENSION` / `EXPLORER_LANGUAGE_BY_FILENAME` /
  `explorerCodeLanguage()` classifiers.
- `web/static/css/terminals.css` (icon/menu styling from `tokens.css`).
- `web/api.py` + `web/explorer.py` (new Git revert route, mirroring the
  existing stage/unstage/commit/publish routes with `GIT_TERMINAL_PROMPT=0`).

**Why grouped:** 024 and 028 both modify the two row renderers
(`explorerTreeRowHtml` + `renderExplorerGitFileRows`); 023 and 018 both extend
the Git sidebar rows in the same function. Doing them together avoids touching
`renderExplorerGitFileRows()` four separate times.

**Key work**
1. Add a shared `explorerFileTypeIconHtml(path)` reusing the existing language
   classifiers; render before the name in both tree and Git rows (024).
2. Add a delegated in-page context menu (Copy path / Copy relative path) on the
   tree and Git containers — no `window.prompt/confirm`, WebView2-safe (028).
3. Pass `openDiff: true` (and the correct staged vs worktree diff mode) from the
   changed-file click path so rows open in Diff (023).
4. Add a confirmed Revert action + `git restore --worktree -- <path>` route,
   preserving staged content and excluding untracked/conflicted files (018).

**Read-only contract:** copy-path is a read (in scope); Git revert is the only
mutation and stays inside the existing narrow Git exception. Do not broaden file
mutations.

**Sizing:** Medium. Mostly client rendering + one new backend route.

**Implementation notes (2026-07-16)**
1. **024 — file-type icons.** Added a shared `explorerFileTypeIconHtml(path)` in
   `web/static/js/terminals.js` that reuses `explorerCodeLanguage()` /
   `normalizeExplorerLanguage()` to map a file to one of ten categories
   (`code`, `shell`, `data`, `markup`, `style`, `markdown`, `config`, `sql`,
   `log`, `doc`) and renders a distinct stroke-style `currentColor` SVG glyph
   (`aria-hidden="true"`, generic `doc` fallback for unknown types). It is
   rendered before the name in `explorerTreeRowHtml()`,
   `renderExplorerGitFileRows()` (including staged/unstaged and commit-history
   rows) and `explorerDirectoryRowHtml()`. Per-category tints are new
   `--explorer-icon-*` tokens defined once per theme in `terminals.css` (mirrors
   the `--git-lane-*` pattern; no inline palette literals). The now-unused
   `EXPLORER_FILE_ICON` constant was removed.
2. **028 — copy-path context menu.** A delegated `contextmenu` handler
   (`wireExplorerCopyPathMenu`) on the tree and Git panels opens an in-page
   `#explorer-ctx-menu` (WebView2-safe, no `window.*`) offering **Copy path**
   (absolute, joined against the pane's explorer root with the root's separator
   style) and **Copy relative path**, reusing the existing `_copyText` helper.
   Rows carry `data-explorer-copy-path`; the menu is keyboard-navigable
   (Arrow/Escape), dismisses on outside click, and stays inside the read-only
   contract (copy is a read).
3. **023 — changed rows open the diff.** `explorerGitOpenFile()` now opens with
   `openDiff: true` and a section-specific `diffMode` (`worktree` for **Changes**,
   `staged` for **Staged Changes**), threaded through `openExplorerFile()` →
   `renderExplorerFile()` → `loadExplorerDiff()` (cache key + `?mode=` include
   the mode) so a partially staged file never shows the other section's hunks.
   Commit-history rows keep their existing commit-diff path.
4. **018 — revert unstaged changes.** New `_git_revert_path()` in
   `web/explorer.py` (equivalent to `git restore --worktree -- <path>`, run with
   `GIT_TERMINAL_PROMPT=0`) backing `POST /api/explorer/<id>/git/revert` in
   `web/api.py`. It preserves any staged copy, and refuses untracked, conflicted,
   and no-unstaged-change paths. The client adds a Revert button to eligible
   tracked rows under **Changes** (`explorerGitCanRevert` → modified/deleted/
   renamed only) that goes through a new reusable in-page confirm shell
   (`openGenericConfirmModal` / `#genericConfirmModal`) before calling the route
   and reloading the open file/diff.

Tests: `tests/test_api.py` gained behavioral coverage (worktree-vs-staged diff
separation; revert discard / staged-preservation / deleted-restore /
untracked-refusal / staged-only-refusal / out-of-root rejection) plus
rendered-template assertions for all four issues.

---

## Stage 2 — Explorer tabbed file viewer (foundational)

> **Status: Implemented (2026-07-16).** All three issues below are closed. See
> **Implementation notes** at the end of this section for what shipped.

**Goal:** turn the Explorer main pane into a persistent tabbed read-only viewer.
This is the spine that two other issues explicitly build on.

**Issues**
- **ISSUE-2026-014** — Replace explorer directory view with tabbed file viewer ✅
- **ISSUE-2026-016** — Markdown preview links do not open explorer tabs ✅
- **ISSUE-2026-015** — Persist open explorer tabs in saved sessions ✅

**Shared surface**
- `web/static/js/terminals.js`: `renderExplorerDirectoryRows()`,
  `wireExplorerDirectoryRows()`, `openExplorerFile()`, `renderExplorerFile()`,
  `explorerTreeRowHtml()` (the `+` open-in-tab control),
  `buildWorkspaceTerminalEntry()` (for 015).
- `web/static/js/launcher.js`: `collectFormConfig()` (015 round-trip).
- `web/api.py`: `_normalize_terminal_entries()`,
  `_normalize_workspace_layout()` (015).
- `sessions/manager.py`: `TerminalSession` (015 tab metadata).
- `web/static/css/terminals.css` (tab strip).

**Key work**
1. Per-pane tab model: one permanent non-closable dynamic-preview tab plus
   deduplicated pinned tabs keyed by normalized path; tab strip above the file
   header; focus fallback on close (014).
2. Delegated Markdown anchor handling that resolves relative links against the
   current file, validates against the Explorer root, and opens/focuses a pinned
   tab via the shared primitive; fragments scroll to headings (016).
3. Bounded saved-session fields for ordered pinned paths + active tab, captured
   in `buildWorkspaceTerminalEntry()` and normalized through the API and
   `TerminalSession`; ignore missing/out-of-root paths without failing launch (015).

**Ordering inside the stage:** 014 → then 016 and 015 in parallel.

**Sizing:** Large. 014 is a real refactor of the main pane; budget accordingly.

**Implementation notes (2026-07-16)**
1. **014 — tabbed viewer.** `explorer-list-<index>` now holds a stable shell —
   an `.explorer-tab-strip` above an `.explorer-viewer` body — created lazily by
   `explorerEnsureViewerShell()`. A per-pane tab model (`pane._explorerTabs` +
   `pane._explorerActiveTabId`, seeded by `ensureExplorerTabState()`) has one
   permanent non-closable **Preview** tab (`EXPLORER_PREVIEW_TAB_ID`) plus
   deduplicated pinned tabs keyed by `explorerNormalizeTabPath()`.
   `explorerAssignOpenTab()` routes a plain tree click into the Preview tab and a
   `+` action (new `data-explorer-tree-open-tab` control on file rows) or Markdown
   link into a pinned tab (capped at `EXPLORER_MAX_PINNED_TABS = 12`).
   `renderExplorerTabStrip()` renders/wires the strip; `activateExplorerTab()` /
   `closeExplorerTab()` switch and close (closing the active pinned tab falls back
   to its left neighbour, ultimately the Preview tab). First show routes through
   `openExplorerViewer()` (empty **"Select a file to view"** state + auto-opened
   Files tree) instead of a directory listing; `renderExplorerFile()`,
   `renderExplorerCommitDiffFile()`, `renderExplorerDirectoryRows()`, and
   `renderExplorerMessage()` now render into the viewer body and keep the strip in
   sync. Directory browsing still works but lives inside the Preview tab, so the
   Stage-1 directory-search subsystem is unchanged.
2. **016 — Markdown link navigation.** `wireExplorerMarkdownLinks()` installs a
   delegated click handler on each `.explorer-markdown-preview`.
   `explorerClassifyLink()` splits fragment / external / mailto / unsupported /
   relative; `explorerResolveRelativePath()` resolves a relative link against the
   current file and rejects `..` traversal above the Explorer root and drive /
   scheme paths. Relative links open a pinned tab via the shared primitive
   (`openExplorerFile(..., { pinned: true })`) and fragments scroll to the heading
   (`explorerScrollPreviewToHeading()`); external `http(s)` links open isolated
   with `window.open(..., 'noopener,noreferrer')` and never navigate the session
   page away.
3. **015 — saved-session persistence.** New bounded fields `explorer_open_tabs`
   (ordered pinned paths) and `explorer_active_tab` thread end to end:
   `explorerSerializeTabs()` / `buildWorkspaceTerminalEntry()` capture them for
   active-workspace saves; `web/static/js/launcher.js` carries them invisibly
   through the terminal row dataset (`parseExplorerOpenTabsDataset()`) so resaving
   a preset preserves them; `web/saved_sessions.py`
   (`_normalize_explorer_open_tabs` / `_normalize_explorer_active_tab`,
   `_normalize_terminal_entries`, `_merge_workspace_session_config`) validate,
   de-duplicate, cap, and gate them to explorer panes; `TerminalSession`
   (`sessions/manager.py`) and the runtime-state snapshot
   (`web/runtime_state.py`) carry them into a relaunched workspace, where
   `restoreExplorerPersistedTabs()` reopens readable tabs and activates the saved
   one — missing/out-of-root paths are ignored without failing launch.

Tests: `tests/test_api.py` gained rendered-template coverage for the tabbed
viewer, Markdown-link handling, and tab persistence, plus behavioral coverage for
open-tab normalization (dedup / traversal / cap / active-tab validation) and a
workspace-save round-trip; `tests/test_session_manager.py` covers
`TerminalSession` carrying the new fields (and defaulting when absent).

---

## Stage 3 — Markdown preview presentation

> **Status: Implemented (2026-07-16).** Both issues below are closed. See
> **Implementation notes** at the end of this section for what shipped.

**Goal:** make Markdown previews look polished and let users tune the reading
surface — both changes live in `.explorer-markdown-preview` styling + the render
path.

**Issues**
- **ISSUE-2026-017** — Improve Markdown preview visual hierarchy and callouts ✅ *(landed first — defines the default surface)*
- **ISSUE-2026-030** — Add user-customizable Markdown preview appearance and presets ✅ *(builds on 017's default)*

**Shared surface**
- `web/static/css/terminals.css`: `.explorer-markdown-preview` (headings,
  lists, blockquotes/callouts, tables, links) driven from `tokens.css`.
- `web/explorer.py`: `_render_markdown_preview()` (callout syntax, sanitized).
- `web/static/js/terminals.js`: preview toolbar control + preset classes / CSS
  variables; optional bounded client postprocessor for callouts.

**Key work**
1. Cohesive default theme: differentiated `h1`–`h6`, list nesting/markers,
   links/rules/tables, plus GitHub-style `[!NOTE]/[!TIP]/[!WARNING]/…` callouts
   emitted as safe semantic classes (017).
2. Appearance control (preset selector + optional font family/size) driven by
   preset classes + CSS custom properties from tokens; persisted (local pref or
   an `/api/app-config` field) and applied idempotently to new previews (030).

**Constraints:** keep sanitization, search highlighting, zoom, light/dark
contrast, and narrow-pane wrapping intact; token-only colors (no palette
literals).

**Sizing:** Medium. Mostly CSS + a small render/postprocess change.

**Implementation notes (2026-07-16)**
1. **017 — hierarchy + callouts.** `web/explorer.py` gained a bounded
   post-sanitization step: `_render_markdown_preview()` now runs
   `_augment_markdown_callouts()` on the already-`bleach`-cleaned HTML, rewriting
   GitHub-style `> [!NOTE]/[!TIP]/[!IMPORTANT]/[!WARNING]/[!CAUTION]` blockquotes
   into `<div class="md-callout md-callout-{type}">` blocks with an accessible
   `md-callout-title` (stroke-`currentColor` SVG icon + label). Because the
   transform runs *after* bleach and only injects a closed, backend-owned set of
   class names + icons, the sanitizer allowlist is unchanged and callout bodies
   stay sanitized; non-callout blockquotes and unknown keywords pass through
   untouched (adjacent callouts merge in Python-Markdown — a documented authoring
   edge case that degrades gracefully). `web/static/css/terminals.css` gives
   `.explorer-markdown-preview` a cohesive theme: differentiated `h1`–`h6`
   (dividers on `h1`/`h2`, muted `h5`/`h6`), nested list markers, styled links,
   rules, tables (header tint + zebra rows), task-item and image handling, and
   the callout blocks. Callout accents/tints are new per-theme
   `--explorer-callout-*` tokens (mirroring the `--git-lane-*` pattern; no inline
   palette literals), and code/blockquote/table chrome now reads the shared
   `--md-preview-*` custom properties so it adapts to the presets below.
2. **030 — customizable appearance + presets.** The preview surface exposes two
   orthogonal, persisted axes driven entirely by CSS custom properties defined
   from tokens: a **reading-surface preset** (`default`, `paper`,
   `high-contrast`) and a **font family** (`system`, `serif`, `mono`). A new
   header control (`data-explorer-md-appearance`, shown only for previewable
   files) opens an in-page popover (`#explorer-md-menu`, WebView2-safe, keyboard
   navigable with Arrow/Escape, radio `aria-checked` items, outside-click
   dismiss) mirroring the Stage-1 context-menu pattern. `explorerMarkdownAppearance()`
   reads a bounded `localStorage` preference (`gridvibe.mdPreviewPreset` /
   `gridvibe.mdPreviewFont`, allowlist-validated with a safe default);
   `setExplorerMarkdownAppearance()` persists it and applies it live to every open
   preview via `applyExplorerMarkdownAppearanceToElement()`
   (`md-preset-*` / `md-font-*` classes remapping `--md-preview-*`). Both preview
   render paths (`renderExplorerFile()` and `updateExplorerFileInPlace()`) apply
   the saved appearance idempotently, so it survives reopens and coexists with
   the existing zoom control, search highlighting, and light/dark themes. Paper
   and high-contrast surfaces are intentionally theme-independent reading
   surfaces (deliberate exception, same spirit as the pinned terminal canvas in
   deep-dive 7.1), with their literals confined to a single token block.

Tests: `tests/test_api.py` gained behavioral coverage for callout emission across
all five types, plain-blockquote/unknown-keyword pass-through, body sanitization
with nested content, and endpoint `preview_html` delivery, plus rendered-template
assertions for the heading/callout CSS + tokens (017) and the appearance
functions, bounded preset/font allowlists, persisted-preference keys, header
control, idempotent dual-path application, and token-driven preset classes (030).

---

## Stage 4 — Explorer large-file / log preview

> **Status: Implemented (2026-07-17).** The issue below is closed. See
> **Implementation notes** at the end of this section for what shipped.

**Goal:** stop discarding the newest content of oversized append-only files and
give previews a predictable head/tail policy.

**Issues**
- **ISSUE-2026-020** — Large log previews discard the newest entries ✅

**Shared surface**
- `web/explorer.py`: `EXPLORER_FILE_PREVIEW_MAX_BYTES`, `read_file_prefix`
  (add a ranged/tail read for local + SFTP backends).
- `web/api.py`: `get_explorer_file()` (head/tail selection + range metadata).
- `web/static/js/terminals.js`: "Showing the last N" messaging.

**Key work:** validated/bounded preview limit; tail read for `.log` and
append-oriented formats with UTF-8/line-boundary safety; return retained
start/end + total size; client message; keep search bounded to the returned
preview.

**Note:** standalone, but it touches the same `renderExplorerFile()` /
`get_explorer_file()` path as Stage 2's tabbed viewer. If Stage 2 is scheduled
first, land 020 against the new viewer to avoid reworking the message surface.

**Sizing:** Medium (backend read-range work is the bulk).

**Implementation notes (2026-07-17)**
1. **Backend read-range.** `web/explorer.py` gained a `read_file_suffix(file_path,
   max_bytes, total_size)` ranged/tail read on both `_LocalExplorerBackend` and
   `_SftpExplorerBackend` (seek to `total_size - max_bytes`, read forward — the
   SFTP variant works against the `io.BytesIO` the SFTP handle exposes). A shared
   `read_explorer_file_preview(backend, file_path, *, total_size, tail)` chooses the
   head or tail window and returns byte-range metadata; it is the single place
   that decides which end of an oversized file is kept.
2. **Head/tail policy.** `_is_tail_preview_file()` classifies append-oriented
   files by reusing the existing language map (`.log` → `"log"`), so only logs
   keep their **newest** bytes; every other oversized text file keeps its opening
   bytes (the explicit, predictable head policy). The 1 MiB
   `EXPLORER_FILE_PREVIEW_MAX_BYTES` cap is unchanged (bounded, no browser-stall
   or oversized-read regression).
3. **UTF-8 / line safety.** `_trim_tail_preview_to_boundary()` drops a tail
   window's partial leading line up to the first newline (and, when there is no
   usable newline, skips leading UTF-8 continuation bytes) so a tail preview never
   begins mid-character or mid-line.
4. **API + client.** `get_explorer_file()` in `web/api.py` now returns
   `preview_mode` (`head`/`tail`), `preview_start_byte`, `preview_end_byte`, and
   `total_size` alongside the existing `truncated`/`size`. In
   `web/static/js/terminals.js`, `explorerPreviewTruncationLabel()` turns that
   metadata into `Showing the last <N> of <M>` for logs and `Showing the first
   <N> of <M>` otherwise, replacing the generic `Preview truncated`. Search stays
   bounded to the returned preview (it already operates on the delivered `content`
   only).

Tests: `tests/test_api.py` gained `test_trim_tail_preview_to_boundary_variants`
(partial-line / trailing-newline / continuation-byte trimming),
`test_explorer_log_preview_retains_tail_and_range_metadata` (retained end marker,
excluded start marker, line-boundary start, self-consistent range metadata),
`test_explorer_non_log_preview_retains_head`, `test_explorer_preview_at_exact_limit_is_not_truncated`,
`test_explorer_log_preview_tail_is_utf8_line_safe` (multibyte boundary),
`test_explorer_remote_log_preview_retains_tail` (SFTP parity), and
`test_terminals_page_explorer_preview_tail_message` (client messaging).

---

## Stage 5 — Terminal close & layout integrity

> **Status: Implemented (2026-07-17).** Both issues below are closed. See
> **Implementation notes** at the end of this section for what shipped.

**Goal:** make closing a terminal pane surgical — expand exactly the right
neighbor and leave every other pane (including Explorer/browser state) untouched.

**Issues**
- **ISSUE-2026-022** — Closing a terminal expands unrelated panes in complex split layouts ✅
- **ISSUE-2026-027** — Closing a terminal resets explorer/browser pane state in the group ✅

**Shared surface**
- `web/static/js/terminals.js`: `closeTerminalPane()`,
  `buildTerminalCloseRectsBySessionId()`,
  `buildTerminalCloseRectsForSideGroup()`, `findTerminalCloseNeighbor()`,
  `pendingSplitRestore`, `initialLoad()`, `splitColumnWeights` /
  `splitRowWeights`.

**Why grouped:** both are defects in the single `closeTerminalPane()` flow — 022
is the geometry (which panes grow), 027 is the state loss (the full-grid
`initialLoad()` rebuild). Fixing 027 by moving to an in-place close naturally
interacts with 022's rect computation, so design them together.

**Key work**
1. Prefer single-neighbor (longest shared border) expansion in the side-group
   fallback; preserve user split weights across column/row count changes (022).
2. Replace the full `initialLoad()` rebuild for single-pane closes with an
   in-place card removal + geometry reflow (mirroring split add), so sibling
   Explorer/browser panes keep open files, expanded trees, Git sidebar, and URL
   (027).

**Sizing:** Medium–Large (layout math + lifecycle change; needs solid tests).

**Implementation notes (2026-07-17)**
1. **022 — single-neighbor expansion.** `buildTerminalCloseRectsForSideGroup()` in
   `web/static/js/terminals.js` was refactored around a shared
   `terminalCloseRectsForExpandingContacts(plan, side, contactsToExpand)` helper
   (the overlap + gap/area invariants live there). The side-group fallback now
   first tries expanding only the single contact with the greatest shared border
   and uses it whenever it satisfies those invariants; it drops to the full
   side-group expansion only when the single-pane attempt would leave a gap or
   overlap. The primary single-neighbor path (`findTerminalCloseNeighbor` +
   `canAbsorbClosedRect`) is unchanged.
2. **022 — proportion preservation.** A valid close preserves the grid's bounding
   box, so the pre-close `splitColumnWeights`/`splitRowWeights` map 1:1 onto the
   reflowed grid. Both `closeTerminalPane()` and `closeSplitPane()` now capture
   them into `pendingSplitRestore` (via `cloneSplitTrackWeights`), and
   `initialLoad()` re-applies them before `applySplitSlotGeometry()` — previously
   the `buildGrid()` → `clearSplitSlotGeometry()` teardown nulled the weights and
   reset every surviving pane to uniform proportions.
3. **027 — sibling state across the rebuild.** A fully general in-place close
   would have to re-index every pane's DOM ids and re-wire its index-bound event
   handlers (effectively a rebuild), so the sanctioned fallback from the issue was
   taken: capture and restore the surviving panes' client state across the
   existing rebuild. Before the rebuild, `closeTerminalPane()` calls
   `captureSurvivingPaneClientState()` — a per-session-id snapshot of each
   surviving explorer pane (tree/Git sidebar flags, pinned open tabs + active tab,
   the active Preview file + view) and browser pane (live URL) — into a new
   `pendingCloseClientState`. `initialLoad()`, **only** for that close-driven
   rebuild (gated on `groupId`), overlays the explorer fields and browser URL onto
   the freshly fetched session objects so `buildGrid()` and
   `restoreExplorerPersistedTabs()` seed them, then routes each close-affected
   explorer pane through `restoreExplorerPaneFromClose()` (tabbed-viewer entry
   point + a Preview-file reopen) instead of the plain-listing default. The
   snapshot is cleared on consume and in `resetSessionView()`, so non-close loads
   are untouched. Browser scroll cannot survive a cross-origin iframe reload (a
   documented limitation); the URL is preserved.

Tests: `tests/test_api.py` gained `test_terminals_page_close_prefers_single_neighbor_expansion`
and `test_terminals_page_close_preserves_split_track_weights` (022) plus
`test_terminals_page_close_preserves_sibling_pane_state` (027), asserting the new
helpers, single-pane preference, weight capture/restore, state capture, session
overlay, and viewer-based restore are wired end to end in the served client.

---

## Stage 6 — Terminal input focus & targeting

> **Status: Implemented (2026-07-17).** Both issues below are closed. See
> **Implementation notes** at the end of this section for what shipped.

**Goal:** make the active input target visible and make voice honor the same
broadcast rules as typing — both revolve around `_focusedTerminalIndex` and the
input-forwarding path.

**Issues**
- **ISSUE-2026-025** — Highlight the currently active terminal pane
- **ISSUE-2026-026** — Voice transcription ignores Broadcast typing and reaches one terminal

**Shared surface**
- `web/static/js/terminals.js`: `_focusedTerminalIndex`,
  `forwardTerminalInput()`, `_sendToTerminal()`, the `voice_result` handler,
  `setBroadcastInput()`, `wirePaneInputForwarding()`.
- `web/static/css/terminals.css` (active-pane treatment, distinct from
  `broadcast-input`).

**Why grouped:** both concern how input is routed to panes. 025 centralizes
focus state (`setFocusedTerminal(index)`), and 026 routes the final voice
transcript through the same broadcast fan-out `forwardTerminalInput()` already
uses. Touching focus/forwarding once keeps the two consistent.

**Key work**
1. `setFocusedTerminal(index)` helper that toggles a semantic active class +
   ARIA state on exactly one pane, routed through all focus paths (025).
2. In the `voice_result` final branch, when `broadcastInputActive`, mirror the
   transcript to every plain pane (reusing the `forwardTerminalInput` filter that
   skips explorer/browser panes); keep interim previews on the recording pane (026).

**Sizing:** Small–Medium.

**Implementation notes (2026-07-17)**
1. **025 — active-pane highlight (focus-driven, never output-driven).** The
   highlight is tied to *real DOM keyboard focus* so it can never disagree with
   where typing or voice actually lands. A delegated `focusin`/`focusout` pair on
   `document` (wired once) marks whichever plain terminal currently holds focus:
   `focusin` inside a `.terminal-container` (that is not an explorer/browser pane)
   calls `setFocusedTerminal(slot)`, and `focusout` clears the mark
   (`clearActiveTerminalHighlight()`) unless focus is moving to another plain
   terminal (whose own `focusin` repaints it). Visual state lives in
   `paintActiveTerminalCard(index)` — it adds `terminal-active` +
   `aria-current="true"` to exactly one `tc-<index>` and strips both from every
   other `.terminal-container` (one-active-pane enforcement); `isPlainTerminalCard()`
   validates explorer/browser panes out so they are never marked. Crucially the
   highlight is **not** driven by `term.onData`: TUI apps with mouse reporting
   (vim, opencode, …) emit mouse-move escape sequences through `onData`, so an
   earlier draft that set focus from input made the highlight *follow the mouse*
   into an unfocused pane (visible on a restored split running opencode) —
   `forwardTerminalInput()` no longer touches selection at all. `_focusedTerminalIndex`
   now equals the highlighted pane exactly (or -1 when nothing is selected) and is
   the single input target for voice / push-to-talk / search; it is cleared on
   blur to non-terminal, so a pane that is not visibly selected never silently
   receives dictation, and push-to-talk (`_findPttTerminalIndex()`) targets the
   focused terminal only (no fall-back to the first pane). Teardown resets it via
   `resetFocusedTerminal()` in `teardownCurrentGrid()`. `web/static/css/terminals.css`
   keeps the token-only `.terminal-container.terminal-active` treatment — a heavier
   2px inset `--t-accent` ring plus an accent header rule — distinct from and
   prominent alongside the 1px broadcast border, with the same
   `:not(.explorer-pane):not(.browser-pane)` exclusions.
2. **026 — voice honors broadcast.** The keyboard peer fan-out was extracted from
   `forwardTerminalInput()` into a shared `broadcastInputToPeers(sourceIndex, data)`
   helper (the `broadcastInputActive` gate, `_noteBroadcastActivity()`, and the
   explorer/browser-skipping `terminals[otherIndex]?.term` filter all live there
   once). The `voice_result` final branch now delivers the committed transcript to
   the recording pane via `_sendToTerminal()` and then calls the same
   `broadcastInputToPeers(index, text)`, so a dictated transcript fans out to every
   plain pane exactly like typing when Broadcast is on, with no duplicate to the
   source. Interim (non-final) previews stay isolated to the recording pane
   (`_showVoicePreview`), unchanged. Enabling Broadcast typing also focuses a
   terminal immediately (`setBroadcastInput(true)` → `focusActiveOrDefaultTerminal()`,
   preferring the sticky `_focusedTerminalIndex`, else the first attached plain
   terminal), so the user can start typing without first clicking a pane.

Tests: `tests/test_api.py` gained `test_active_terminal_pane_paints_a_single_focused_card`
(one-active-pane enforcement, semantic class + `aria-current`, explorer/browser
exclusion), `test_active_terminal_pane_tracks_real_dom_focus` (delegated
focusin/focusout wiring, clear-on-blur-to-non-terminal drops both highlight and
input target, `forwardTerminalInput`/`onData` never touches selection, teardown
reset), `test_push_to_talk_targets_only_the_selected_terminal` (no stale
last-selected target), `test_broadcast_enable_focuses_a_terminal_for_immediate_typing`
(broadcast auto-focus), `test_active_terminal_pane_has_distinct_token_style`
(token-only accent, 2px ring vs broadcast 1px, distinct header rule), and
`test_voice_transcript_honours_broadcast_typing` (final fan-out through the shared
helper, interim isolation, shared-helper reuse count); the existing
`test_input_forwarding_goes_through_the_broadcast_helper` was updated for the
extracted `broadcastInputToPeers` helper.

---

## Stage 7 — Settings & launcher configuration

> **Status: Implemented (2026-07-18).** All three issues below are closed. See
> **Implementation notes** at the end of this section for what shipped.

**Goal:** extend the settings/launcher configuration pipeline for the two
config-shaped requests.

**Issues**
- **ISSUE-2026-031** — App Settings action buttons overlap voice settings content ✅ *(landed first — fixed the modal layout before it grew)*
- **ISSUE-2026-029** — Settings UI omits terminal config (font family, size, max sessions) ✅
- **ISSUE-2026-013** — Add per-agent auto-mode toggles to terminal settings ✅

**Shared surface**
- `web/static/css/launcher.css`: `.app-settings-card`, `.settings-grid`
  overflow/scroll behavior, `.app-settings-actions` (031).
- `templates/index.html` (App Settings modal fields; 029/031) and the launcher
  agent-settings row (013).
- `web/static/js/launcher.js`: `collectAppSettings()` / `saveAppSettings()`
  (029), `buildTerminalInitialCommand()` / `collectTerminalDrafts()` (013).
- `web/api.py`: `_normalize_app_config_update()` (029),
  `_run_startup_sequence()` + saved-session/workspace normalization (013).
- `web/config.py` `RuntimeConfig` (029), agent registry / `agent_registry.json`
  (013), `sessions/manager.py` `TerminalSession` (013).

**Why grouped:** 029 and 013 both flow through the launcher settings UI →
normalization → `RuntimeConfig`/`TerminalSession` → session launch pipeline, and
both round-trip through saved sessions and the `/api/app-config` contract. 031 is
the App Settings modal's layout defect — the same modal 029 adds fields to — so
fixing the body scroll model **before** adding terminal settings prevents the
overlap from getting worse.

**Key work**
1. Restore the App Settings body scroll (drop the `overflow: visible` override on
   `.app-settings-card .settings-grid`) so the pinned Save/Cancel row never
   overlaps content, verified with voice enabled (031).
2. Add a validated `terminal` branch (font family, bounded font size, clamped
   max_sessions) to the App Settings modal, `collectAppSettings()`, and
   `_normalize_app_config_update()`; propagate to open windows via the existing
   app-config update contract (029).
3. Add per-terminal `agent_auto_mode` with registry-defined flags; compose the
   startup command from validated agent metadata; round-trip through drafts,
   saved sessions, and workspace save (013).

**Ordering inside the stage:** 031 → 029 (029's new fields must land on top of the
fixed, scrollable modal, not the overlapping one) → 013 (independent).

**Constraints:** config keys must be wired end-to-end through `RuntimeConfig`
(no dead keys); never persist secrets into new local state; keep the modal body
scrollable so future fields don't reintroduce the 031 overlap.

**Sizing:** Small (031 CSS) + Medium (029/013 settings paths).

**Implementation notes (2026-07-18)**
1. **031 — App Settings scroll region.** The
   `.app-settings-card .settings-grid { overflow: visible; padding-right: 0 }`
   override in `web/static/css/launcher.css` was removed, along with the
   narrow-width breakpoint block that re-restored `overflow: auto` (now
   redundant). The App Settings body therefore uses the base `.settings-grid`
   scroll model (`overflow: auto; min-height: 0; padding-right: 4px`) at every
   width inside the `.modal-card` grid rows (`auto minmax(0, 1fr) auto`), so the
   pinned `.app-settings-actions` row never overlaps content — taller content
   (voice enabled, or the new 029 fields) extends the scrollable region instead.
2. **029 — terminal settings end to end.** The App Settings modal
   (`templates/index.html`) gained a **Terminal** section with bounded inputs for
   Font Family, Font Size (6–48), and Max Sessions (1–16), collected/synced via a
   new `terminal` section in `DEFAULT_APP_SETTINGS` / `syncAppSettingsForm()` /
   `collectAppSettingsForm()` (`web/static/js/launcher.js`).
   `_normalize_app_config_update()` in `web/api.py` validates the branch (font
   family trimmed, rejected when empty or over `TERMINAL_FONT_FAMILY_MAX_LENGTH`
   with fallback to the current runtime value; font size and max sessions
   int-parsed with fallback and clamped), with the bounds defined once in
   `web/config.py` (`TERMINAL_FONT_SIZE_MIN/MAX`, `MAX_SESSIONS_MIN/MAX`) and the
   same clamps applied in `RuntimeConfig.refresh()` so hand-edited configs cannot
   smuggle out-of-range values. `_public_app_config()` returns the block, and the
   existing app-config update contract propagates live changes:
   `_broadcast_app_config_update()` / `notifyAppConfigUpdated()` carry
   `terminal.font_family`/`font_size`, and `applyAppConfigTerminalFont()` in
   `web/static/js/terminals.js` applies them to every open xterm pane immediately
   (with refit); `max_sessions` applies to new launches only, per the modal hint.
3. **013 — per-agent auto mode.** Flags are registry-owned: `agent_registry.json`
   entries may declare `auto_mode` (`flag` + `description`) — `claude
   --enable-auto-mode`, `codex --full-auto`, `copilot --allow-all-tools`;
   `opencode`/`kilo` register none. `_agent_auto_mode_flag()` in `web/agents.py`
   accepts a flag only if it starts with `-` and has no whitespace (a registry
   typo can never smuggle a second command); `_agent_options()` exposes it as
   `auto_mode_flag`. The launcher agent row (`web/static/js/launcher.js`) shows an
   accessible **Auto mode** checkbox only when the selected built-in agent has a
   registered flag (`syncTerminalAgentAutoModeState()` recomputes visibility +
   `Launches as "<agent> <flag>"` help text on selection/mode changes; the toggle
   clears when unavailable, custom agents stay verbatim). The per-terminal
   boolean `agent_auto_mode` round-trips through drafts, saved-session
   normalization (`web/saved_sessions.py`, gated to agent startup mode),
   workspace saves (`buildWorkspaceTerminalEntry()` +
   `_merge_workspace_session_config()`), `TerminalSession`
   (`sessions/manager.py`), and the runtime-state snapshot
   (`web/runtime_state.py`) — while the persisted `initial_command` always stays
   the bare agent key so preflight detection keeps working. The flag is composed
   only at launch: `_run_startup_sequence()` (`web/terminal_io.py`) sends
   `_compose_agent_startup_command(session)` (`web/agents.py`), which appends the
   registered flag exactly once for a built-in agent in agent mode with the
   toggle on and returns everything else verbatim.

Tests: `tests/test_api.py` gained a `SettingsLauncherConfigTestCase` with 17
focused tests — the 031 scroll-region regression test, the 029 app-config
return/persist/clamp/reject, `RuntimeConfig` clamp, modal collect wiring, and
live-font-update tests (plus an updated broadcast-payload assertion in
`test_app_config_endpoint_persists_theme_and_voice_settings`), and the 013
registry-flag exposure/validation, command-composition, startup-sequence,
saved-session/workspace/sessions-API/runtime-state round-trip, and launcher
toggle-wiring tests; `tests/test_session_manager.py` gained
`test_create_sessions_carries_agent_auto_mode`.

---

## Cross-stage dependencies

- **Stage 2 (014) is a magnet.** The tabbed viewer changes `openExplorerFile()`
  / `renderExplorerFile()` and adds a `+` control to `explorerTreeRowHtml()`.
  Three items touch adjacent code:
  - Stage 1 / **023** (open-in-diff) rides the same open path — do 023 either
    *before* 014 as a small standalone change, or fold its `openDiff` mode into
    the 014 open primitive.
  - Stage 1 / **024** (icons) and **028** (context menu) also edit
    `explorerTreeRowHtml()`. Design the row markup once — coordinate the `+`
    button (014), the leading icon (024), and the context menu (028) so the row
    isn't rewritten three times. Practical order: **Stage 1 first**, then let 014
    build on the finished row, *or* schedule 014 first and rebase 024/028 onto it.
  - Stage 4 / **020** shares `renderExplorerFile()` messaging.
- **Stage 3 / 016 link:** ISSUE-2026-016 lives in Stage 2 (it needs the tab
  primitive) but is Markdown-flavored; keep its link-classification logic
  consistent with the Stage 3 sanitizer/callout work.
- **Stage 5 pair is atomic:** don't ship 027's in-place close without
  re-validating 022's rect math against it (and vice versa).
- **Stages 1, 3, 5, 6, 7 are mutually independent** and can be parallelized
  across people/branches.

## Suggested overall order

1. **Stage 1** — self-contained Explorer row/Git polish; also settles the row
   markup before the 014 refactor.
2. **Stage 2** — the big tabbed-viewer refactor and its dependents (014 → 016, 015).
3. **Stage 4** — fold large-file/log preview onto the new viewer.
4. **Stage 3** — Markdown default theme then presets.
5. **Stage 5** — terminal close correctness + state preservation.
6. **Stage 6** — focus highlight + voice broadcast.
7. **Stage 7** — settings/launcher configuration.

Stages 5, 6, and 7 have no dependency on the Explorer stages and can be pulled
forward if terminal-side work is the priority.

## Per-stage test expectations

Every stage adds focused tests in `tests/test_api.py` (rendered-template +
behavioral), plus `tests/test_session_manager.py` where session state changes
(Stages 2/7). Run `make check` (or `python tests/run_tests.py` +
`python -m ruff check .` on Windows without `make`) before handing back each
stage.
