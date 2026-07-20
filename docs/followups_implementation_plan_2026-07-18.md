# GridVibe Follow-up Implementation Plan

Last updated: 2026-07-20

This plan covers the **follow-up refinements** captured in
[`todos.txt`](todos.txt) after the seven original stages in
[`implementation_stages_2026-07-16.md`](implementation_stages_2026-07-16.md)
shipped. Every original stage is now closed; the items here are the second-pass
polish, defects, and small feature requests that surfaced while using the
delivered work.

**Scope rules for this document**

- It **references** the issues already filed in
  [`testing_issues.md`](testing_issues.md) (ISSUE-2026-032, -033, -034, -035)
  and does not restate their full reports. Anything not already filed is
  described inline here — **no new issues are opened** for this pass.
- Items are grouped by the **original stage** they belong to (so this maps 1:1
  onto `todos.txt`), and **within each stage the simplest change is listed
  first**.
- The stage sections below are reference/spec material. The recommended order of
  work — sorted so the simplest, lowest-risk changes land first and the one big
  refactor lands last — is in **[Suggested sequence](#suggested-sequence)**.
- Every previously ambiguous point is now settled in **[Resolved
  decisions](#resolved-decisions)** (OD-1…OD-14); each item section bakes its
  decision inline, so no item is gated on further confirmation.

Sizing legend: **Trivial** (isolated CSS/markup, minutes) · **Small** (one
function or one classifier/registry entry) · **Medium** (client + one backend
route, or a settings-pipeline branch) · **Large** (touches the tab-state model
and its persistence).

## At a glance

Done ✅ = all 21 items implemented: Waves 1–4 (2026-07-18/19), then 2.e, 5.a, and 2.f (Wave 5, 2026-07-19), and finally 2.g and 2.d (Wave 5, 2026-07-20). **This plan is complete.**

| # | Stage | Item | Refs | Size | Done |
| --- | --- | --- | --- | --- | --- |
| 1.a | 1 · Git sidebar | Reload Files tree after commit / stage-all / discard-all | ISSUE-2026-034 | Small | ✅ |
| 1.b | 1 · Git sidebar | "Stage All" button in the Changes header | ISSUE-2026-032 | Medium | ✅ |
| 1.c | 1 · Git sidebar | "Discard All" button for unstaged changes | — (extends 018) | Medium | ✅ |
| 1.d | 1 · Git sidebar | "Open in new tab" (`+`) button ignores light/dark theme | — | Trivial | ✅ |
| 2.a | 2 · Tabbed viewer | Remove the vestigial Back button on the Preview tab | — | Trivial | ✅ |
| 2.b | 2 · Tabbed viewer | `go.mod` (and peers) not displayed | — | Small | ✅ |
| 2.c | 2 · Tabbed viewer | Valid UTF-8 rejected when sample splits a character | ISSUE-2026-035 | Small | ✅ |
| 2.d | 2 · Tabbed viewer | Clicking a tree directory opens it in Preview to browse | — | Medium | ✅ |
| 2.e | 2 · Tabbed viewer | Preserve per-tab view mode + scroll across tab swaps | — | Large | ✅ |
| 2.f | 2 · Tabbed viewer | Persist tab modes / scroll / appearance / active tab in sessions | ISSUE-2026-033 | Large | ✅ |
| 2.g | 2 · Tabbed viewer | Tab drag-reorder, middle-click close, double-click preview→pin | — | Medium | ✅ |
| 3.a | 3 · Markdown | Add a VS Code-style "gray surface / white text" preset | — (extends 030) | Small | ✅ |
| 3.b | 3 · Markdown | Colour heading titles in **Source** view too | — | Small | ✅ |
| 4.a | 4 · Large-file preview | Raise preview cap from 1 MiB toward 10 MiB | — (extends 020) | Small | ✅ |
| 5.a | 5 · Terminal close | Closing a terminal resets the shown tab's mode + scroll | — (pairs with 2.e) | Medium | ✅ |
| 6.a | 6 · Focus/broadcast | Drop all highlights when broadcast focus leaves to dead space | — (extends 025/026) | Small | ✅ |
| 7.a | 7 · Settings | Add Kimi CLI to the agent registry | — | Small | ✅ |
| 7.b | 7 · Settings | Verify/fix agent auto-mode launch + descriptive text for all agents | — (extends 013) | Medium | ✅ |
| 7.c | 7 · Settings | "Apply to all active sessions" toggle + font preset dropdown | — (extends 029) | Medium | ✅ |

---

## Stage 1 — Explorer tree rows & Git sidebar

**Shared surface:** `renderExplorerGitSidebarContent()`,
`performExplorerGitAction()`, `explorerGitStageFile()`, `reloadExplorerTree()` in
`web/static/js/terminals.js`; the `POST /api/explorer/<id>/git/*` routes in
`web/api.py`; the `_git_*_path()` helpers in `web/explorer.py`. All four items
edit the same sidebar renderer and Git-action path, so build them together to
avoid reworking `renderExplorerGitSidebarContent()` repeatedly.

### 1.d — "Open in new tab" (`+`) button ignores light/dark theme *(Trivial, do first)*

The `+` control (`explorer-open-tab-btn`, `terminals.js:9104`) reuses
`explorer-search-btn` markup but does not pick up theme tokens correctly, so it
looks wrong in one of the two themes. Give
`.explorer-open-tab-btn` its own token-driven rule in `terminals.css`
(foreground/border/hover from the same `--explorer-*` / `--t-*` tokens the
sibling tree controls use — no palette literals, per Regression Guardrail 7).
Verify in both light and dark.

### 1.a — Reload the Files tree after Git actions *(ISSUE-2026-034)*

Per the filed report: after a successful **commit**, `performExplorerGitAction`
updates the Git sidebar but never calls `reloadExplorerTree(index)`, so the
Files tree and the open file/diff go stale. Extend the fix to **every mutating
Git action that changes working-tree state** — commit, the new stage-all (1.b),
and the new discard-all (1.c), plus the existing per-row stage/unstage/revert —
by reloading the tree (guarded internally on `pane._explorerTreeSidebarOpen`) and
refreshing/invalidating any open diff for the affected paths so stale staged
hunks are not shown. Follow the proposed solution in the issue; add the tests it
specifies.

### 1.b — "Stage All" button in the Changes header *(ISSUE-2026-032)*

Per the filed report: add a compact **Stage All** button to the **Changes**
section header, disabled when the unstaged list is empty or an action is busy,
wired to a new `explorerGitStageAll(index)` → `POST
/api/explorer/<id>/git/stage-all` route backed by `_git_stage_all_paths()` in
`web/explorer.py` (`git add -A` scoped to the repo root, `GIT_TERMINAL_PROMPT=0`).
Follow the issue's proposed solution and tests. Its success handler routes
through the shared refresh in 1.a.

### 1.c — "Discard All" button for unstaged changes *(no issue — extends ISSUE-2026-018)*

Mirror the existing single-row **Revert** action (Stage 1 / 018,
`_git_revert_path` = `git restore --worktree -- <path>`) as a bulk **Discard
All** button in the **Changes** header, next to Stage All (1.b). Requirements:

- New `_git_discard_all_paths(backend, root_path)` in `web/explorer.py` and
  `POST /api/explorer/<id>/git/discard-all` in `web/api.py`, run with
  `GIT_TERMINAL_PROMPT=0`.
- **Reuse the existing in-page confirm shell** (`openGenericConfirmModal` /
  `#genericConfirmModal`) — this is irreversible (Regression Guardrail 8); never
  `window.confirm`.
- Preserve the same safety envelope as single-file revert: operate on the
  **worktree only** so staged content is preserved; **exclude untracked and
  conflicted files**. Discard All must **never** run `git clean` — discarding
  untracked files is a delete that crosses the read-only contract (decided in
  **OD-1**: worktree restore of tracked files only, matching single-file Revert).
- Success routes through the shared refresh in 1.a.

**Read-only contract:** stage-all and discard-all stay inside the narrow,
pre-existing Git exception (Key Concepts). Do not broaden file mutations beyond
Git, and do not delete untracked files (OD-1: no `git clean`).

---

## Stage 2 — Explorer tabbed file viewer

**Shared surface:** the per-pane tab model (`pane._explorerTabs`,
`pane._explorerActiveTabId`, `EXPLORER_PREVIEW_TAB_ID`, `activateExplorerTab()`,
`closeExplorerTab()`, `renderExplorerTabStrip()`), `openExplorerFile()`,
`renderExplorerFile()`, `explorerAssignOpenTab()` in
`web/static/js/terminals.js`; `_explorer_content_looks_binary()` in
`web/explorer.py`; and the persistence path in `web/saved_sessions.py` /
`web/runtime_state.py`. Items 2.e/2.f/2.g reshape the tab model itself and are the
heaviest work in this whole plan.

### 2.a — Remove the vestigial Back button on the Preview tab *(Trivial)*

Since ISSUE-2026-014 the main pane is always the tabbed viewer and directory
browsing lives inside the permanent **Preview** tab, so the old single-file
**Back** button no longer has a meaningful target. Remove it from the
Preview-tab render path (and its handler) so the header shows only the tab strip
+ file header. **Decided (OD-3):** drop the old Back button outright — the
in-Preview directory navigation from 2.d is served by a lightweight breadcrumb
instead, not a resurrected Back control.

### 2.b — `go.mod` and peers not displayed *(Small)*

**Root cause confirmed (OD-2):** the suppression is in the **backend**
eligibility check, not the frontend classifier. `GET
/api/explorer/<id>/file?path=go.mod` returns **400** and the pane shows
*"Explorer file format is not supported for editor preview"* — that message is
raised by `_explorer_editor_language()` (`web/explorer.py:247`) when
`_explorer_code_language()` returns `None`, because `CODE_PREVIEW_FILENAMES`
(`web/explorer.py:144`) has no `go.mod` entry and `CODE_PREVIEW_LANGUAGES` has no
`.mod` / `.sum` / `.work` extension.

**Fix at the backend layer first:** add `'go.mod': 'go'`, `'go.sum': 'text'`,
`'go.work'`/`'go.work.sum'` to `CODE_PREVIEW_FILENAMES` (and any other common
extension-less/uncommon config files found alongside them) so the file becomes
eligible and stops returning 400. Then **mirror the same entries in the frontend
classifier** `EXPLORER_LANGUAGE_BY_FILENAME` (`terminals.js:7707`) so highlighting
matches the backend language. Add both a backend eligibility test (`go.mod` no
longer 400s / resolves to `go`) and the frontend rendered-template assertion.

### 2.c — Valid UTF-8 rejected when the sample splits a character *(ISSUE-2026-035)*

Per the filed report: `_explorer_content_looks_binary()` decodes only the first
4,096 bytes strictly, so a multibyte character straddling the boundary triggers a
false "binary" rejection. Follow the issue's proposed solution (incremental
strict decoder with `final=False` only when bytes exist beyond the sample, or
trim the sample to a code-point boundary) while keeping NUL/control-byte
heuristics and local/SFTP parity intact. Add the regression tests it specifies.

### 2.d — Clicking a tree directory opens it in Preview to browse *(Medium)*

Today a directory row in the Files tree only expands in place. Requirement:
clicking a directory should **also open that directory in the Preview tab** as a
navigable listing so the user can drill in from the main pane (the Preview tab
already hosts directory browsing from Stage 1's directory subsystem — this wires
the tree-click into it). Keep the tree-expand behaviour; add the Preview
navigation on top. **Decided (OD-3):** add a **lightweight breadcrumb** in the
Preview header for in-Preview navigation (up-one-level / jump to an ancestor);
this replaces the removed Back button from 2.a.

### 2.e — Preserve per-tab view mode + scroll across tab swaps *(Large — foundation)*

**Defect:** swapping between tabs drops the other tabs' state — a Markdown tab
reverts from Preview to Source, a diff tab loses its mode, and scroll position
resets to top. Root cause: view mode (`source` / `preview` / `diff`) and scroll
offset are effectively global/re-derived on activate rather than stored per tab.

**Work:** extend the per-pane tab records to carry, per tab, the active **view
mode** and the **scroll position** of the viewer body (and of the Markdown
preview). `activateExplorerTab()` restores them on switch instead of
re-rendering from defaults; the mode/scroll are captured on the way out of a tab.
This is the shared foundation for 2.f (persist them) and 5.a (survive a close).

**Scroll fidelity (decided, OD-4):** store the scroll position as a **fraction**
of scroll height (not an absolute pixel offset), **clamp on restore**, and
**skip restore if the tab's content identity changed** (e.g. a tail-updated log
or a re-fetch). Apply the same rule everywhere scroll is captured/restored (2.e
swaps, 2.f persistence, 5.a close-rebuild) so behaviour is consistent.

### 2.f — Persist tab modes / scroll / appearance / active tab in sessions *(ISSUE-2026-033 + extension, Large)*

`todos.txt` asks that **saving a session also save fonts/layouts/view-modes/
scroll position and the current open tab**. Part of this is already filed as
**ISSUE-2026-033** (Markdown appearance — preset + font — not in the preset
schema / snapshot); implement that per its proposed solution. Then **extend the
same three persistence paths** (`buildWorkspaceTerminalEntry()` →
`web/saved_sessions.py` `_normalize_terminal_entries()` →
`web/runtime_state.py` `_SESSION_SNAPSHOT_FIELDS`, mirroring the existing
`explorer_open_tabs` / `explorer_active_tab` handling from ISSUE-2026-015) to also
carry the **per-tab view modes and scroll positions** introduced in 2.e.

Depends on 2.e (there is nothing to persist until per-tab mode/scroll exists).
**Persisted field set (decided, OD-5):** per-tab **view mode**, per-tab **scroll**
(as a fraction, per OD-4), **active tab** (already persisted), and **Markdown
appearance** (preset + font, ISSUE-2026-033). The `todos.txt` phrase
"fonts/layouts" maps to the **Markdown appearance font** — already covered by
ISSUE-2026-033 — and "layouts" is already covered by existing split-geometry
persistence; **no separate editor-zoom field is added.** *(Amended 2026-07-19
after user testing: per-tab editor zoom **is** persisted after all — see the
OD-5 amendment and the Wave 5 status note.)*

### 2.g — Tab drag-reorder, middle-click close, double-click preview→pin *(Medium)*

Three tab-strip interaction affordances on the model from 2.e:

- **Drag to reorder** pinned tabs with the cursor. **Decided (OD-6):** the
  permanent **Preview** tab is pinned to the **first slot** and cannot be dragged,
  nor can a pinned tab be dropped ahead of it — only pinned tabs reorder among
  themselves.
- **Middle-mouse-button click** closes a pinned tab (same guard as the `×`).
- **Double-clicking the Preview tab** opens its current file as a **pinned tab
  in the same view mode** (source/preview/diff), promoting the transient preview
  to a kept tab.

Persisted tab order (2.f) must follow the reordered sequence.

---

## Stage 3 — Markdown preview presentation

**Shared surface:** the `--md-preview-*` custom properties and `md-preset-*`
classes in `terminals.css`, `explorerMarkdownAppearance()` /
`setExplorerMarkdownAppearance()` and the appearance popover in `terminals.js`
(all from ISSUE-2026-030), plus the **Source** highlight path.

### 3.a — Add a VS Code-style "gray surface / white text" preset *(Small)*

Add a fourth reading-surface preset alongside `default` / `paper` /
`high-contrast` — a dark grayish background with near-white text, matching VS
Code's `.md` preview. Add it as a new `md-preset-*` class defined purely from a
single token block (same theme-independent-surface exception already granted to
`paper` / `high-contrast`), extend the allowlist in `explorerMarkdownAppearance()`
and the popover, and confirm it round-trips through 2.f/ISSUE-2026-033
persistence. **Decided (OD-7):** user-facing label **"Slate"**, key **`vscode`**.

### 3.b — Colour heading titles in Source view too *(Small)*

`todos.txt`: *"Source files markdowns need titles coloured."* In **Source**
view, Markdown heading lines (and ideally other structural tokens) currently
render flat. Apply the existing syntax-highlight token colours to Markdown
headings in the Source viewer so `#`/`##` titles are visually distinct.
**Decided (OD-8):** a **heading-only tokeniser** is acceptable — if the Source
highlighter has no Markdown grammar, add a minimal heading-only tokeniser rather
than pulling in a new dependency. Token-driven colours only.

---

## Stage 4 — Explorer large-file / log preview

### 4.a — Raise the preview cap from 1 MiB toward 10 MiB *(Small)*

`todos.txt` reports a 1.2 MB JSON-Lines file showing only *"the first 1.0 MB of
1.2 MB"* and asks why we cap at 1 MiB and whether we can go to 10 MB.
`EXPLORER_FILE_PREVIEW_MAX_BYTES` is a single constant (`web/explorer.py:42`); the
head/tail ranged-read machinery from ISSUE-2026-020 already scales to a larger
window. The mechanical change is a one-line bump.

**Shape (decided, OD-9):** the 1 MiB limit is a deliberate guard against
browser-render stalls and unbounded in-memory search on the client, so don't do a
naive 10× bump. **Bump `EXPLORER_FILE_PREVIEW_MAX_BYTES` to 10 MiB, but render as
plain text (no syntax highlighting) above a ~2 MiB threshold** to keep the viewer
responsive. Keep the tail policy for logs and the precise "Showing the last/first
N of M" messaging.

---

## Stage 5 — Terminal close & layout integrity

### 5.a — Closing a terminal resets the shown tab's mode + scroll *(Medium — pairs with 2.e)*

`todos.txt`: closing a terminal pane resets the **currently displayed Explorer
tab's source/preview/diff mode and scroll position**. ISSUE-2026-027 already
made sibling explorer/browser panes survive a close by snapshotting their state
into `pendingCloseClientState` and reapplying it after the rebuild
(`captureSurvivingPaneClientState()` → `restoreExplorerPaneFromClose()`); that
snapshot preserves the open tabs and active Preview file/view but **not** the
per-tab view mode or scroll offset.

Once 2.e stores mode + scroll on each tab record, extend
`captureSurvivingPaneClientState()` / `restoreExplorerPaneFromClose()` to carry
those fields through the close rebuild too. This is the same data 2.f persists to
disk — so 2.e is the shared prerequisite for both. (Cross-origin iframe scroll
for browser panes remains the documented ISSUE-2026-027 limitation; this item is
Explorer-only.)

---

## Stage 6 — Terminal input focus & targeting

### 6.a — Drop all highlights when broadcast focus leaves to dead space *(Small)*

`todos.txt`: in **Broadcast** mode, clicking off into dead space or into an
Explorer/browser pane should **drop the active highlight on all terminals**; only
clicking back onto a terminal should re-light the broadcast highlight across
panes. Today the highlight (ISSUE-2026-025, driven by real DOM focus via the
delegated `focusin`/`focusout` on `document`) can linger because broadcast mode
keeps a sticky focused terminal.

Work in `web/static/js/terminals.js`: when focus moves to a non-terminal target
(dead space, explorer/browser pane) while broadcast is active, run the existing
`clearActiveTerminalHighlight()` path so no terminal shows active; re-light on the
next `focusin` into a plain terminal. Keep the ISSUE-2026-026 behaviour that
enabling broadcast focuses a terminal for immediate typing.

**Re-light semantics (decided, OD-10):** on the next `focusin` into a terminal,
the highlight follows **broadcast state at that moment** — if broadcast is
**still on**, re-light **all participating panes**; if broadcast was **turned off**
while focus was in dead space, re-light only the **single** clicked terminal.

---

## Stage 7 — Settings & launcher configuration

**Shared surface:** `agent_registry.json` + `web/agents.py`
(`_agent_auto_mode_flag`, `_agent_options`, `_compose_agent_startup_command`); the
launcher agent row and App Settings modal in `web/static/js/launcher.js` /
`templates/index.html`; `_run_startup_sequence()` in `web/terminal_io.py`; the
`terminal` app-config branch (`_normalize_app_config_update`, `RuntimeConfig`).

### 7.a — Add Kimi CLI to the agent registry *(Small)*

Add a `kimi` entry to `agent_registry.json` (repo:
`https://github.com/MoonshotAI/kimi-code`, binary `kimi`), mirroring the existing
entries: `label`, `display_name` ("Kimi Code CLI"), per-environment install
options, `verify`, `post_install`, and an `auto_mode` block. **Decided (OD-11):**
use the **`--auto-approve`** flag (alias of `--yolo`, short `-y`; most
descriptive). Because `_agent_auto_mode_flag()` already validates the flag shape,
no code change is needed beyond the registry — confirm the exact install command /
package name across environments while adding the entry. This automatically gives
Kimi the same Auto mode toggle 7.b standardises.

### 7.b — Verify/fix agent auto-mode launch + descriptive text for all agents *(Medium)*

`todos.txt`: *"agent auto mode doesn't do anything — the terminal is still
started with simple claude/codex/copilot."* **Web-confirmed:
`claude --enable-auto-mode` is a real, current Claude Code flag** (Anthropic's Auto
Mode), so the registry value is correct and the problem is in the launch path,
not the flag. Investigate and fix:

1. **Trace the composition end to end** — confirm the per-terminal
   `agent_auto_mode` boolean actually reaches `_compose_agent_startup_command()`
   at launch (`_run_startup_sequence()`), i.e. that the toggle is persisted into
   the launched `TerminalSession` and not lost between draft → session create.
   The most likely gap is the toggle not being carried on the specific launch
   path the user exercises (direct launch vs. saved-session vs. restore).
   Determine whether the flag is (a) never appended, or (b) appended but the
   installed agent version rejects/ignores it. **OD-12 is not a user decision** —
   it is this trace's finding, resolved during implementation: (a) is a code-only
   wiring fix; (b) additionally needs a docs/version note.
2. **Descriptive toggle text for every agent** — `todos.txt` wants the
   `Auto mode / Launches as "<agent> <flag>"` helper (already shown for Claude)
   shown for **all** agents that register a flag. The registry already carries a
   per-agent `description`; surface it (and the composed `Launches as "…"` line)
   uniformly for codex (`--full-auto`), copilot (`--allow-all-tools`), kimi
   (`--auto-approve`, 7.a), and claude (`--enable-auto-mode`). Verify each of
   those flags is still current while here (part of the OD-12 trace).

Add/extend the ISSUE-2026-013 tests for whichever launch path was dropping the
flag.

### 7.c — "Apply to all active sessions" toggle + font preset dropdown *(Medium)*

`todos.txt`: terminal settings currently apply only to the current active
session; add a toggle to **apply to all active sessions**, and make the font
field a **dropdown of basic presets** where each option previews in its own font.

- **Font dropdown with previews:** replace the free-text Font Family input
  (ISSUE-2026-029) with a `<select>` of curated monospace presets, each
  `<option>` styled in its own family so the list previews the font. **Decided
  (OD-13):** keep an explicit **"Custom…"** option that reveals the existing
  free-text input, so no configurability is lost. The chosen value still flows
  through the existing `terminal.font_family` app-config branch and
  `applyAppConfigTerminalFont()`.
- **Per-session font + size, with apply-to-all toggle (decided, OD-14):** the
  desired default is **focused-session-only** for **both font family and font
  size** (today font size is already per focused session; font family currently
  broadcasts globally — align them). Add a **per-session override layer** for both
  `font_family` and `font_size`, distinct from the global `RuntimeConfig` value,
  applied to the focused session by default. The **"Apply to all active
  sessions"** toggle opts into pushing **both** font and size to every session
  (the existing `_broadcast_app_config_update()` / `notifyAppConfigUpdated()`
  broadcast becomes the toggle-on path). Note this is the larger of the two
  interpretations — it adds a per-session override layer, not just a checkbox.

---

## Suggested sequence

Work is grouped into waves that escalate from trivial and isolated to the large,
interdependent tab-state refactor. Waves 1–4 are largely independent and can be
parallelised across branches; Wave 5 is a single ordered thread and should be
done by one person. **All Open decisions (OD-1…OD-14) are now resolved** (see
[Resolved decisions](#resolved-decisions)), so nothing below is gated on further
confirmation — OD-12 alone is settled by tracing during 7.b, not by the user.

**Wave 1 — Trivial polish (isolated CSS/markup, no backend):**
`1.d` open-in-tab button theming · `2.a` remove Preview Back button (OD-3) · `3.a`
"Slate" VS Code Markdown preset (OD-7) · `3.b` Source heading colours (OD-8).

> **Status: DONE (2026-07-18).** All four Wave 1 items are implemented, with
> `python tests/run_tests.py` (553 tests) and `python -m ruff check .` passing.
> - **1.d:** `.explorer-open-tab-btn` got its own token-driven rule in
>   `web/static/css/terminals.css` (surface/border/hover from the theme-aware
>   `--explorer-open-folder-*` tokens, matching its open-folder sibling).
> - **2.a:** the Back button, its click handlers, and the `.explorer-editor-back`
>   CSS rule were removed from both file-header render paths
>   (`renderExplorerFile`, `renderExplorerCommitDiffFile`) in
>   `web/static/js/terminals.js`.
> - **3.a:** `vscode` ("Slate") added to `EXPLORER_MD_PRESETS` /
>   `EXPLORER_MD_PRESET_LABELS`; fixed-surface `--md-preset-vscode-*` tokens
>   (VS Code-style `#1e1e1e` gray / `#d4d4d4` ink) and the
>   `.explorer-markdown-preview.md-preset-vscode` remap rule added to
>   `terminals.css`. Round-trips through the existing localStorage persistence.
> - **3.b:** `renderExplorerSourceLines()` wraps fence-aware Markdown heading
>   lines (levels from the existing `explorerMarkdownHeadingLevels()` map) in
>   `explorer-md-source-heading[-N]` spans; colour comes from `--t-accent`
>   (no new dependency, no palette literals).
> - **Tests:** `tests/test_api.py` — new
>   `test_terminals_page_markdown_slate_preset`,
>   `test_terminals_page_explorer_open_tab_button_is_theme_token_driven`,
>   `test_terminals_page_explorer_preview_back_button_removed`,
>   `test_terminals_page_markdown_source_headings_are_coloured`; the existing
>   `test_terminals_page_markdown_appearance_presets` allowlist assertion was
>   extended for the new preset. CHANGELOG updated.

**Wave 2 — Small isolated fixes (one classifier / route / constant each):**
`2.c` UTF-8 boundary (ISSUE-035) · `2.b` `go.mod` backend eligibility + classifier
(OD-2) · `4.a` preview cap → 10 MiB, plain above ~2 MiB (OD-9) · `7.a` add Kimi to
registry (OD-11).

> **Status: DONE (2026-07-18).** All four Wave 2 items are implemented, with
> `python tests/run_tests.py` (563 tests) and `python -m ruff check .` passing.
> - **2.c (ISSUE-2026-035):** the backend fix (incremental strict UTF-8 decoder
>   in `_explorer_content_looks_binary()`, `web/explorer.py`, deferring a
>   trailing partial sequence only when bytes exist beyond the 4 KiB sample) had
>   already landed; this wave added the issue's regression tests —
>   `test_explorer_binary_detection_allows_multibyte_char_crossing_sample_boundary`,
>   `test_explorer_binary_detection_rejects_invalid_utf8_inside_oversized_sample`,
>   and local + SFTP endpoint parity
>   (`test_explorer_file_accepts_utf8_split_across_sample_boundary[_remote]`).
> - **2.b (OD-2):** `go.mod`/`go.work` → `go`, `go.sum`/`go.work.sum` → `text`
>   added to `CODE_PREVIEW_FILENAMES` (`web/explorer.py`) and mirrored in
>   `EXPLORER_LANGUAGE_BY_FILENAME` (`terminals.js`). Tests:
>   `test_explorer_go_workflow_files_are_editor_eligible`,
>   `test_explorer_file_serves_go_mod` (no more 400), and
>   `test_terminals_page_go_filenames_frontend_classifier`.
> - **4.a (OD-9):** `EXPLORER_FILE_PREVIEW_MAX_BYTES` bumped to 10 MiB; the
>   client renders previews above ~2 MiB as plain text via
>   `EXPLORER_PLAIN_PREVIEW_THRESHOLD` + `pane._explorerFilePlain`, consulted by
>   `renderExplorerSource()` and all three `highlightExplorerPreviewCode()`
>   call sites (tail policy and "Showing the last/first N of M" messaging
>   unchanged). Tests: `test_explorer_preview_cap_is_10_mib` (a ~1.7 MiB
>   JSON-Lines file is served whole) and
>   `test_terminals_page_plain_preview_threshold`.
> - **7.a (OD-11):** `kimi` (Kimi Code CLI, binary `kimi`, auto-mode flag
>   `--auto-approve`) added to `agent_registry.json` with the official install
>   paths confirmed against the Kimi docs — install script
>   (`code.kimi.com/install.ps1` / `install.sh`) plus `uv tool install
>   --python 3.13 kimi-cli` fallback; SSH targets detect-only. No code change
>   needed (`_agent_auto_mode_flag()` validates the flag shape). Tests:
>   `test_agent_registry_includes_kimi_entry` and the extended
>   `test_agent_options_expose_registry_auto_mode_flags`.
> - CHANGELOG updated.

**Wave 3 — Git sidebar cluster (one shared renderer + routes):**
`1.d` is already done in Wave 1; do `1.b` Stage All (ISSUE-032) → `1.c` Discard
All (OD-1: tracked worktree only, no `git clean`) → `1.a` shared post-action
tree/diff refresh (ISSUE-034) last so it covers all three action types at once.

> **Status: DONE (2026-07-19).** All three Wave 3 items are implemented, with
> `python tests/run_tests.py` (577 tests) and `python -m ruff check .` passing.
> - **1.b (ISSUE-2026-032):** compact **Stage All** button in the **Changes**
>   header (`data-explorer-git-stage-all`, disabled when the unstaged list is
>   empty or an action is busy) → `explorerGitStageAll(index)` → new `POST
>   /api/explorer/<id>/git/stage-all` backed by `_git_stage_all_paths()`
>   (`git add --all` at the repo root, `write=True` so `GIT_TERMINAL_PROMPT=0`).
> - **1.c (OD-1):** **Discard All** button beside it
>   (`data-explorer-git-discard-all`, disabled unless the unstaged list has a
>   revert-eligible row) → in-page `openGenericConfirmModal` confirm → new
>   `POST /api/explorer/<id>/git/discard-all` backed by
>   `_git_discard_all_paths()`: enumerates `git status --porcelain -z`
>   (NUL-terminated, so unusual filenames survive; rename records consume their
>   original-path token), keeps only tracked non-conflicted worktree changes
>   via `_git_discardable_worktree_paths()`, then runs one
>   `git restore --worktree -- <paths…>`. Staged content preserved, untracked
>   files untouched, never `git clean`; an all-clean/untracked-only worktree is
>   a clear 400 ("No unstaged changes to discard").
> - **1.a (ISSUE-2026-034):** `performExplorerGitAction()` now routes every
>   worktree-mutating endpoint (`stage`, `unstage`, `revert`, `commit`,
>   `stage-all`, `discard-all` — publish excluded) through a shared
>   `refreshExplorerAfterGitAction()`: reloads the Files tree (guarded
>   internally on `pane._explorerTreeSidebarOpen`), invalidates the diff cache,
>   and re-fetches the open file in place with scroll preserved (bulk actions
>   refresh whatever file is open; single-path actions only their own path).
>   The revert flow's bespoke reopen was folded into the shared refresh.
> - **Guardrail fix while in the cluster:** `explorerGitPublish()` used
>   `window.confirm`, which WebView2 blocks (Regression Guardrail 4) — it now
>   confirms through the in-page shell; `terminals.js` is asserted free of
>   `window.confirm(` calls.
> - **Tests:** `tests/test_api.py` — `test_explorer_git_stage_all_stages_every_change`,
>   `test_explorer_git_stage_all_requires_a_repository`,
>   `test_explorer_git_discard_all_restores_tracked_worktree_changes`,
>   `test_explorer_git_discard_all_preserves_staged_content`,
>   `test_explorer_git_discard_all_rejects_when_nothing_unstaged`,
>   `test_git_discardable_worktree_paths_filters_porcelain_records`,
>   `test_terminals_page_git_bulk_action_controls_are_present`, and
>   `test_terminals_page_git_actions_refresh_tree_and_open_file`. CHANGELOG
>   updated; ISSUE-2026-032 and ISSUE-2026-034 closed in `testing_issues.md`.

**Wave 4 — Terminal-side settings & focus (independent of Explorer):**
`6.a` broadcast highlight drop (OD-10) · `7.b` auto-mode wiring + descriptive text
(OD-12 resolved by tracing) · `7.c` font/size presets + apply-to-all (OD-13/OD-14:
per-session by default).

> **Status: DONE (7.b 2026-07-18; 6.a and 7.c 2026-07-19).** `python
> tests/run_tests.py` (577 tests) and `python -m ruff check .` pass.
> - **6.a (OD-10):** the all-panes broadcast ring was pure CSS on
>   `#terminalsGrid.broadcast-input`, so it lingered regardless of focus. The
>   rule now also requires a `terminal-focus` class that
>   `setFocusedTerminal()` adds and `clearActiveTerminalHighlight()` removes
>   (the existing delegated `focusin`/`focusout` pair drives both), so focus
>   leaving to dead space or an explorer/browser pane drops every highlight.
>   Re-light follows broadcast state at the moment of re-focus exactly per
>   OD-10: the CSS rule needs both classes, so if broadcast was switched off
>   while focus sat in dead space only the clicked pane lights up. The
>   ISSUE-2026-026 enable-focuses-a-terminal behaviour is untouched (and its
>   focusin re-adds the class). Test:
>   `test_broadcast_highlight_drops_when_focus_leaves_terminals`.
> - **7.c (OD-13):** the App Settings Font Family free-text input became
>   `#appTerminalFontPreset` — a `<select>` of seven curated monospace stacks,
>   each `<option>` styled in its own family — plus a **Custom…** option that
>   reveals the original `#appTerminalFontFamily` input
>   (`collectTerminalFontFamily()` resolves preset vs. custom; a saved stack
>   that matches no preset re-opens as Custom).
> - **7.c (OD-14):** new **Apply to all active sessions** checkbox
>   (`#appTerminalApplyAll`, reset on every form sync — it is a one-shot
>   modifier, not a setting). Saving posts `terminal.apply_scope`
>   (`session`/`all`); `set_app_config` strips it before persisting (nothing
>   lands in `config.json`) and threads it through
>   `_broadcast_app_config_update()` / `notifyAppConfigUpdated()`. Scope
>   semantics (revised 2026-07-19 after user feedback — "session" is the
>   session *group*, not a focused pane, which is empty anyway once the
>   settings modal takes focus): `applyAppConfigTerminalFont()` restyles
>   **every terminal pane of the active session** by default, recording the
>   value in a group-id-keyed `groupFontOverrides` map (re-applied by
>   `attachTerminal()` so the session keeps its font across pane rebuilds and
>   splits); `apply_scope: 'all'` pushes font + size to every session —
>   including the hidden groups whose live xterms sit in `cachedGroupViews` —
>   and clears the overrides. Only the all-sessions path advances the body
>   dataset (the default new panes read), so a session-scoped change cannot
>   leak into other groups via a rebuild; new sessions/windows always launch
>   with the saved global font from `config.json`. Tests:
>   `test_app_config_broadcast_carries_apply_scope_without_persisting_it`,
>   `test_app_config_broadcast_defaults_to_session_scope`,
>   `test_app_settings_modal_offers_font_presets_with_custom_escape`,
>   `test_terminals_page_applies_scoped_font_updates`.
> - **7.b trace finding (OD-12 → case a, never appended):** the direct-launch
>   path dropped the toggle — `launchSessions()` in `web/static/js/launcher.js`
>   rebuilt each session payload field-by-field without `agent_auto_mode`, so
>   `TerminalSession.agent_auto_mode` was always `False` and
>   `_compose_agent_startup_command()` returned the bare agent key. Fixed by
>   carrying the toggle in the posted payload; the draft/saved-session/restore
>   paths were already intact (`_SESSION_SNAPSHOT_FIELDS`,
>   `_normalize_terminal_entries()`, `buildWorkspaceTerminalEntry()`).
> - **Descriptive text for all flag agents:** `_agent_options()` now also
>   exposes `auto_mode_description` from the registry, and
>   `syncTerminalAgentAutoModeState()` renders `Launches as "<agent> <flag>".
>   <description>` uniformly (claude, codex, copilot, kimi, kilo).
> - **Flag re-verification (OD-12 trace):** kilo gained its entry (`--yolo`,
>   auto-approves tool permissions). **opencode has no skip-permissions CLI
>   flag upstream** (permissions are `opencode.json`-driven), so no Auto mode
>   toggle is registered for it — recorded here as the docs/version note case.
> - **Tests:** extended `test_launcher_wires_the_auto_mode_toggle`
>   (`launchSessions()` payload slice + description wiring) and
>   `test_agent_options_expose_registry_auto_mode_flags` (kilo), plus new
>   `test_agent_options_expose_registry_auto_mode_descriptions`. CHANGELOG
>   updated.

**Wave 5 — Tabbed-viewer state model (the magnet — ordered, do last):**
`2.e` per-tab mode + scroll (foundation; OD-4 fraction/clamp/skip) → then in
parallel `5.a` survive a close, `2.f` persist to sessions (incl. ISSUE-033;
OD-5 field set), and `2.g` tab reorder/middle-click/double-click (OD-6: Preview
fixed first) → `2.d` directory-in-Preview browsing with breadcrumb (OD-3, rides
the same viewer, can slot in once 2.e lands).

> **Status: 2.e, 5.a, and 2.f DONE (2026-07-19); 2.g and 2.d DONE (2026-07-20)
> — Wave 5 and the whole plan are complete.** `python tests/run_tests.py`
> (599 tests) and `python -m ruff check .` pass.
> - **Tab records carry the snapshot:** each `pane._explorerTabs` entry can now
>   hold a `view` object — `{ mode, identity, scroll }` — instead of mode and
>   scroll living in pane-global fields that were re-derived on every activate.
> - **Capture sites:** `explorerCaptureActiveTabView()` runs in
>   `activateExplorerTab()` before the active id flips, at the top of
>   `openExplorerFile()` (tree clicks / Markdown links swap the shown tab
>   implicitly, and the loading placeholder would otherwise gut the DOM before
>   the fetch returns), and in `renderExplorerCommitDiffFile()`. Path guards
>   make the snapshot land only on the tab whose content is actually rendered,
>   so closing a tab or activating a same-path tab never mis-stores state.
> - **Restore sites:** `renderExplorerFile()` derives `initialFileView` from
>   the stored mode (`preview` / `diff`, with graceful fallback to `source`
>   when the file no longer offers that view) and passes the stored scroll —
>   fractions of scroll height, clamped to live metrics — to
>   `restoreExplorerFileScroll()`; an explicit in-place-refresh `scrollState`
>   still wins. The diff panel loads asynchronously, so its scroll is stashed
>   as `pane._explorerPendingDiffScroll` and re-applied by
>   `applyExplorerPendingDiffScroll()` on both the cached and freshly-fetched
>   diff paths. The Preview tab's directory listing gets the same
>   capture/restore in `renderExplorerActiveTab()`, and
>   `restoreExplorerFileScroll()` no longer calls `setExplorerFileView()` when
>   the DOM has no file panels.
> - **OD-4 identity check:** scroll/mode restore is skipped when the content
>   identity no longer matches — `explorerFileContentIdentity()` (djb2 over
>   path + content + diff commit/mode) and
>   `explorerDirectoryContentIdentity()` (path + entry count) — so a
>   tail-updated log or a re-fetch with new bytes opens at the default view
>   rather than a stale offset. A commit-diff tab whose commit context cannot
>   be re-fetched through the normal open path mismatches identity and falls
>   back to defaults instead of restoring the wrong mode.
> - **Tests:** `tests/test_api.py` —
>   `test_terminals_page_explorer_tabs_preserve_view_mode_and_scroll` and
>   `test_terminals_page_explorer_diff_scroll_restored_after_async_load`.
>   CHANGELOG updated. `5.a` and `2.f` can now both read the same
>   `tab.view = { mode, identity, scroll }` record instead of inventing their
>   own state.
> - **Manual-verify refinements (2026-07-19, second pass):**
>   - **Preview tab isolation:** `explorerAssignOpenTab()` no longer reuses the
>     active pinned tab for plain clicks — every Files-tree / Git-sidebar click
>     loads into the permanent Preview tab, so a pinned tab is never hijacked
>     (previously, clicking a pinned-open file under Changes/Staged Changes
>     repurposed that tab into diff mode and clobbered its saved mode + scroll).
>     The same file may now legitimately be open in the Preview tab and a
>     pinned tab at once. Same-tab re-renders (git-action refresh,
>     `refreshExplorerPane()`) pass `tab: pane._explorerActiveTabId` explicitly
>     so they are not rerouted into the Preview tab.
>   - **Per-tab editor zoom:** the font size moved from pane-global
>     `pane._explorerEditorFontSize` to `tab.fontSize` (zoom controls update
>     the active tab; every render applies the shown tab's size, including the
>     commit-diff path).
>   - **Preview-tab mode preference across files:** explicit Source/Preview
>     switches record a sticky `tab.preferredMode`; the Preview tab carries it
>     across *different* files (files without a Markdown preview fall back to
>     source; scroll still resets on a new file; diff stays an explicit
>     per-view action). The Markdown appearance (preset + font) was already
>     global via localStorage and applies on every render.
>   - **Tests:** added
>     `test_terminals_page_explorer_preview_tab_isolated_from_pinned_tabs` and
>     `test_terminals_page_explorer_zoom_and_mode_are_per_tab`; updated
>     `test_terminals_page_manual_refresh_keeps_open_explorer_file` for the
>     explicit-tab refresh call (581 tests pass).
> - **Rendered-tab guard (2026-07-19, third pass):** Preview isolation made
>   the capture's path-match guard ambiguous — with the same file open in the
>   Preview tab *and* a pinned tab, activating the pinned tab re-captured the
>   Preview tab's DOM state (diff mode, wrong scroll) onto the pinned tab
>   because the paths matched. `pane._explorerRenderedTabId` now records which
>   tab the viewer DOM actually belongs to: it is stamped by every render
>   entry point (`renderExplorerFile()`, `renderExplorerCommitDiffFile()`,
>   directory/empty/error renders) and cleared by the loading placeholder, and
>   `explorerCaptureActiveTabView()` stores a snapshot only when
>   `_explorerRenderedTabId === _explorerActiveTabId`. Clicking a pinned-open
>   file under Changes now opens the diff in the Preview tab while the pinned
>   tab's mode + scroll survive the round trip. Test:
>   `test_terminals_page_explorer_capture_tracks_rendered_tab` (582 tests
>   pass).
> - **5.a (2026-07-19):** the terminal-close snapshot now carries the per-tab
>   state 2.e introduced. `captureSurvivingPaneClientState()` first runs
>   `explorerCaptureActiveTabView(index)` (folding the shown tab's live mode +
>   scroll into its record while the DOM is intact), then adds
>   `explorer_tab_state` — every tab's full-fidelity record (`view` with its
>   complete scroll metrics + identity, `fontSize`, `preferredMode`) — plus
>   the disk-shape `explorer_tab_views` overlaid onto the rebuilt session
>   entries in `initialLoad()`. `restoreExplorerPaneFromClose()` reattaches
>   the records onto the rebuilt `pane._explorerTabs` synchronously after
>   `syncExplorerPane()` — before the active tab's async re-fetch resolves —
>   so `renderExplorerFile()` restores mode/scroll/zoom through the normal
>   OD-4 identity check. Test:
>   `test_terminals_page_close_preserves_tab_view_state`.
> - **2.f (2026-07-19, closes ISSUE-2026-033):** three new persisted terminal
>   entry fields, carried through all three persistence paths
>   (`buildWorkspaceTerminalEntry()` → `_normalize_terminal_entries()` →
>   `_SESSION_SNAPSHOT_FIELDS`, mirroring `explorer_open_tabs`):
>   - `explorer_tab_views` — per-tab `{ mode, scroll, identity }` keyed by
>     normalized tab path (OD-5 field set): the view mode, the active panel's
>     scroll as a clamped [0, 1] fraction (OD-4), and the djb2
>     content-identity hash, reduced from the live `tab.view` records by
>     `explorerPersistableTabView()` (the shown tab is captured live first).
>     On restore, `restoreExplorerPersistedTabs()` inflates them back into
>     `tab.view` snapshots (`explorerInflatePersistedTabView()`), so render
>     applies them only while the re-fetched content's identity still matches
>     — a persisted diff view whose commit context cannot be re-fetched
>     mismatches and falls back to defaults, exactly like the in-memory case.
>   - `explorer_md_preset` / `explorer_md_font` — the Markdown appearance
>     (ISSUE-2026-033), read from `explorerMarkdownAppearance()` at save time
>     and re-applied on session restore via
>     `applyExplorerSessionMarkdownAppearance()` →
>     `setExplorerMarkdownAppearance()` (which also syncs the shared
>     localStorage keys). Applied **once per session id** so a close-driven
>     rebuild never clobbers an appearance changed since launch; sessions
>     without the fields keep the local preference (graceful degradation).
>   - Backend: `_normalize_explorer_tab_views()` /
>     `_normalize_explorer_md_choice()` in `web/saved_sessions.py`
>     (allowlists `source|preview|diff`, presets
>     `default|paper|contrast|vscode`, fonts `system|serif|mono`; unknown
>     modes, unlisted/escaping paths, non-dict records dropped; scroll
>     clamped; identity bounded at 64 chars), gated to explorer panes in
>     `_merge_workspace_session_config()`; new `TerminalSession` fields +
>     `to_dict()`/`create_sessions()`/`update_session_metadata()` wiring; the
>     launcher round-trips all three via row datasets
>     (`data-explorer-tab-views`, `data-explorer-md-preset/-font`) and the
>     launch payload, and `buildSavedSessionLaunchPayload()` on the terminals
>     page passes them through too.
>   - **Zoom amendment (2026-07-19, user feedback):** OD-5 originally excluded
>     an editor-zoom field, but per-tab zoom was reset by a relaunch, so the
>     persisted record gained an optional `font_size` (clamped to the client's
>     10–24 bounds on both sides, omitted when at the default 13). Zoom-only
>     records are valid (a zoomed tab whose view was never captured), and the
>     reserved `__preview__` key persists the Preview tab's zoom alone — its
>     path/content stay transient per ISSUE-2026-015.
>     `restoreExplorerPersistedTabs()` applies the Preview zoom even when no
>     pinned tabs were saved.
>   - **Tests:** `tests/test_api.py` —
>     `test_normalize_terminal_entries_validates_tab_views_and_md_appearance`,
>     `test_workspace_save_round_trips_tab_views_and_md_appearance`,
>     `test_terminals_page_persists_tab_views_and_markdown_appearance`,
>     `test_launcher_round_trips_explorer_tab_views_and_markdown_appearance`,
>     `test_runtime_state_snapshot_includes_tab_views_and_md_appearance`, and
>     extended defaults assertions; `tests/test_session_manager.py` —
>     `test_create_sessions_carries_explorer_tab_views_and_md_appearance`
>     (589 tests pass). CHANGELOG updated; ISSUE-2026-033 closed in
>     `testing_issues.md`.

> - **2.g (2026-07-20, OD-6):** three tab-strip affordances on the 2.e model,
>   all wired in `wireExplorerTabStripInteractions()` (called from
>   `renderExplorerTabStrip()`):
>   - **Drag to reorder:** pinned tabs render `draggable="true"` (the
>     permanent Preview tab does not); `reorderExplorerPinnedTab()` splices
>     the pane's tab array and clamps the insertion index behind the Preview
>     tab's slot, so only pinned tabs reorder and nothing lands ahead of
>     Preview. Drop-side feedback is a token-driven accent edge
>     (`drag-before` / `drag-after`) plus a dimmed `dragging` state. The
>     persisted tab order follows automatically because
>     `explorerSerializeTabs()` reads the array in order (2.f), and the 5.a
>     close snapshot rides the same serialization.
>   - **Middle-click close:** `auxclick` with `button === 1` closes a pinned
>     tab through the same `closeExplorerTab()` guard as the `×` (never the
>     Preview tab); `mousedown` suppresses the browser's middle-click
>     autoscroll on tabs.
>   - **Double-click Preview → pin:** `promoteExplorerPreviewTab()` folds the
>     live mode + scroll into the Preview record
>     (`explorerCaptureActiveTabView()`), creates the pinned tab via the
>     normal dedup/cap path (`explorerAssignOpenTab()`), copies the full
>     per-tab state across (`view` snapshot, `fontSize`, `preferredMode`),
>     and hands the already-rendered viewer DOM to the new tab
>     (`_explorerRenderedTabId`) — no re-fetch, so the promoted tab shows the
>     identical source/preview/diff view. A path that is already pinned just
>     activates that tab (its own state is never clobbered). To keep the
>     double-click's two leading single-clicks from racing the promotion with
>     redundant re-fetches, `activateExplorerTab()` is now a no-op when the
>     requested tab is already active *and* its DOM is current
>     (`_explorerRenderedTabId` matches).
> - **2.d (2026-07-20, OD-3):** directory browsing from the tree + breadcrumb:
>   - **Tree click navigates Preview:** `toggleExplorerTreeDirectory()` keeps
>     its expand/collapse toggle and additionally browses the clicked
>     directory in the Preview tab via `loadExplorerPane()` (both on expand
>     and collapse); clicking the directory the Preview tab already shows is
>     a pure toggle. `loadExplorerPane()` now runs
>     `explorerCaptureActiveTabView()` before the loading placeholder, so
>     tree-click navigation preserves the outgoing tab's mode + scroll
>     exactly like `openExplorerFile()` does (2.e parity).
>   - **Reveal no longer force-expands the target:**
>     `revealExplorerTreePath()` expands only *ancestors* so the target row
>     becomes visible — previously it expanded the target directory itself,
>     which would have made every collapse click self-revert once navigation
>     rides the click.
>   - **Breadcrumb (OD-3):** `renderExplorerPathBreadcrumb()` turns the
>     explorer bar's path label into a trail of clickable ancestor segments
>     (root included) that browse that directory in the Preview tab; the
>     shown directory/file leaf is inert. All four render paths route
>     through it (directory listing, file open, in-place refresh, commit
>     diff), giving the up-one-level / jump-to-ancestor navigation that
>     replaces the 2.a Back button. Styling is token-driven
>     (`--explorer-muted` / `--explorer-text` / `--t-accent`).
>   - **Tests:** `tests/test_api.py` —
>     `test_terminals_page_tree_directory_click_browses_in_preview`,
>     `test_terminals_page_explorer_breadcrumb_navigation`, and
>     `test_terminals_page_tab_strip_drag_middle_click_and_promote`
>     (599 tests pass). CHANGELOG updated.

Rationale: `2.e` is this plan's magnet exactly the way ISSUE-2026-014 was for the
original plan — `5.a` (Stage 5) and `2.f` (Stage 2 / ISSUE-033) both need the
per-tab mode+scroll data structure it introduces, so building it once up front
avoids reworking the close-restore and persistence paths twice.

**Coverage check — every item is scheduled exactly once:**

| Stage | Items | Wave(s) |
| --- | --- | --- |
| 1 · Git sidebar | 1.d, 1.b, 1.c, 1.a | 1 (1.d), 3 (1.b→1.c→1.a) |
| 2 · Tabbed viewer | 2.a, 2.b, 2.c, 2.d, 2.e, 2.f, 2.g | 1 (2.a), 2 (2.b, 2.c), 5 (2.e→2.f/2.g→2.d) |
| 3 · Markdown | 3.a, 3.b | 1 |
| 4 · Large-file preview | 4.a | 2 |
| 5 · Terminal close | 5.a | 5 |
| 6 · Focus/broadcast | 6.a | 4 |
| 7 · Settings | 7.a, 7.b, 7.c | 2 (7.a), 4 (7.b, 7.c) |

All 21 items (1.a–1.d, 2.a–2.g, 3.a–3.b, 4.a, 5.a, 6.a, 7.a–7.c) appear in a
wave; no item is unscheduled and none is scheduled twice.

---

## Resolved decisions

All fourteen decisions are confirmed — none blocks implementation. Each row
records the decision as built into the item sections above.

- **OD-1 (item 1.c — Discard All scope):** **No `git clean`.** Discard All does a
  worktree restore of **tracked files only**, leaving untracked files in place
  (matches single-file Revert; stays inside the read-only contract).

- **OD-2 (item 2.b — `go.mod` root cause):** **Backend eligibility check**, not the
  frontend classifier. `GET /api/explorer/<id>/file?path=go.mod` returns **400**
  and the pane shows *"Explorer file format is not supported for editor preview"*
  (raised by `_explorer_editor_language()` when `_explorer_code_language()` returns
  `None`). Fix in `web/explorer.py` — add `go.mod` / `go.sum` / `go.work` to
  `CODE_PREVIEW_FILENAMES` — then mirror the entries in the frontend classifier
  for highlighting parity.

- **OD-3 (items 2.a / 2.d — Preview navigation chrome):** **Add a lightweight
  breadcrumb, drop the old Back button.** The breadcrumb (2.d) provides
  up-one-level / ancestor navigation in the Preview header; the vestigial Back
  control (2.a) is removed outright.

- **OD-4 (items 2.e / 2.f / 5.a — scroll fidelity):** Store scroll as a **fraction**
  of scroll height, **clamp on restore**, and **skip restore if the tab's content
  identity changed** (tail-updated log / re-fetch). Same rule at every
  capture/restore site.

- **OD-5 (item 2.f — persisted field set):** Persist per-tab **view mode**, per-tab
  **scroll** (fraction), **active tab**, and **Markdown appearance** (preset +
  font). "Fonts/layouts" = the **Markdown appearance font** (already covered by
  ISSUE-2026-033); "layouts" = existing split-geometry persistence. ~~No separate
  editor-zoom field.~~ **Amended 2026-07-19 (user feedback after testing 2.f):**
  the per-tab **editor zoom** is persisted after all — an optional `font_size`
  on each tab's view record (clamped to the 10–24 client bounds, omitted at the
  default size), plus the reserved `__preview__` key carrying the Preview tab's
  zoom only (its content stays transient per ISSUE-2026-015).

- **OD-6 (item 2.g — reorder rules):** **Preview fixed first.** The permanent
  Preview tab is pinned to the first slot and cannot be dragged; only pinned tabs
  reorder among themselves and none may be dropped ahead of Preview.

- **OD-7 (item 3.a — preset name):** User-facing label **"Slate"**, key **`vscode`**.

- **OD-8 (item 3.b — Source highlighter):** **Heading-only tokeniser is fine.** If
  the Source highlighter has no Markdown grammar, add a minimal heading-only
  tokeniser rather than pulling in a new dependency.

- **OD-9 (item 4.a — preview cap strategy):** **Bump to 10 MiB, render plain (no
  syntax highlight) above ~2 MiB**, keep the log tail policy and "Showing the
  last/first N of M" messaging.

- **OD-10 (item 6.a — broadcast highlight semantics):** Re-light follows broadcast
  state at the moment of re-focus: **broadcast still on → all participating panes;
  broadcast turned off while in dead space → only the single clicked terminal.**

- **OD-11 (item 7.a — Kimi specifics):** Use **`--auto-approve`** (most
  descriptive; alias of `--yolo`). Confirm the install command / package name per
  environment while adding the registry entry.

- **OD-12 (item 7.b — auto-mode failure mode):** **Not a user decision** — settled
  by the trace in 7.b. If the flag is never appended → code-only wiring fix; if
  appended but ignored by the installed agent → also add a docs/version note.
  Re-confirm each agent's current flag while implementing.

- **OD-13 (item 7.c — custom font escape hatch):** **Presets + an explicit
  "Custom…" option** that reveals the existing free-text input, so no
  configurability is lost.

- **OD-14 (item 7.c — "apply to all" semantics):** **Per-session by default for
  both font family and font size** (font size is already per focused session;
  align font family to match). Add a per-session override layer for both; the
  **"Apply to all active sessions"** toggle opts into pushing **both** font and
  size to every session. This is the larger interpretation (new override layer,
  not just a checkbox).
