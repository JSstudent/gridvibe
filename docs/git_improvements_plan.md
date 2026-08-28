# GridVibe — (GIT) Improvements & Updates Plan

> **Status: working document, not a contract.**
> This file is planning scratch in the same sense as `docs/r&d/`: it records an
> intent at one point in time, not how the code behaves. **Never cite it** — not
> from code comments, not from docstrings, not from `CLAUDE.md` / `AGENTS.md` /
> `README.md`, not from tests. When a stage lands, the rule it established goes
> into the code it governs, into `CHANGELOG.md`, and (if it is a guardrail) into
> the Regression Guardrails lists in `CLAUDE.md` **and** `AGENTS.md`. Delete a
> stage from here once it has shipped and been documented there.

Source notes: `docs/r&d/todos.txt`, items **4, 8, 10, 11, 13, 18** (the `(GIT)`
marked ones). Written 2026-08-26 against `szua_gridvibe_wrk-new-feat` @ `4c5a027`.

---

## 0. What the code actually does today

Read before planning any stage — several of the notes describe behaviour that is
already half-built, and one describes a divergence between the code and
`CLAUDE.md`.

### 0.1 The Git scope (pin / follow)

`explorerGitScopePath(pane)` — `web/static/js/explorer-git-sidebar.js:25`:

```js
if (pane?._explorerGitFollowBrowsing) return String(pane._explorerPath || '');
return typeof pane?._explorerGitPinnedPath === 'string' ? pane._explorerGitPinnedPath : null;
```

- `null` → the request omits `scope`, and `_explorer_git_anchor_paths()`
  (`web/api.py:1651`) anchors on the explorer root.
- a string → `?scope=path&path=<root-relative>`, resolved by
  `backend.resolve_dir()`, i.e. **the scope must be a directory**.

`toggleExplorerGitPinnedScope()` (`explorer-git-sidebar.js:496`) stores
`pane._explorerPath` — **the browsed folder**, not the repository root.

The anchor then feeds two different consumers in `_get_git_repo_summary()` /
`_get_git_repo_state()` (`web/explorer.py:2894`, `:2871`):

| consumer | needs | today |
| --- | --- | --- |
| `_resolve_git_worktree_root()` (`rev-parse` with `cwd=`) | a **directory** | the anchor |
| `backend.pathspec(repo_root, anchor)` → `git status`, `git log --graph` | a path, file or dir | the anchor |

That table is the whole reason note **11** (file-level pin) is a backend change
and not a client one: only the first consumer needs a directory.

> **Superseded by stage 4 (2026-08-28).** The snippet and the table above
> describe the code *before* stage 4. `_explorer_git_anchor_paths()` now returns
> `(root_path, anchor_path, context_dir)` and the scope carries a validated
> `kind`; the two consumers are fed separately, which is what the table was
> predicting. Read `CLAUDE.md`'s Git-scope guardrail for the shipped contract.

> **Divergence — resolved 2026-08-27; the document was fixed, not the code.**
> The pin is a frozen **path** scope: it captures the *browsed folder*,
> root-relative, at the same granularity Follow has, just not live. `CLAUDE.md`
> and `AGENTS.md` used to call it "repository-level"; both now describe what the
> code does, and stage 2's contract tests pin that reading. Notes 8 and 11 both
> assume it (pin a subdirectory; pin a file), so nothing downstream re-opens it.

### 0.2 What is already persisted

The pin travels as a **pair** — `explorer_git_pin_active` (bool) +
`explorer_git_pinned_path` (string) — through five stores/paths (stage 4 made it
a **triple**, adding `explorer_git_pin_kind` along every one of them):

| where | what |
| --- | --- |
| `sessions/manager.py:99,100,168,169,878,879,1044,1045` | live session fields, `to_dict()`, defaults, mutable set |
| `web/session_presentation.py:76,77,106,113` | `PANE_PRESENTATION_FIELDS`, `_EXPLORER_BOOL_FIELDS`, `_EXPLORER_STRING_FIELDS`, `_normalize_explorer_tab_path` bound |
| `web/runtime_state.py:184,185` | `_SESSION_SNAPSHOT_FIELDS` (workspace snapshot) |
| `web/saved_sessions.py:285,286,494–500` | `_LIVE_SESSION_VIEW_FIELDS` + the `startup_mode == "explorer"` gate |
| `web/session_modes.py:374,375` | **cleared** on explorer→terminal, deliberately |

Client side: `terminals.js:2301` (live describe), `:2524` (Save Workspace launch
config), `:5039` / `:6208` (pane build on restore), `:6593` / `:7418`
(close-driven rebuild overlay); `session-persistence.js:250,251` (wire payload);
`launcher.js:113,115,734,736,1792,1793` (preset row dataset, gated on
`explorerTabsMatchRoot`).

Git commit expansion (`explorer_git_expanded`, note 4's second half) rides the
same chain: captured in `explorerSidebarPresentation()`
(`explorer-viewer.js:2076`, bounded to 128), normalized by
`_normalize_explorer_git_expanded()`, restored into
`pane._explorerDiffExpandedCommits`.

**So: the fields exist end-to-end.** Note 10's flakiness was therefore *not* a
missing field but an ordering/identity/resolution defect in that chain — found,
fixed and recorded as **ISSUE-2026-047** (stage 2, shipped 2026-08-27).

### 0.3 Existing patterns the stages must copy

- Alt+click level-fold exists for the Files tree, and Graph rows now mirror it
  with a collapse-only Alt+click backed by `collapseAllPlan()`.
- The Graph find is DOM-free in `explorer-git-search.js` with a paint-only
  adapter (`paintExplorerGitCommitSearch`), covering subject and hash modes.
- Git search/active/menu state **paints, never re-renders** (the commit textarea
  and search input would lose the caret). Structural commit expansion still
  renders its file rows; Alt-collapse restores the textarea focus and selection.
- `explorer-git-active.js` / `-menu.js` / `-search.js` / `-pin.js` are the "new
  surface gets a DOM-free module + a thin adapter" pattern (architecture
  guardrail 6). `-pin.js` is the newest, now holding the pin predicate, the pin
  button's three states and the repo bar's scope rows — the module stage 4
  extends to a file-level pin.
- `explorer-tree.js` is the completed Files-tree pure-move pattern; the lasting
  extraction rule and its next trigger now live in `CLAUDE.md` and `AGENTS.md`.
- The Files tree's pin marker (3b) is derived per row from
  `_explorerGitPinnedPath` and painted onto the two rows that changed — never by
  rebuilding `[data-explorer-tree-body]`, which resets the tree's scroll. A `''`
  pin marks the FILES head, because the body lists the root's *children* and has
  no root row. The Graph header's pin button (3c) is painted the same way, from
  the same predicate, through the same `refreshExplorerPinAffordances(index)`.

---

## Stage 3 — The pin is visible where it was made, and re-pinnable on the fly (note 8)

**Shipped 2026-08-27 — 3b and 3c both.** Nothing here is outstanding; the stage
is recorded rather than planned, and the rules it established live in the code,
in `CHANGELOG.md`, in `README.md`, and in the Git-scope guardrail in `CLAUDE.md`
/ `AGENTS.md`.

- **3b — the Files-tree pin marker.** Introduced `web/static/js/explorer-git-pin.js`
  and its `explorerGitPathIsPinned()` predicate.
- **3c — the pin button asks "is the pin *here*".** Three states from
  `explorerGitPinButtonState()` on that same predicate, a one-write re-pin from
  anywhere else, and the repo bar's always-reachable **Clear pin**.
- **3c follow-up, from the manual pass** — the repo bar's single scope row
  showed the *effective* scope, so with Follow on it named the browsed folder,
  wore the chain icon and still carried a **Clear pin** for a path that was not
  on the row. Split into one row per control via `explorerGitScopeLines()`, pin
  first, with the clear only ever on the row that names the path it clears.
- **3c follow-up 2, from the same pass** — that clear was then withheld while
  the pin was *here*, on the grounds that the pressed button already clears
  there. It made the one named control vanish at the only folder a reader is
  standing in when they decide to unpin, leaving a button whose pressed state
  had stopped meaning "a pin exists" everywhere else. `clearAvailable` now
  asks "is there a pin" and ignores the browsed path; `pressed` keeps asking
  "is the pin here".

What stage 4 inherits: `explorer-git-pin.js` is now the module both the tree
marker and the two Graph-header/repo-bar surfaces read, so a file-level pin
extends the predicate, the button state and the row model in one place.

---

## Stage 4 — Pin & Follow down to file level, and pinning from the tree (note 11)

**Shipped 2026-08-28.** Nothing here is outstanding. The rules it established
live in the code, in `CHANGELOG.md`, in `README.md`, in the Git-scope guardrail
in `CLAUDE.md` / `AGENTS.md`, and in `docs/session_state_guideline.md`; the two
defects the manual pass turned up are **ISSUE-2026-048** and **ISSUE-2026-049**
in `docs/testing_issues.md`.

- **The scope became a `(path, kind)` pair.** `_explorer_git_anchor_paths()`
  returns `(root_path, anchor_path, context_dir)` — the pathspec and the
  directory `rev-parse` runs in, separated so a file scope's `status` is not
  widened back to its parent. `?scope=path&kind=dir|file` is validated against a
  server-side allowlist; an absent `kind` means `dir`.
- **`explorer_git_pin_kind` rides every persistence route** beside the existing
  pair, normalized against the two-value allowlist and refused rather than
  coerced. The kind decision went the recommended way — an explicit field, not
  the trailing-slash encoding.
- **Follow reads the same pair live**, so the scope tracks the open file and
  widens back on a folder.
- **The right-click pin** landed through `explorerGitScopeMenuItem()` in the
  existing `explorer-git-pin.js`: single-entry, never on a commit row, and
  disabled-not-dropped outside a worktree.

Three things came out of the manual pass rather than the plan:

- **The listing needed the menu too.** The plan scoped the entry to the Files
  tree, but the Preview tab's directory listing is the *other* filesystem
  browsing surface and shows the same rows. Two surfaces disagreeing about what
  a row can do is exactly what `explorer-git-pin.js` exists to prevent, so both
  now carry `data-explorer-git-scope-{path,kind,surface}` and offer identical
  pin and Follow entries.
- **ISSUE-2026-048 — a restored Files tree drew open chevrons over empty
  branches.** Expansion is persisted and the listings behind it are not, and
  nothing re-read them outside the ancestors of the shown path. The general rule
  ("a persisted set of keys is not the data behind them") is now in
  `docs/session_state_guideline.md` under Restore.
- **ISSUE-2026-049 — Follow re-scoped on an interval when a file was opened.**
  Only the listing and the image viewer reported the scope change; the ordinary
  file render did not, so the change listener's poll was doing the work. All
  three now go through `explorerGitScopeNeedsLoad(pane)`, which also keeps an
  *unchanged* scope from re-rendering the panel and stealing the commit-message
  caret.

---

## Stage 5 — Branch graph in the Preview panel (note 13)

**Deferred 2026-08-28 — not scheduled.** Everything below is the plan as
written; nothing about it has been built, and it is kept here rather than
deleted because it is the only stage still outstanding. It has no dependency on
stage 4 beyond §5.4 step 9 (which reads better now that a file pin exists), so
it can be picked up whenever.

**Risk: high.** New backend endpoint, a substantial new frontend surface, and —
in its full form — a schema widening. Recommend shipping the **narrow variant**
first and treating the full one as a follow-up.

### 5.1 Problem

Note 13: *"a git graph button that displays the git branch graph in preview on
demand (persistance for preview applies)."* The sidebar's Graph is a **linear,
scope-narrowed** list (`git log --graph … -- <pathspec>`, `--max-count=60`), one
commit per row in a ~260 px panel, with the ASCII graph prefix squeezed into
`.explorer-diff-commit-graph`. It cannot show branch topology.

### 5.2 Solution — narrow variant (recommended first)

**Backend — new read-only endpoint `GET /api/explorer/<id>/git/graph`:**

- `git log --graph --all --decorate --date-order` with the existing
  `_GIT_LOG_GRAPH_FORMAT`, `--max-count=EXPLORER_GIT_BRANCH_GRAPH_MAX_COMMITS`
  (new module constant, propose **200**), and **no pathspec** — branch topology
  is a repository-wide question, and narrowing it by scope would draw a graph
  whose edges are missing.
- Anchored on the pane's selected scope for **repository discovery only**
  (`_git_action_anchor`-style: which repo, not which paths).
- `_require_complete_git_result()` before parsing; the module's
  `EXPLORER_GIT_MAX_OUTPUT_BYTES` / `EXPLORER_GIT_MAX_STDERR_BYTES` ceilings (a
  caller may tighten, never widen); `GIT_TERMINAL_PROMPT=0`; the bounded runner
  in `web/process_bounds.py`. Read-only — it stays inside the explorer's read
  contract and widens nothing.
- Reuse `_parse_git_graph_log()`; do **not** grow a second parser.

**Frontend — new file `web/static/js/explorer-git-graph.js`** (guardrail 6: a
substantial new surface gets its own file, never `explorer-viewer.js`):

- **DOM-free half:** parse the `--graph` prefix into lane/edge records and
  produce a row model. Node-tested against real `git log --graph --all` output
  captured from this repo.
- **Thin paint adapter:** renders into the existing directory/preview surface.
- **Bounded like every other big surface.** 200 commits × a lane column is
  enough rows to matter, so the build obeys the frame-budget rule: emit under a
  render token, end a frame on **~8 ms** and never on a row count, and go
  through `explorerReleasePaneWork()` on disposal. If it can exceed the Source
  view's row ceiling it needs a tier and a `role="status"` notice naming what is
  off — reuse `explorer-tiers.js`, do not invent a second ladder.
- **Persistence — one boolean pane field, `explorer_git_graph_open`.** Not a new
  `EXPLORER_TAB_VIEW_MODES` entry and not a new `EXPLORER_SCROLL_PANELS` entry:
  the graph is a pane-level view of the *repository*, not a view of a file, so
  it does not belong in a per-tab record. Scroll reuses the existing `directory`
  panel offset. This is the whole reason to prefer the narrow variant — it
  satisfies "persistance for preview applies" while touching one boolean instead
  of two schema tuples on both sides of the wire.
- One toggle button in the Graph section header beside pin / follow / search,
  stroke-`currentColor` SVG, `aria-pressed`.

**Tests:**

- `tests/test_api.py`: the endpoint's bounds, refusals, root confinement,
  `GIT_TERMINAL_PROMPT=0`, incomplete-result rejection, and that it stays a read
  (no mutation reachable from it).
- `tests/test_explorer_git_graph.py` (new, Node): the lane/edge parse over real
  captured `--graph --all` output, including a merge, an octopus merge, a
  detached HEAD, and a repository with a single commit.
- `tests/test_explorer_git_pin_persistence.py`: `explorer_git_graph_open` joins
  the parametrised round trip.
- Frame-budget / disposal coverage in the style of `test_explorer_repaint.py` if
  the build turns out to be sliced.

### 5.3 Full variant (follow-up only, if the narrow one proves insufficient)

Promote the graph to a real tab view mode: widen `EXPLORER_TAB_VIEW_MODES`
(`web/session_presentation.py:24`) and `VIEW_MODES`
(`explorer-persistence.js:9`), add a `graph` entry to `EXPLORER_SCROLL_PANELS`,
and handle the v1→v2 record migration. **Do not** start here: it changes the
persisted view-record schema for every explorer pane in every store, and the
narrow variant delivers the note's stated ask without that.

### 5.4 Manual test outline

1. Explorer pane on a repo with several branches and at least one merge. Toggle
   the graph button → the branch graph renders in the preview surface.
2. Confirm branches and merges are visible as topology, not as a flat list.
3. Toggle off → the previous preview content (directory listing or file) is
   back, at the scroll position it had.
4. Scroll the graph, switch to another session tab and back → the position is
   held.
5. Save Workspace → restart → restore → the graph is open (or closed) as you
   left it, at roughly the same offset.
6. Turn the graph on in a repo with 1,000+ commits → it renders within the
   bound, the window stays responsive while it draws, and if a tier engages the
   in-pane notice says what is off.
7. Turn it on in a folder with **no** repository → an explicit in-pane message,
   never a spinner that never ends.
8. Turn it on, then immediately switch the pane to terminal mode → no console
   errors, no orphaned request (the pane's work is released).
9. Turn it on with a **file** pin active (stage 4) → the graph still shows the
   whole repository's branches, and the sidebar Graph still shows the file's
   commits. Confirm the two are clearly distinguishable.
10. Repeat 1, 5, 6 on an **SSH** pane, on a repo large enough to make the round
    trip visible.
11. Both explorer themes; then `make check`.

### 5.5 Documentation after verification

- **`README.md`** — a real user-facing feature: describe the button, what the
  graph shows (whole repository, `--all`), and its commit bound.
- **`CLAUDE.md` + `AGENTS.md`** — `explorer-git-graph.js` and its test in the
  repo-layout trees; a "Key Concepts" note that the branch graph is
  **repository-wide by construction** and is deliberately *not* narrowed by the
  selected scope, so nobody "fixes" it later by adding a pathspec.
- **`CHANGELOG.md`** — one `(feat)` bullet naming the bound.
- **`docs/session_state_guideline.md`** — `explorer_git_graph_open` in the field
  inventory.

---

## Sequencing, and what each stage costs

| Stage | Notes | Backend | New persisted field | New endpoint | Files touched | Risk |
| --- | --- | --- | --- | --- | --- | --- |
| 3b | 8 | — | — | — | shipped 2026-08-27 | low |
| 3c | 8 | — | — | — | shipped 2026-08-27 | low |
| 4 | 11 | yes | `explorer_git_pin_kind` | — | shipped 2026-08-28 | med-high |
| 5 | 13 | yes | `explorer_git_graph_open` | `git/graph` | deferred | high |

**Hard dependencies:** all of them are now discharged. 3b, 3c and 4 needed 2 —
the pin had to be trustworthy before it was made visible or extended — and 2, 3b
and 3c shipped on 2026-08-27, 4 on 2026-08-28. Stage 5 is independent of 3 and 4
and can be scheduled whenever; the scope work it benefits from has landed.

**Decisions still open** (pin semantics was decided with stage 2: a frozen
*path* scope, and the document was corrected; stage 4's kind field was decided
with stage 4 — the explicit `explorer_git_pin_kind`, not the trailing slash):

1. **Stage 5 shape** — narrow (`explorer_git_graph_open`) first, or straight to
   the tab-view-mode variant. Recommendation: narrow.

**Every stage ends with `make check`** (`python tests/run_tests.py` and
`python -m ruff check .` on Windows without `make`) green **before** the manual
pass, and the documentation step happens **after** the manual pass, not before.
