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

> **Divergence to resolve (blocks stages 2–4).** `CLAUDE.md` states: *"The Graph
> header's pin button resolves the repository containing the currently browsed
> folder and captures **that repository's** explorer-root-relative path as a
> fixed Git scope; pin is therefore repository-level."* The code captures the
> **browsed folder**, so the pin is in fact a frozen *path* scope — the same
> granularity Follow has, just not live. Nothing rewrites it to the repo root
> (the server's `git.repo_path` identity is never read back by the client;
> `grep -rn "repo_path" web/static/js/` finds one unrelated hit).
> Notes 8 and 11 both assume the code's behaviour (pin a subdirectory; pin a
> file), so the recommendation is **fix the document, not the code** — but this
> is a decision, and it must be made before stage 2 writes a contract test that
> pins one of the two readings.

### 0.2 What is already persisted

The pin travels as a **pair** — `explorer_git_pin_active` (bool) +
`explorer_git_pinned_path` (string) — through five stores/paths:

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

**So: the fields exist end-to-end.** Note 10's flakiness is therefore *not* a
missing field — it is an ordering/identity/resolution defect somewhere in that
chain, which is what stage 2 is for.

### 0.3 Existing patterns the stages must copy

- Alt+click level-fold exists for the Files tree, and Graph rows now mirror it
  with a collapse-only Alt+click backed by `collapseAllPlan()`.
- The Graph find is DOM-free in `explorer-git-search.js` with a paint-only
  adapter (`paintExplorerGitCommitSearch`), covering subject and hash modes.
- Git search/active/menu state **paints, never re-renders** (the commit textarea
  and search input would lose the caret). Structural commit expansion still
  renders its file rows; Alt-collapse restores the textarea focus and selection.
- `explorer-git-active.js` / `-menu.js` / `-search.js` are the "new surface gets
  a DOM-free module + a thin adapter" pattern (architecture guardrail 6).

### 0.4 Cross-cutting prerequisite: the `explorer-tree.js` extraction gate

`CLAUDE.md` guardrail 6: *"the **next substantial change to the Files tree**
extracts `explorer-tree.js` to the same pure-move standard."*
`explorer-viewer.js` is **7,781 lines** today. Stage 3 changes the tree's row
markup and stage 4 adds tree context-menu entries — both are that change.

**Recommendation:** do the pure move as **Stage 3a**, before either. Moving
`explorerTreeRowHtml`, `renderExplorerTreeNodes`, `renderExplorerTreePanel`,
`toggleExplorerTreeDirectory`, `toggleExplorerTreeLevel`,
`openExplorerTreeDirectory`, `ensureExplorerTreeState`,
`explorerTreeRowIsActive`, `explorerTreeEntryForPath` and their siblings out
byte-identically, with `explorer-viewer.js` a pure deletion and
`tests/test_explorer_tree_fold.py` / `test_explorer_find.py` passing on their
existing assertions (only their source-of-truth path changes — still a pure
move). Doing it *after* stage 3 would mean the move is no longer pure.

---

## Stage 2 — Pin & expansion save/restore hardening (notes 10, 4b)

**Risk: low-medium.** No new UI, no new field, no new endpoint. Characterization
first, then a targeted fix for whatever the characterization catches. Stages 3
and 4 both depend on the pin being trustworthy, so this comes before them.

### 2.1 Problem

Note 10: *"pinned git directory not saved, git not present yellow text from top
directory on restore … seems to get saved sometimes … This seems flaky."*
Note 4b: *"lets check open git tree persistance/restoration."*

Section 0.2 shows the fields exist in every store. The symptom — the sidebar
falling back to the explorer root and reporting no worktree — is what you see
when `explorerGitScopePath()` answers `null`, i.e. `_explorerGitPinnedPath` is
not a string on the restored pane. There are five plausible causes, and they
need to be separated by test rather than by guess:

- **H1 — the pin names a folder that is not a repository.** Per 0.1 the pin is
  the *browsed folder*. Pin a folder outside any worktree (note 8's own words:
  *"pinned a directory from a directory without a git tree"*) and the restored
  pane faithfully re-applies it; `_get_git_context()` then answers
  *"Folder is not inside a Git worktree"* and the sidebar paints the error.
  **This reproduces the exact reported symptom and is not a persistence bug at
  all** — it is the 0.1 divergence. Highest-probability cause.
- **H2 — `loadedForPath` compares a raw scope against a resolved anchor.**
  `loadExplorerGitRepo()` (`explorer-git-sidebar.js:912`) sets
  `requestedAnchorPath = scopePath ?? ''` but stores
  `pane._explorerGitAnchorPath = data.anchor_path` — the value
  `backend.rel_explorer_path()` produced. A pin whose stored spelling differs
  from its resolved form (trailing slash, a `.` segment, a path that resolves to
  the root) makes those two disagree permanently: the early return never fires,
  and the `finally` block's re-load condition can re-enter, or the panel settles
  on a scope that is not the one the pin names.
- **H3 — a root pin is indistinguishable from no pin.** Pinning while browsing
  the explorer root stores `''`; `explorerGitScopePath` then returns `''`, which
  the request encodes as `scope=path&path=` — resolving to the same root an
  unpinned pane uses. The pin round-trips as *state* but has no *effect*, so it
  reads as "the pin was lost".
- **H4 — the launcher drops the pin when the row's directory is edited.**
  `explorerTabsMatchRoot` (`launcher.js:696`) gates
  `explorer_git_pin_active` / `_pinned_path` on the row's directory input being
  untouched. Correct in intent (the pin is root-relative), invisible in
  practice — the user gets no notice.
- **H5 — the presentation write is routed by visible group.**
  `notePanePresentationChanged(index)` (`terminals.js:2404`) ignores `index` and
  notes `visibleGroupId || activeGroupId`. Believed safe (the pin button is only
  reachable on-screen), but it is exactly the slot-vs-identity assumption
  guardrail 4 exists for, and it is one assertion to pin down.

### 2.2 Solution (code + tests)

**Tests first — these are the deliverable even if no code changes.**

- **`tests/test_explorer_git_pin_persistence.py` (new, Python).** One
  parametrised round-trip of a pinned explorer pane through **all four** durable
  routes, asserting the `(pin_active, pinned_path)` pair *and*
  `explorer_git_expanded` survive each intact:
  1. live presentation sync — `POST /api/session-presentation` →
     `session.to_dict()`;
  2. workspace snapshot — `_snapshot_session()` → `runtime_state.json` →
     restore → `_normalize_session_config()`;
  3. saved preset — Save Workspace → `saved_sessions.json` → relaunch;
  4. close-driven rebuild — the `pendingCloseClientState` overlay fields
     (`terminals.js:7418`) as a shape contract on the Python side.

  Plus the negative cases that must stay true: explorer→terminal **clears** the
  pin (`web/session_modes.py:374`); a wrong-typed `explorer_git_pin_active`
  fails the **whole group** rather than dropping the pane (the "launchable shape
  fails" rule); a pin path with `..` or `:` normalizes to `""`; the 128-key
  ceiling on `explorer_git_expanded` holds.
- **`tests/test_session_presentation.py` (extend, Node half).** The client's own
  round trip, executed rather than asserted: `describePanePresentation()` →
  `buildPanePresentation()` → the pane literal built by `createPaneInstance()`
  yields the same `explorerGitScopePath()` it started with — including the `''`
  (root pin) case, which is the one H3 makes ambiguous.
- **`tests/test_explorer_git_identity.py` (extend, Node vm).** `loadedForPath`
  (H2): a pin whose stored spelling resolves to a different string does **not**
  re-enter the load loop, and a second `loadExplorerGitRepo()` for an unchanged
  scope issues **no** request.

**Code — only what the tests above turn red:**

- **H1 / 0.1 — resolve the divergence.** Recommended: keep the code (pin = a
  frozen path scope, which is what notes 8 and 11 assume) and **correct
  `CLAUDE.md` + `AGENTS.md`**. Then make the failure legible instead of
  mysterious: when a **restored** pin resolves to a folder outside any worktree,
  the sidebar's existing error branch (`explorer-git-sidebar.js:521`) gains one
  line naming the pinned path and a **Clear pin** action beside it. In-pane
  error/`role="status"`, never `showGridVibeNotice()` — this is a state that
  lasts as long as the pin does, not an event.
  *If instead you want pin to mean repository-level, that is a behaviour change
  and belongs in its own stage — say so before stage 2 starts.*
- **H2** — store the **requested** scope as the load identity and keep
  `data.anchor_path` as the *server's* answer under its own key
  (`_explorerGitResolvedAnchor`), so the early return compares like with like.
  Three-line change, one new assertion.
- **H3** — make "pinned at the root" a first-class, visible state rather than a
  silent no-op: `aria-pressed="true"` already distinguishes it in the DOM; the
  Graph header's repo bar gains the scoped path (`repo · branch · <scope>`, and
  `· root` when the pin is `''`). Stage 3's tree marker then makes it visible in
  the tree too. **No schema change.**
- **H4** — when the launcher drops a pin because the row's directory was edited,
  report it once through `showGridVibeNotice(…, 'info')` — that *is* an event,
  and the launcher's one banner is the right surface for it.
- **H5** — if the Node test shows the pin can be described for the wrong group,
  route by the pane's own group id; otherwise leave it and keep the assertion.

### 2.3 Manual test outline

Run each on a **local** pane and repeat 1–6 on an **SSH** pane.

1. Explorer pane rooted **above** a repo (e.g. `…\Desktop`, repo at
   `…\Desktop\gridvibe_colab`). Navigate into the repo, pin. The sidebar shows
   the repo, branch, and (new) the scoped path.
2. Save Workspace → close GridVibe entirely → relaunch → restore. **The pin is
   still set and the sidebar shows the same repository** — not the yellow
   "no worktree" text from the top directory.
3. Repeat 1–2 five times, restoring differently each time (restart-with-save,
   the launcher's per-row **Save**, and Restore from the Workspaces card). The
   note calls this flaky, so a single pass is not evidence.
4. Pin a subdirectory *inside* the repo (e.g. `web/static/js`). Restore. The
   Graph shows only that subdirectory's commits, and Stage/Unstage/Discard All
   still act on that scope only.
5. Pin a folder that is **not** inside any repository. Restore. You should get
   the new explicit message naming the pinned path plus **Clear pin** — not a
   bare "Folder is not inside a Git worktree".
6. Pin while browsing the explorer root itself. Restore → the pin button is
   still pressed and the repo bar says the scope is the root.
7. Turn **Follow** on with a pin set, restore, turn Follow off → the pane
   returns to the pinned scope.
8. Switch the pinned pane to terminal mode and back to explorer → **the pin is
   gone** (this is deliberate) and the pane re-derives its root.
9. Expand 4 commits, save, restore → the same 4 commits are expanded and the
   sidebar is scrolled roughly where you left it.
10. Open a diff from a commit, save, restore → that commit is expanded and its
    row is scrolled into view (the once-per-commit reveal).
11. Two workspaces, each with a differently pinned explorer pane; switch tabs a
    few times, then save and restore → each pane keeps **its own** pin and
    expansion (the cached-group capture path).
12. Save a workspace as a preset, edit the row's **directory** in the launcher,
    relaunch → the pin is dropped **and the launcher says so once**.
13. Close one pane in a 3-pane group → the two survivors keep their pins and
    expansions.

### 2.4 Documentation after manual verification

- **`CLAUDE.md` + `AGENTS.md`** — correct the *"The Git sidebar has one selected
  scope"* guardrail so it describes the pin as a **fixed path scope captured
  from the browsed folder**, not "repository-level". This is the point of the
  stage: a stale contract is worse than a missing one.
- **`docs/session_state_guideline.md`** — add the pin pair to the field
  inventory with the rule the new test enforces: *`explorer_git_pin_active` and
  `explorer_git_pinned_path` are one fact and are captured, normalized, stored
  and restored together; a pin is meaningless without the root it was captured
  under, so a route that retargets the root drops it.* (Same shape as the
  `explorer_root_directory` / `explorer_root_configured` rule already recorded.)
- **`CHANGELOG.md`** — a `(fix)` bullet per defect the characterization actually
  caught. If nothing was broken, say so plainly in this stage's notes and ship
  the tests as regression cover with no changelog entry.
- **`docs/testing_issues.md`** — open an issue for note 10 in the existing
  format, with the reproduction from 2.3 steps 1–3, and close it with the
  finding. That is the maintained record; this plan file is not.

---

## Stage 3 — The pin is visible where it was made (note 8)

**Risk: medium**, almost all of it in **3a**. Depends on stage 2.

### 3a. Pure move: extract `explorer-tree.js`

**Why now:** guardrail 6's trigger (§0.4). `explorer-viewer.js` is 7,781 lines;
stage 3b changes the tree's row markup and stage 4 adds tree context-menu
entries. Doing the move after either means it is no longer pure.

**Solution:** move the Files-tree domain out of `explorer-viewer.js` into
`web/static/js/explorer-tree.js`, **byte-identically**, loaded directly after
`explorer-viewer.js` (both stay classic scripts sharing one global scope — the
same shape `explorer-tabs.js`, `explorer-diff.js` and `explorer-git-sidebar.js`
already use). `explorer-viewer.js` must be a **pure deletion**.

**Tests:** `tests/test_explorer_tree_fold.py` and `tests/test_explorer_find.py`
pass **untouched apart from the module path they load**. A move that has to
change what a test asserts is not a move — stop and re-plan.

**Manual test:** it is a move, so the manual pass is a smoke test: tree renders,
expands, folds, Alt-folds, the name filter works, open-folder and open-in-tab
buttons work, right-click still opens the entry menu, in both local and SSH
panes. Then `make check`.

**Docs:** the repo-layout tree in `CLAUDE.md`, `AGENTS.md` and `README.md` gains
the file with a one-line description; guardrail 6's "remaining trigger" sentence
is rewritten to name the **next** condition (candidate: the explorer's file-list
/ preview surface), because this one has fired.

### 3b. The pin marker

**Problem.** Note 8: nothing in the Files tree shows *which* folder is pinned,
so a pin made in a folder you have since navigated away from is invisible —
which is also half of why note 10 reads as "the pin was lost".

**Solution (code + tests):**

- `explorerTreeRowHtml()` renders a **marker, not a button**, immediately left
  of the open-in-folder button, when the row's path equals the pane's pinned
  path: `<span class="explorer-tree-pin-mark" title="Git scope pinned here">` +
  the existing `EXPLORER_GIT_PIN_ICON`. Marker only in this stage — the *action*
  is stage 4.
- **Zero new persisted state.** The marker is derived from
  `_explorerGitPinnedPath`, which stage 2 makes trustworthy. Note 8's
  *"Persistance/restore must apply"* is satisfied by stage 2, and this is the
  low-blast property worth protecting: resist adding an
  `explorer_tree_pin_marked` field.
- **Paint, never re-render.** `toggleExplorerGitPinnedScope()` calls a new
  `applyExplorerTreePinMark(index)` that touches **exactly two rows** — the one
  losing the mark and the one gaining it — found by one walk over the rendered
  rows, not a `querySelector` per row (the repaint guardrail). Rebuilding
  `[data-explorer-tree-body]` would reset the tree panel's scroll to 0, which
  the capture-phase scroll listener would then persist as the user's position.
- A pin whose folder is not currently rendered (collapsed ancestor, or outside
  the tree) shows no marker. That is honest and costs nothing; the repo bar from
  stage 2 (H3) still names the scope. Do **not** auto-expand to reveal it.
- CSS in `terminals.css` beside `.explorer-tree-row .explorer-icon`, colours
  from `tokens.css`, `display: inline-flex` + an explicit `svg` box matching the
  neighbouring buttons (guardrail 7 — an SVG does not centre by font metrics).
  While that block is open, migrate any palette literals it still carries.

**Tests:**

- `tests/test_explorer_tree_pin_mark.py` (new, Node, against the moved module):
  the marker appears on exactly the pinned path's row; on no row when the pin is
  unset; on the **root** row for a `''` pin; `applyExplorerTreePinMark` moves the
  mark between two rows **without** rebuilding the body (assert the row nodes
  are the same objects afterwards) and leaves the panel's scroll untouched.
- A source-level assertion is fair here for the markup hooks only
  (`explorer-tree-pin-mark`, the `title`) — rendered markup and `data-*` /
  `aria-*` hooks are the documented exception; the behaviour is executed.

**Manual test outline:**

1. Explorer pane on a repo, Files tree open, expand a few levels.
2. Navigate into `web/static/js`, pin → the pin mark appears on that folder's
   tree row, left of the open-in-folder button, sized like its neighbours.
3. Navigate elsewhere (including above the pinned folder) → the mark **stays**
   on the pinned row.
4. Scroll the tree, then toggle the pin off and on → the tree does **not** jump;
   the mark moves without the tree re-rendering.
5. Collapse the pinned folder's parent → no marker anywhere; the Graph header
   still names the scope. Expand again → the marker is back.
6. Pin while browsing the explorer root → the root row carries the marker.
7. Save Workspace → restart → restore → the marker is on the same row.
8. Repeat 2, 3 and 7 on an SSH pane.
9. Check both light and dark explorer themes.
10. Type a name filter; confirm the filtered tree also shows the marker on the
    pinned row if it is in the results.

**Docs after verification:** `CHANGELOG.md` `(feat)` bullet; the Git-sidebar
scope guardrail in `CLAUDE.md` / `AGENTS.md` gains the sentence *"the pinned
path is marked in the Files tree; the mark is derived, never persisted"*;
`README.md` screenshot/caption if it shows the tree.

---

## Stage 4 — Pin & Follow down to file level, and pinning from the tree (note 11)

**Risk: medium-high.** First backend contract change in this plan, plus one new
persisted field. Depends on stages 2 and 3.

### 4.1 Problem

Note 11: *"pin and follow git buttons should also work down to file level, so it
shows only commits on a open file in preview/explorer tab … Also file tree needs
a pin option on the right click menu to pin directories/files not in preview or
tabs."*

Today the scope must be a directory: `_explorer_git_anchor_paths()` calls
`backend.resolve_dir()`, and `_resolve_git_worktree_root()` runs `rev-parse`
with the anchor as `cwd`. `git status` and `git log --graph` already accept a
file pathspec — see the table in §0.1. So this is *one* consumer that needs a
directory, not a redesign.

### 4.2 Solution (code + tests)

**Backend — `web/api.py` + `web/explorer.py`:**

- `_explorer_git_anchor_paths(backend)` returns
  `(root_path, anchor_path, context_dir)`. The scope kind arrives as
  `?scope=path&kind=dir|file`, validated against a **server-side allowlist** —
  an unknown kind is a `400`, exactly the way `GIT_DIFF_CONTEXT_WIDTHS` refuses
  an unknown context name. An absent `kind` means `dir`, so every existing
  client request keeps working unchanged.
- `kind=file` resolves through `backend.resolve_file()`; `context_dir` is
  `backend.file_dirname(anchor)`. `kind=dir` keeps today's behaviour with
  `context_dir == anchor_path`.
- `_get_git_repo_state()`, `_get_git_repo_summary()` and `_git_action_anchor()`
  take the pair: `context_dir` for `_resolve_git_worktree_root()` and
  `validate_repo_paths()`, `anchor_path` for `backend.pathspec()`. Root
  confinement is unchanged and still checked on both.
- **Behavioural consequence that must be stated in the UI, not just the code:**
  with a file scope, `Stage All` / `Unstage All` / `Discard All` act on that one
  file's pathspec, and `Commit` refuses when anything staged lies outside it.
  That follows the existing "bulk actions obey the selected scope" guardrail
  exactly, and it is surprising unless the sidebar says so — so the repo bar's
  scope text (stage 2 H3) shows the **file name**, and the bulk buttons' titles
  name the scope.

**Client:**

- The pin becomes `{path, kind}`. New persisted field
  **`explorer_git_pin_kind`** (`"dir"` | `"file"`, default `"dir"`), because a
  path and what kind of thing it names are **one fact** — the same reasoning
  `CLAUDE.md` already records for `explorer_root_directory` /
  `explorer_root_configured`. Inferring the kind from a `stat` per request was
  considered and rejected: it costs a round trip and answers wrongly for a path
  that has since been deleted.
  *Lower-blast fallback if you want no schema change: encode a directory as a
  trailing `/`. It works, and it violates the "a flag that qualifies a stored
  value is stored beside it" rule — offered, not recommended.*
- The field must ride **every** route in §0.2: `sessions/manager.py` (field,
  `to_dict`, defaults, mutable set), `session_presentation.py`
  (`PANE_PRESENTATION_FIELDS` + `_EXPLORER_STRING_FIELDS` + a normalizer that
  refuses anything outside the two values), `runtime_state.py`
  (`_SESSION_SNAPSHOT_FIELDS`), `saved_sessions.py`
  (`_LIVE_SESSION_VIEW_FIELDS` + the `startup_mode` gate), `session_modes.py`
  (cleared with the rest of the pin), `session-persistence.js`, `launcher.js`
  (dataset + `explorerTabsMatchRoot` gate), and `terminals.js` (describe /
  launch config / pane build / close overlay). **Stage 2's round-trip test is
  parametrised, so this is one new entry in it, not a new test file** — that is
  why stage 2 comes first.
- **Follow at file level:** when Follow is on and the pane is showing a file
  (`pane._explorerMode === 'file'` with an `_explorerFilePath`), the scope is
  that file; when showing a directory, today's browsed folder.
  `explorerGitScopePath()` gains that branch, and the change listener
  (`explorer-git-watch.js`) already reloads on a scope change.
- **Tree context menu:** `handleExplorerContextMenu()` gains **Pin Git here** /
  **Unpin Git** for a tree row (directories *and* files), placed with the
  filesystem entries. Rules: **single-entry only** (never a batch target — the
  same rule Rename lives by); **never** on a commit row (that branch stays
  path-free — "a commit names a repository object, not a path"); the entry is
  **disabled, not dropped**, when the row is outside any worktree, with a title
  saying why.
- Stage 3b's marker learns the file case — same marker, on a file row.

**Tests:**

- `tests/test_api.py` (extend): `kind=file` scopes status/graph to that file;
  a garbage `kind` is a `400` and mutates nothing; a `kind=file` path that is a
  directory is refused; root confinement holds for both kinds; **a refusal
  mutates nothing** (assert on a whole-pane snapshot, the way
  `test_session_modes.py` does).
- `tests/test_git_process_bounds.py` (extend): a file-scoped `git status` /
  `log` still runs with `GIT_TERMINAL_PROMPT=0` and under the module output
  ceilings.
- `tests/test_explorer_git_pin_persistence.py` (stage 2's file): add
  `explorer_git_pin_kind` to the parametrised round trip, including the
  explorer→terminal clear and the wrong-value refusal.
- `tests/test_explorer_fs_batch.py` (extend): the pin entry never appears for a
  multi-entry selection and never for a commit row.
- Node: `explorerGitScopePath()` returns the open file under Follow, the browsed
  folder otherwise, and the pinned pair when Follow is off.

### 4.3 Manual test outline

1. Open a file in the Preview tab. Turn **Follow** on → the Graph shows only
   that file's commits; the repo bar names the file.
2. Switch tabs to a different file → the Graph follows it. Open a directory →
   the Graph widens back to that folder.
3. Turn Follow off → the Graph returns to the previous fixed scope.
4. With a file scoped, check the **Changes** and **Staged Changes** lists show
   only that file, and the bulk buttons' tooltips name the scope.
5. **Stage All** with a file scope while a *second* file is also modified →
   only the scoped file is staged. Then **Commit** → it refuses, naming the
   staged path outside the scope, and tells you to widen the scope or unstage.
6. **Discard All** with a file scope → only that file is restored; another
   modified file and every untracked file are untouched. (Do this on a scratch
   repo.)
7. Right-click a **file** in the Files tree → **Pin Git here** → the Graph
   scopes to it and the tree row gets the pin mark.
8. Right-click a **folder** → **Pin Git here** → folder scope, marker moves.
9. Right-click the pinned row → **Unpin Git** → scope returns to the explorer
   root, marker gone.
10. Select three entries (Ctrl+click), right-click → **no pin entry** at all.
11. Right-click a commit row in the Graph → still only the two copy entries.
12. Right-click a file outside any worktree → the pin entry is **greyed out**
    with an explanatory tooltip, not missing.
13. Pin a file → Save Workspace → restart → restore → **the file pin comes
    back**, marker and all.
14. Pin a file → switch the pane to terminal mode and back → the pin is cleared
    (deliberate).
15. Repeat 1, 7, 13 on an **SSH** pane.
16. Delete the pinned file outside GridVibe, then refresh the sidebar → an
    explicit message plus **Clear pin**, no crash and no silent widening.

### 4.4 Documentation after verification

- **`CLAUDE.md` + `AGENTS.md`** — the *"The Git sidebar has one selected scope"*
  guardrail is rewritten: scope is `(path, kind)`; `kind` is validated against a
  server-side allowlist and anything else is a `400`; `context_dir` is the
  directory `rev-parse` runs in while `anchor_path` is the pathspec; a file
  scope narrows every bulk action and the commit refusal accordingly; the pin
  entry is single-entry, never a batch, never on a commit row.
- **`docs/session_state_guideline.md`** — `explorer_git_pin_kind` added beside
  the pair, with the "one fact" rule.
- **`CHANGELOG.md`** — one `(feat)` bullet, and a **separate** bullet for the
  bulk behaviour under a file scope, because that is the surprising part.
- **`docs/testing_issues.md`** — only if the stage uncovers a defect.

---

## Stage 5 — Branch graph in the Preview panel (note 13)

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
| 2 | 10, 4b | maybe | — | — | ~4 + 3 test files | low-med |
| 3a | (gate) | — | — | — | 2 (pure move) | med |
| 3b | 8 | — | — | — | 3 + 1 test file | low |
| 4 | 11 | yes | `explorer_git_pin_kind` | — | ~12 + 4 test files | med-high |
| 5 | 13 | yes | `explorer_git_graph_open` | `git/graph` | ~10 + 3 test files | high |

**Hard dependencies:** 3b and 4 need 2 (the pin must be trustworthy before it is
made visible or extended). 4's tree menu and 3b's row markup both need 3a. 5 is
independent of 3 and 4 and could be scheduled earlier, but it is the largest
piece and benefits from the scope work landing first.

**Decisions needed before stage 2 starts:**

1. **Pin semantics** — is the pin a frozen *path* scope (what the code does, and
   what notes 8 and 11 assume) or *repository-level* (what `CLAUDE.md` says)?
   Recommendation: path scope; correct the document.
2. **Stage 4's kind field** — explicit `explorer_git_pin_kind` (recommended) or
   the trailing-slash encoding (smaller, worse).
3. **Stage 5 shape** — narrow (`explorer_git_graph_open`) first, or straight to
   the tab-view-mode variant. Recommendation: narrow.

**Every stage ends with `make check`** (`python tests/run_tests.py` and
`python -m ruff check .` on Windows without `make`) green **before** the manual
pass, and the documentation step happens **after** the manual pass, not before.
