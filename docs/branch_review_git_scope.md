# Pre-merge review — `szua_gridvibe_wrk-new-feat` → `main`

> **Status: point-in-time review, not a contract.**
> This is an audit of the branch as it stood at `7e45ae9`. Per the Working
> Rules, an audit is never the citation of record: anything here that turns
> into a rule belongs in the code it governs and in the Regression Guardrails
> lists in `CLAUDE.md` / `AGENTS.md`. Delete this file once the findings have
> been actioned or consciously accepted.
>
> **F1–F10 have been fixed** (see *Resolution* on each). F11 was left as
> housekeeping by decision. The suite is 2213 tests (41 added by the fixes),
> `ruff` is clean, and `node --check` passes on every touched file.

**Scope reviewed:** `git diff main...szua_gridvibe_wrk-new-feat` — 11 commits,
41 files, +6643 / −912. Backend (`web/api.py`, `web/explorer.py`, the three
persistence modules, `sessions/manager.py`), frontend (the new
`explorer-git-pin.js` and `explorer-tree.js`, the Git sidebar, viewer, watch,
search, tabs, launcher, terminals), CSS, docs and tests.

**Gate status (after the fixes):** `python tests/run_tests.py` → **2213
passed, 9 skipped, 0 failed**. `python -m ruff check .` → **clean**.
`node --check` → clean on every touched JS file.

---

## Verdict

The branch is in good shape and the hard parts are done right: the `kind`
allowlist, the `anchor_path` / `context_dir` split, the compare-like-with-like
load-identity fix, the `build_live_session_view_updates` narrowing and the
bounded tree-restore walk are all correct and well argued in-code.

**One confirmed functional bug had to be fixed before merge (F1)** — a
one-line fix, but it broke a behaviour the CHANGELOG explicitly promises.
Everything else was medium-or-below.

**F1–F10 are fixed.** Each finding below carries a *Resolution* line naming
what changed and the test that now executes it. F11 is housekeeping and was
deliberately left alone.

| # | Severity | Area | Summary | Status |
|---|----------|------|---------|--------|
| F1 | **Medium-high** | Correctness | Files-tree pin marker: the render and the repaint read two *different* kind attributes, so a **file** pin's marker is painted and then stripped | **fixed** |
| F2 | Medium | Correctness | A rename/delete retargets every path the pane holds **except** the Git pin | **fixed** |
| F3 | Medium | Performance / race | The right-click menu now blocks on a `git/state` round trip before it opens | **fixed** |
| F4 | Low | Correctness | Launcher's retarget notice numbers unnamed rows by their post-filter index | **fixed** |
| F5 | Low | Identity | The menu's pin action addresses the grid slot, not the captured pane | **fixed** |
| F6 | Low | Dead code | `_explorerGitResolvedAnchor` is write-only; `collapseAllPlan().next` is never read | **fixed** |
| F7 | Low | Consistency | Watch pending-scope trio is cleared asymmetrically | **fixed** |
| F8 | Low | Contract | The pin trio is not validated as one fact server-side | **fixed** |
| F9 | Low | UX | A file pin is reduced to its basename in both label *and* title | **fixed** |
| F10 | Low | UX | Hash-mode find reports a non-hex query with the "not in the loaded graph" message | **fixed** |
| F11 | Trivial | Housekeeping | Tracked scratch plan doc in `docs/`, unreferenced 93 KB image, two pass-through aliases | **left as-is** |

---

## Findings

### F1 — The Files-tree pin marker is derived from two different attributes (Medium-high, **confirmed**)

`web/static/js/explorer-tree.js`

The row markup and the repaint disagree about what kind of thing a row is:

- **Render** — `explorerTreeRowHtml()` (`explorer-tree.js:230`) derives the kind
  from `entry.type` and writes `data-explorer-git-scope-kind="${isDirectory ? 'dir' : 'file'}"`,
  then computes the marker with that same value.
- **Repaint** — `applyExplorerTreePinMark()` (`explorer-tree.js:204`) reads
  `row.dataset.explorerContextKind === 'file' ? 'file' : 'dir'`, i.e.
  `entry.entry_kind`.

Those are not the same field, and they diverge in two ordinary cases:

1. **The filtered tree.** `explorerTreeSearchRowsHtml()`
   (`explorer-tree-search.js:247`) sets `entry_kind: loaded?.entry_kind || ''`
   — empty for any search hit whose parent listing has not already been
   fetched, which is the normal case for a hit deep in the tree. The attribute
   is then `""`, so the repaint reads `'dir'` for **every** filtered row while
   the render read `'file'` for the file rows.
2. **Symlink and special entries.** `_explorer_entry_kind()`
   (`web/explorer.py:1019`) returns `"link"` / `"other"`, while `type`
   collapses both to `"file"` (`web/explorer.py:1104`). Same disagreement in
   the ordinary tree.

**Effect.** With a **file** pinned, `explorerTreeRowHtml()` paints the marker
onto the row and the very next `refreshExplorerPinAffordances()` removes it —
`wanted` is `false`, `mark` exists, so the `mark.remove()` branch runs. That
paint fires on every navigation (`explorer-viewer.js:6527, 6831, 7312`) and on
every pin write (`explorer-git-sidebar.js:739`). The marker therefore appears
on a full tree render and vanishes on the next click.

This contradicts the CHANGELOG's *"it appears in the filtered tree too"*, and
it is precisely the "one question answered twice" that `explorer-git-pin.js`
was created to prevent — the predicate is shared, but its **inputs** are not.

**Fix (one line):** read the attribute the render actually wrote.

```js
row.dataset.explorerGitScopeKind === 'file' ? 'file' : 'dir'
```

**Test gap that hid it.** `tests/test_explorer_tree_pin_mark.py` hand-built
rows as `dataset: { explorerContextPath: path, explorerContextKind: kind }`
rather than feeding `applyExplorerTreePinMark()` the markup
`explorerTreeRowHtml()` actually produces, so the render↔repaint contract was
never executed end to end.

**Resolution.** The paint reads `data-explorer-git-scope-{path,kind}` — the
attributes the render itself wrote.
`ExplorerTreePinMarkRenderPaintAgreementTestCase` is that contract, executed: it
renders through the real builder, builds its row stubs *out of that markup*,
paints over them, and asserts the marked set is unchanged across the paint and a
second repaint. Its fixture carries both divergent entry shapes
(`entry_kind: "link"`, and the filtered tree's `entry_kind: ""`). Reverting the
one-line fix fails four of its five cases. The older hand-built harness now
carries both attribute sets, so it can express the divergence at all.

---

### F2 — A rename or delete retargets everything except the Git pin (Medium)

`web/static/js/explorer-fs.js:855-925`

The filesystem-mutation handler remaps `_explorerPath`, `_explorerFilePath`,
`_explorerFileName`, every tab path, `_explorerTreeExpanded` and the editor's
`edit.path` through `explorerFilesystemRetargetPath()` — and prunes all of the
same on a delete. `_explorerGitPinnedPath` is in neither branch.

Renaming or deleting the pinned path therefore leaves the pin pointing at a
path that no longer exists. The sidebar drops to its error branch until the
user presses **Clear pin**.

This was already true for folder pins on `main`, so it is not a regression —
but the branch makes **files** first-class pin targets, and renaming an open
file is far more common than renaming a folder.

**Resolution.** The move branch of `reconcileExplorerAfterFilesystemMutation()`
retargets `_explorerGitPinnedPath` through the same helper, guarded on `typeof`
so an absent pin is never turned into `''` (a pin on the explorer root). The
**delete** branch is deliberately unchanged: a deleted path cannot be followed
anywhere, and dropping the pin there would silently widen the scope back to the
root instead of naming the path with **Clear pin** beside it — the documented
behaviour. `GitPinRetargetTestCase` in `test_explorer_fs_batch.py` runs the real
reconcile over eight cases: the pin itself, an ancestor, a file pin, a pin
elsewhere, a root pin, no pin, the delete, and the presentation write.

---

### F3 — The context menu now blocks on a network round trip before it opens (Medium)

`web/static/js/explorer-viewer.js:1735-1966`

`handleExplorerContextMenu()` became `async`, and
`explorerGitScopeAvailableForMenu()` issues a `GET .../git/state` for every
right-click whose pin entry would be **Pin Git here** — that is, every tree
row, every Preview listing row, every pinned tab, and blank space in both
browsing surfaces, except the one row that already *is* the pin.

`event.preventDefault()` and the `.explorer-context-target` highlight run
synchronously (correct), but the menu itself only appears after the request
returns. On a remote pane that request is `rev-parse` + `git status` over the
pooled SSH transport with a 2 s server-side timeout, so the row highlights and
nothing else happens for as long as the round trip takes.

Two secondary points:

- **Ordering race.** Two right-clicks in quick succession start two handlers.
  `_explorerContextMenuInvoker` is set before each `await`, so the earlier
  handler can call `showExplorerContextMenu()` *after* the later one, with the
  invoker already pointing at the later row. The pane/session identity check
  after the await is right and catches the pane-replacement case; it does not
  catch this one.
- **Unabortable fetch.** This is a bare `fetch`, not one of the pane's seven
  request slots, so `explorerReleasePaneWork()` cannot abort it when the pane
  is disposed.

**Resolution.** `explorerGitScopeInsidePaneWorktree()` answers from the model
the pane already holds: the sidebar's loaded repository reports `repo_path: ''`
when the worktree *contains* the explorer root, so every path under it is inside
it and the menu opens with no request at all. The probe still runs for
everything that model cannot settle — a pane whose sidebar has never loaded, a
repository sitting *below* the explorer root, and anything under `.git`
(segment-wise, so `web/.gitignore` is not the git directory), where `rev-parse`
genuinely is not in a worktree.

The two secondary points are closed too. `_explorerContextMenuToken` is bumped
on entry to every context-menu gesture and re-checked after the await, so a
superseded handler stops instead of opening its menu over the live one. The
probe takes the `gitScopeMenu` request slot, so `explorerReleasePaneWork()`
aborts it with the rest when the pane is disposed.

Covered in `ExplorerGitScopeMenuSurfaceTestCase`: no request inside the pane's
own repository (folder and file), a request for a repository below the root, for
a pane with nothing loaded, and for every `.git` path; plus a two-gesture race
asserting exactly one menu opens.

---

### F4 — The launcher's retarget notice mis-numbers unnamed rows (Low)

`web/static/js/launcher.js:688-697`

```js
.map((row, index) => (row.querySelector('.t-title')?.value.trim() || `Terminal ${index + 1}`))
```

`index` is the position in the **filtered** array, not in the form. An untitled
row at form position 5 that is the only retargeted one is reported as
`"Terminal 1"`, naming a pane the user did not edit. Capture the row's form
index before filtering.

Two smaller notes on the same function:

- `row.querySelector('.t-dir').value` is unguarded while the `.t-title` lookup
  beside it uses `?.` — `.t-dir` is always present in `buildTerminalRows`, so
  this is a consistency nit only.
- The notice is raised at severity `'info'`, which auto-dismisses after 6 s.
  It reports **silently discarded saved state**; `'warning'` (persists until
  dismissed) is the honest severity, and it still folds into the one launch
  message.

**Resolution.** The form index is captured before the filter, the `.t-dir`
lookup is optional-chained, and the severity is `'warning'` whenever anything
was dropped. The message and the severity moved into two named functions
(`explorerRetargetLaunchNote`, `launchNoticeSeverity`) so they can be executed
rather than read: `LaunchRetargetNoticeTestCase` in `test_notice_banner.py`
slices all three out of `launcher.js` and runs them — including the case that
pins the bug, an untitled row at form position 3 reported as `Terminal 3`.

---

### F5 — The menu's pin action addresses the slot, not the captured pane (Low)

`web/static/js/explorer-viewer.js:1930-1932`

```js
action: () => pinItem.action === 'unpin'
    ? clearExplorerGitPinnedScope(index)
    : setExplorerGitPinnedScope(index, gitScopePath, gitScopeKind)
```

The identity captured a few lines above is checked once, immediately after the
worktree probe, and not again at click time. A group switch while the menu is
open would apply the outgoing pane's path to whichever pane now occupies the
slot. Narrow (menus normally dismiss on the switch), but it is the guardrail-4
shape.

**Resolution.** The pane and session are captured once at the top of the Git
block and re-checked by `gitTargetIsCurrent()` *inside* both actions — the pin
entry and Follow.
`test_an_entry_clicked_after_the_slot_changed_hands_writes_nothing` replaces the
slot's pane between opening the menu and clicking the entry, and asserts neither
a pin write nor a follow toggle happens.

---

### F6 — Dead state and an unread plan field (Low, guardrail 5)

- **`_explorerGitResolvedAnchor`** is written in three places
  (`explorer-git-sidebar.js:102, 1257, 1491`) and read by nothing in the
  application — only by `tests/test_explorer_git_identity.py:553,572`, which
  asserts that it was set. Its in-code justification ("it is what the sidebar
  can show the reader") is not realised: the sidebar renders
  `explorerGitScopeLabel(pinnedPath)`, never the resolved anchor. Either
  surface it or drop it and the assertion with it.
- **`collapseAllPlan().next`** (`explorer-git-search.js:192`) is never read —
  the caller clears the live `Set` directly (`explorer-git-sidebar.js:1136`).
  Only `changed` is load-bearing.

**Resolution.** Both are gone. `explorerGitNoteLoadedScope()` no longer takes or
stores the payload, and the two assertions that only proved the field had been
written were rewritten to state what the load identity actually is.
`collapseAllPlan()` returns `{ changed }` alone, with a third case covering a
non-list argument.

---

### F7 — The watch's pending-scope trio is cleared asymmetrically (Low)

`web/static/js/explorer-git-watch.js:249` and `:648`

Both `explorerGitWatchSuspend()` and the "nothing changed" branch of
`explorerGitWatchCheckOne()` clear `_explorerGitWatchPending` and
`_explorerGitWatchPendingScope` but leave `_explorerGitWatchPendingScopeKind`
standing.

Harmless today — the flush returns early on a null payload, and
`explorerGitWatchApplyRefresh()` always writes all three together — but the
three fields are one fact, and one of them could hold a value from a scope the
pane had left.

**Resolution.** Stored as one object: `_explorerGitWatchPending = { data,
scopePath, scopeKind }`, destructured at the flush. There is nothing left to
clear asymmetrically.
`test_git_watch_pending_payload_and_its_scope_are_one_field` asserts the two
parallel fields no longer exist.

---

### F8 — The pin trio is not validated as one fact server-side (Low)

`web/session_presentation.py:930-944`

`explorer_git_pin_active`, `explorer_git_pinned_path` and the new
`explorer_git_pin_kind` are each normalized independently. A payload stating
only `explorer_git_pin_kind` normalizes cleanly, and — with the (correct) new
`build_live_session_view_updates` rule that takes the field set from what the
page stated — that value alone can be written onto a live pane, pairing a new
kind with an old path.

Every current client sends all three together (`session-persistence.js`'s
`explorerGitPinDescriptor()` is the single source), so this was latent. But
`CLAUDE.md` records the invariant as *"one fact in two fields, captured,
normalized, stored and restored together"* — now three — and nothing enforced
it.

**Resolution.** `normalize_pane_presentation()` refuses a payload stating part
of the pin, on the same shape as the existing "active tab requires open tabs"
rule. `explorer_git_pin_kind` stays optional in one direction only: it postdates
the pair, so a client that never learned it keeps directory semantics — exactly
as the `?kind=` parameter's own absent-means-`dir` rule does — but it may never
appear *without* the pair.

The stored-record path stays forgiving for the reason the tab rule already is: a
snapshot written before half the pair existed stores it as `None`, which is
stripped, so `normalize_pane_presentation_fields()` supplies the companion
rather than turning an old file into an unrestorable pane. Silence is a
statement from the page and an omission from a file. Five new cases in
`PinRefusalTestCase`; the three value-rule refusal tests were also given whole
pin records, since they had begun failing for the wrong reason.

---

### F9 — A file pin is reduced to its basename in the label *and* the title (Low)

`web/static/js/explorer-git-pin.js:44-51`

```js
if (explorerGitScopeKind(kind) === 'file') {
    return value.replace(/\\/g, '/').split('/').filter(Boolean).pop() || value;
}
```

`explorerGitPinLabel()` is read by the repo bar's scope row, its `title`, the
pin button's title, and the outside-worktree notice. For a file scope every one
of them shows only the basename, so `web/static/js/a.js` and
`tests/fixtures/a.js` are indistinguishable, and the root-relative path is
nowhere on screen.

That is the same failure the scope row exists to prevent — an unexplained
scope reads as a lost pin. The basename is the right *label* in a narrow
column; the `title` should carry the full root-relative path.

Related: `explorerGitPinLabel('', 'file')` returned `''`. Unreachable today (a
root pin is always `dir`), but it was the one input for which the function had
no answer.

**Resolution.** `explorerGitPinPathLabel()` is the whole-path spelling;
`explorerGitPinLabel()` stays the short one. Every *title* built from a scope
reads the former — the scope row, the pin button's "pinned: …", **Clear pin**,
the out-of-worktree notice and the three bulk-action tooltips — and scope rows
carry `path` beside `label`. `explorerGitPinLabel()` is now total: no leaf to
take falls back to the whole-path spelling, so `('', 'file')` is `root`. Five
new cases in `ExplorerGitScopeLinesTestCase`, and the sidebar render test now
asserts the short label, the full-path title, and the absence of the abbreviated
one.

---

### F10 — Hash-mode find reports a non-hex query as "not in the loaded graph" (Low)

`web/static/js/explorer-git-search.js:104-125`

`validHashQuery` correctly refuses a non-`[0-9a-f]` query rather than searching
it as a subject (guardrail-conformant). But the refusal and a valid-but-absent
id produce the same `emptyText`: `"No commit in the loaded graph"`. Typing
`zzz` in hash mode reports that the id is not loaded, when the truth is that it
is not an id. Two messages, one predicate already computed.

Also minor: in `paintExplorerGitCommitSearch()` the short hash fell back to
`commit.full_hash` (`shortHash = commit.hash || hash`) while
`renderExplorerGitPanel()` used `commit.hash` alone. The backend supplies both,
so it never showed — but a payload carrying only one field would have rendered
one thing and repainted as another.

**Resolution.** The two empty results read differently: a non-hexadecimal query
is reported as not being an id, rather than as an id that is not loaded. The
short hash and the searchable hash are written once
(`explorerGitCommitShortHash`, `explorerGitCommitSearchableHash`) and read by
both the render and the paint. The search harness also gained
`encoding="utf-8"` — it had been decoding Node's output with the host console
codepage, which mangles the em dash in the new message.

---

### F11 — Housekeeping (Trivial)

- **`docs/git_improvements_plan.md` is tracked scratch inside `docs/`.** Its own
  header declares it non-citable "in the same sense as `docs/r&d/`", but
  `docs/r&d/` is gitignored and `docs/` is the directory the Working Rules
  reserve for maintained, citable documents. It also still carries stages 3 and
  4 marked `shipped 2026-08-27` / `shipped 2026-08-28`, which its own rule says
  to delete once shipped — only stage 5 is outstanding. Either move it under
  `docs/r&d/`, or trim it to the open stage and add it to the Working Rules
  document list.
- **`docs/images/diff_issue.png` (93 KB) is referenced by nothing** in any
  tracked `.md`. (Separately, and pre-existing on `main`: `CHANGELOG.md`
  references `curent_git_unclear.png`, `clipping.png`, `error_load.png` and
  `diff_empty_space.png`, none of which exist in `docs/images/`.)
- **Two pass-through aliases** left after the refactors:
  `explorerGitRequestedScopeKind(pane)` is `explorerGitScopeKind(pane)`
  verbatim (`explorer-git-sidebar.js:71`), and `wireExplorerCopyPathMenu()` is a
  one-line call to `wireExplorerContextMenu()` (`explorer-viewer.js:1979`).

---

## What was checked and is correct

Recorded so the next reviewer does not repeat the work.

### Security / root confinement
- `?kind=` is validated against a **server-side allowlist** before anything
  else, and an unknown value raises `ValueError` → `400` through
  `_explorer_route_response()`, mutating nothing. An **absent** `kind` means
  `dir`, so an older client is unaffected. Covered by
  `test_explorer_git_file_scope_rejects_bad_kind_directory_and_escape_without_mutation`.
- `kind=file` resolves through `backend.resolve_file()` (existence-checked,
  directory-rejected, root-confined) and `kind=dir` through `resolve_dir()`, as
  specified. `context_dir` is `file_dirname(anchor)` and can never escape the
  root, since `resolve_file()` refuses the root itself.
- `_get_git_context()` and `_git_action_anchor()` both run
  `validate_repo_paths()` on the **context directory and the anchor** — the
  second check is the new one and is present in both.
- `file_dirname()` already existed on both the local and SFTP backends; no new
  backend surface.
- All new markup interpolation goes through `escHtml()`, including the new
  `data-explorer-git-scope-*` attributes and the pin/scope titles.

### Concurrency and identity
- Every `await` in the Git sidebar, watch and viewer paths re-checks pane,
  session **and now scope kind** before writing or painting; the split between
  "write to the captured pane" and "skip anything addressing the slot" is
  preserved. F5 is the single exception found.
- `EXPLORER_GIT_IDENTITY_SCOPE_CHANGED` / `_PANE_REPLACED` continue to be
  distinguished, and `explorerGitNoteLoadedScope()` records the **requested**
  scope, not the server's resolved answer — the load-identity fix is correct and
  is what stops every background poll refetching the repository.
- `explorerGitWatchFlushPending()` applies a deferred payload under the scope it
  was *fetched* under, not the pane's current scope. Correct.
- The watch backs off on failure, so a pin on a deleted or out-of-worktree path
  does not hammer `git/state`.
- `notePanePresentationChanged()` resolving the group from
  `pane._session.group_id` rather than `visibleGroupId` is correct; `group_id`
  is on `to_dict()` (`sessions/manager.py:142,250`) and
  `describeGroupPresentation()` already handles cached, non-visible groups.
- No new locks, emits under locks, or shared-state reads were introduced — the
  backend change is purely parameter threading.

### Persistence
- `explorer_git_pin_kind` reaches **all eight** enumerated field sets:
  `TerminalSession` field + `to_dict()`, `SessionManager` defaults +
  `allowed_fields`, `_SESSION_SNAPSHOT_FIELDS`, `_LIVE_SESSION_VIEW_FIELDS`,
  `PANE_PRESENTATION_FIELDS`, `_EXPLORER_STRING_FIELDS`, the saved-preset
  defaults/normalizer/merge, and both launcher dataset directions.
- `_normalize_explorer_git_pin_kind()` raises rather than coercing, so a bad
  value costs the launchable shape — matching the failure boundary. Runtime
  state validates through `normalize_pane_presentation_fields()` in
  `_validate_session()`, so a corrupt snapshot drops the pane rather than
  restoring a bogus kind.
- The `build_live_session_view_updates()` change (field set from the raw page
  payload, values from the normalized preset) is a genuine bug fix and is
  correctly reasoned: the normalizer fills every absent key, so taking the field
  set from it wrote defaults onto live panes.
- `apply_pane_mode_change()` now clears **Follow as well as the pin** on the way
  out of explorer mode. Correct per the guardrail, and the launcher's re-rooting
  still drops the pin alone.

### Architecture
- **`explorer-tree.js` is a verified pure move.** Diffing the lines removed from
  `explorer-viewer.js` against the lines added to `explorer-tree.js` in `5f762aa`
  yields exactly one difference: the new file's header comment. 636 removed,
  648 added, 0 changed. `explorer-viewer.js` is a pure deletion in that commit.
- `explorer-git-pin.js` is DOM-free, `require()`-able and Node-tested, with
  paint-only adapters in both consumers — the pattern the guardrail prescribes.
  F1 is a defect in one adapter's *input*, not in the module.

### Performance
- `hydrateExplorerTreeExpansion()` is bounded (`EXPLORER_TREE_RESTORE_MAX_NODES
  = 64`), concurrency-capped (`EXPLORER_TREE_LEVEL_LOAD_CONCURRENCY = 4`),
  provably terminating (`next` is drawn only from a finite expansion set over a
  cycle-free tree — symlinked directories arrive as `type: "file"`), and prunes
  only paths a loaded parent listing proves are gone. "Unproven is not stale" is
  correctly implemented: a budget-truncated walk leaves the rest alone.
- `applyExplorerTreePinMark()` is one walk over the rendered rows touching only
  the rows that disagree, never a body rebuild — the repaint guardrail is
  respected (modulo F1, which makes it touch the wrong rows).
- The context-menu listener is idempotently wired
  (`panel.dataset.contextMenuWired`), so per-render re-wiring cannot stack
  handlers.
- `explorerGitScopeNeedsLoad()` correctly stops an unchanged scope costing a
  render — `loadExplorerGitRepo()`'s own early return still re-renders, which
  would take the caret out of the commit-message textarea.

### Styling
- No palette literals at use sites. `--explorer-warning` is declared in **all
  five** explorer theme blocks (matching `--explorer-muted`'s count) and is used
  in three places.
- Every new class that sets `display` and is toggled by `hidden` carries an
  explicit `[hidden] { display: none }` override (`.explorer-tree-pin-mark`,
  `.explorer-git-commit-search-empty`, `.explorer-git-scope-clear-btn`).
  `.explorer-git-clear-pin-btn` sets no `display`, so the UA rule applies.
  Checked; no leaks.
- `is-pinned-elsewhere` carries a dashed border in addition to colour, so the
  third pin state is not colour-alone.
- Alt-collapse preserves the commit-message focus and caret, and suppresses the
  `mousedown` focus shift so the selection survives the structural render.
