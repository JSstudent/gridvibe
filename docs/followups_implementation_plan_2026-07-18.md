# GridVibe Follow-up Implementation Plan

Last updated: 2026-07-18

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

| # | Stage | Item | Refs | Size |
| --- | --- | --- | --- | --- |
| 1.a | 1 · Git sidebar | Reload Files tree after commit / stage-all / discard-all | ISSUE-2026-034 | Small |
| 1.b | 1 · Git sidebar | "Stage All" button in the Changes header | ISSUE-2026-032 | Medium |
| 1.c | 1 · Git sidebar | "Discard All" button for unstaged changes | — (extends 018) | Medium |
| 1.d | 1 · Git sidebar | "Open in new tab" (`+`) button ignores light/dark theme | — | Trivial |
| 2.a | 2 · Tabbed viewer | Remove the vestigial Back button on the Preview tab | — | Trivial |
| 2.b | 2 · Tabbed viewer | `go.mod` (and peers) not displayed | — | Small |
| 2.c | 2 · Tabbed viewer | Valid UTF-8 rejected when sample splits a character | ISSUE-2026-035 | Small |
| 2.d | 2 · Tabbed viewer | Clicking a tree directory opens it in Preview to browse | — | Medium |
| 2.e | 2 · Tabbed viewer | Preserve per-tab view mode + scroll across tab swaps | — | Large |
| 2.f | 2 · Tabbed viewer | Persist tab modes / scroll / appearance / active tab in sessions | ISSUE-2026-033 | Large |
| 2.g | 2 · Tabbed viewer | Tab drag-reorder, middle-click close, double-click preview→pin | — | Medium |
| 3.a | 3 · Markdown | Add a VS Code-style "gray surface / white text" preset | — (extends 030) | Small |
| 3.b | 3 · Markdown | Colour heading titles in **Source** view too | — | Small |
| 4.a | 4 · Large-file preview | Raise preview cap from 1 MiB toward 10 MiB | — (extends 020) | Small |
| 5.a | 5 · Terminal close | Closing a terminal resets the shown tab's mode + scroll | — (pairs with 2.e) | Medium |
| 6.a | 6 · Focus/broadcast | Drop all highlights when broadcast focus leaves to dead space | — (extends 025/026) | Small |
| 7.a | 7 · Settings | Add Kimi CLI to the agent registry | — | Small |
| 7.b | 7 · Settings | Verify/fix agent auto-mode launch + descriptive text for all agents | — (extends 013) | Medium |
| 7.c | 7 · Settings | "Apply to all active sessions" toggle + font preset dropdown | — (extends 029) | Medium |

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
persistence; **no separate editor-zoom field is added.**

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

**Wave 3 — Git sidebar cluster (one shared renderer + routes):**
`1.d` is already done in Wave 1; do `1.b` Stage All (ISSUE-032) → `1.c` Discard
All (OD-1: tracked worktree only, no `git clean`) → `1.a` shared post-action
tree/diff refresh (ISSUE-034) last so it covers all three action types at once.

**Wave 4 — Terminal-side settings & focus (independent of Explorer):**
`6.a` broadcast highlight drop (OD-10) · `7.b` auto-mode wiring + descriptive text
(OD-12 resolved by tracing) · `7.c` font/size presets + apply-to-all (OD-13/OD-14:
per-session by default).

**Wave 5 — Tabbed-viewer state model (the magnet — ordered, do last):**
`2.e` per-tab mode + scroll (foundation; OD-4 fraction/clamp/skip) → then in
parallel `5.a` survive a close, `2.f` persist to sessions (incl. ISSUE-033;
OD-5 field set), and `2.g` tab reorder/middle-click/double-click (OD-6: Preview
fixed first) → `2.d` directory-in-Preview browsing with breadcrumb (OD-3, rides
the same viewer, can slot in once 2.e lands).

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
  ISSUE-2026-033); "layouts" = existing split-geometry persistence. **No separate
  editor-zoom field.**

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
