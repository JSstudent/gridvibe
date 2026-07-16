# GridVibe Staged Implementation Plan

Last updated: 2026-07-16

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
| 1 | Explorer tree rows & Git sidebar | 024, 028, 023, 018 |
| 2 | Explorer tabbed file viewer (foundational) | 014, 016, 015 |
| 3 | Markdown preview presentation | 017, 030 |
| 4 | Explorer large-file / log preview | 020 |
| 5 | Terminal close & layout integrity | 022, 027 |
| 6 | Terminal input focus & targeting | 025, 026 |
| 7 | Settings & launcher configuration | 029, 013, 031 |

All 17 open issues are covered. Stages 1, 3, 5, 6, 7 are independent of each
other and can be scheduled in any order. Stage 2 is the largest refactor and
several other items relate to it (see **Cross-stage dependencies**).

---

## Stage 1 — Explorer tree rows & Git sidebar

**Goal:** finish the per-row Explorer experience — file-type recognition,
right-click actions, and a complete Git review/staging workflow — while all the
code that renders tree and Git rows is open in one pass.

**Issues**
- **ISSUE-2026-024** — File and Git trees lack file type icons
- **ISSUE-2026-028** — Explorer file tree has no right-click "Copy path" context menu
- **ISSUE-2026-023** — Git change rows open source instead of diff view
- **ISSUE-2026-018** — Add a revert action for unstaged Git changes

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

---

## Stage 2 — Explorer tabbed file viewer (foundational)

**Goal:** turn the Explorer main pane into a persistent tabbed read-only viewer.
This is the spine that two other issues explicitly build on.

**Issues**
- **ISSUE-2026-014** — Replace explorer directory view with tabbed file viewer *(must land first)*
- **ISSUE-2026-016** — Markdown preview links do not open explorer tabs *(depends on 014's tab primitive)*
- **ISSUE-2026-015** — Persist open explorer tabs in saved sessions *(depends on 014's tab model)*

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

---

## Stage 3 — Markdown preview presentation

**Goal:** make Markdown previews look polished and let users tune the reading
surface — both changes live in `.explorer-markdown-preview` styling + the render
path.

**Issues**
- **ISSUE-2026-017** — Improve Markdown preview visual hierarchy and callouts *(land first — defines the default surface)*
- **ISSUE-2026-030** — Add user-customizable Markdown preview appearance and presets *(builds on 017's default)*

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

---

## Stage 4 — Explorer large-file / log preview

**Goal:** stop discarding the newest content of oversized append-only files and
give previews a predictable head/tail policy.

**Issues**
- **ISSUE-2026-020** — Large log previews discard the newest entries

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

---

## Stage 5 — Terminal close & layout integrity

**Goal:** make closing a terminal pane surgical — expand exactly the right
neighbor and leave every other pane (including Explorer/browser state) untouched.

**Issues**
- **ISSUE-2026-022** — Closing a terminal expands unrelated panes in complex split layouts
- **ISSUE-2026-027** — Closing a terminal resets explorer/browser pane state in the group

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

---

## Stage 6 — Terminal input focus & targeting

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

---

## Stage 7 — Settings & launcher configuration

**Goal:** extend the settings/launcher configuration pipeline for the two
config-shaped requests.

**Issues**
- **ISSUE-2026-031** — App Settings action buttons overlap voice settings content *(land first — fix the modal layout before it grows)*
- **ISSUE-2026-029** — Settings UI omits terminal config (font family, size, max sessions)
- **ISSUE-2026-013** — Add per-agent auto-mode toggles to terminal settings

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
