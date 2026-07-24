# `terminals.js` Split Plan (N6) — 2026-07-23

Safe, **move-only** decomposition of `web/static/js/terminals.js` (13,510 lines)
into a small set of cohesive classic-script files. Closes finding **N6** of
`docs/guardrail_audit_2026-07-22.md` and is the scheduled opening move of the
source-and-diff / text-editor work (audit §5 item 5).

**This is a refactor of location, not logic.** No behaviour changes, no
rewrites, no renames, no signature changes. Function bodies move byte-for-byte.
If a diff of a moved function shows anything other than a pure cut-and-paste,
it does not belong in this work.

---

## 1. The one fact that makes this safe

`terminals.js` is **not** wrapped in an IIFE or a `DOMContentLoaded` closure.
The uniform 4-space indent is cosmetic residue from when the code lived inside
`<script>` tags in `terminals.html`. Every one of its ~665 top-level
`const` / `let` / `function` / `class` declarations sits in the **global
lexical environment**, which classic scripts *share across `<script>` tags on
the same page*.

The codebase already depends on exactly this:

- `templates/terminals.html:274-277` declares `const DEFAULT_SURFACE_MODE` and
  `const MAX_SESSIONS` in an inline `<script>`; `terminals.js` reads both
  (e.g. `MAX_SPLIT_TERMINALS` at `terminals.js:488`). Cross-script top-level
  `const` sharing is already load-bearing here.
- `shared.js` (loaded first) exposes `resolveTheme`, `getStoredTheme`,
  `themeToggleButtonHtml`, `cssColor`, … which both `launcher.js` and
  `terminals.js` call. The "shared functions in an earlier script" pattern is
  already the norm.

**Consequence:** splitting `terminals.js` into additional classic scripts
loaded in sequence requires **no `export`/`import` wiring and no `window.`
namespacing**. A function moved to an earlier-loaded file still sees the same
`terminals`, `socket`, `cachedGroupViews`, … bindings, because they remain one
shared global scope.

### The only two rules that keep it safe

Because there is no module boundary, correctness reduces to **evaluation
order**:

1. **Move only declarations, never top-level executing statements.** Function
   declarations and pure self-contained `const`s are safe to relocate to an
   earlier-loaded file: function *bodies* don't run until the boot sequence
   (end of `terminals.js`), by which point every binding — including state that
   stays behind in `terminals.js` — exists. A bare top-level statement
   (a call, an assignment, `initTheme()`, an `addEventListener`, the boot
   block) that runs at parse time must **stay in `terminals.js`**; moving it
   earlier can hit a Temporal Dead Zone reference to a `let`/`const` that
   `terminals.js` has not evaluated yet.

2. **All mutable state stays in `terminals.js`, which stays loaded last.** The
   entire `State` section (`terminals.js:456-492` and any other top-level
   `let`) does not move. Socket init (`:13337`) and Boot (`:13495`) do not
   move. Extracted files are pure "library" files of hoisted functions plus
   local constants; they *read and write* the shared state from inside their
   bodies at call time, which is fine.

Corollary: **the new files must remain classic scripts.** Do **not** add
`type="module"` — modules get their own scope and would break every implicit
cross-file reference. Keep `?v={{ version }}` cache-busting on each tag.

Load order becomes:

```
vendor libs → inline consts (terminals.html) → shared.js
            → [ new extracted files, any order among themselves ]
            → terminals.js   (state + socket init + boot; always last)
```

The extracted files may load in any order relative to each other, since none
of them executes top-level code — they only *declare*. `terminals.js` stays
last because it owns the state and the boot.

---

## 2. Current structure (section map)

Banner-delimited sections in load order (line numbers approximate, from the
current file):

| Lines | Section | Nature | Candidate destination |
|---|---|---|---|
| 1–452 | Theme icons + app-config sync + per-pane theme | consts + fns + `initTheme()` call at :44 | icons → `terminal-icons.js`; keep the rest |
| 453–492 | **State** (`terminals`, `socket`, maps, flags) | top-level `let`/`const` | **stays** (never moves) |
| 493–3382 | Close-state capture, confirm shells, session-tab drag | fns | stays (Phase 3 candidate) |
| 3383–3608 | Split-rect geometry | fns | stays (Phase 3 candidate) |
| 3610–4152 | Xterm factory | fns | stays (Phase 3 candidate) |
| 4154–5951 | Teardown, grid build, card DOM, pane wiring, broadcast input, active-terminal | fns | stays (Phase 3 candidate) |
| 5953–6031 | Clipboard helpers | fns | stays |
| 6033–7333 | **Voice input** (capture → Socket.IO → STT, indicator, push-to-talk) | fns | **`voice-input.js` (Phase 2)** |
| 7086–7333 | Scrollback search overlay | fns | stays (terminal-scoped) |
| 7335–7944 | Attach/show xterm, status badge, initial load, error states, status refresh | fns | stays |
| 7946–8003 | Close session group | fns | stays |
| 8005–8016 | Resize | fns | stays |
| 8018–8943 | Generic Helpers **+ explorer language classifier / highlight / diff / source render / md appearance** | fns (interleaved) | explorer fns → `explorer-viewer.js` |
| 8944–11791 | Explorer copy-path menu, image/mermaid zoom, source/diff/markdown render, tab zoom | fns | **`explorer-viewer.js` (Phase 1)** |
| 11793–13335 | Explorer tabbed viewer, breadcrumb, tab view/scroll state, saved-tab persistence, image viewer, md link nav | fns | **`explorer-viewer.js` (Phase 1)** |
| 13337–13493 | Socket init | top-level executing | **stays** |
| 13495–end | Boot | top-level executing | **stays** |

**Key caveat:** the explorer viewer code is **not a clean contiguous slice** —
it is interleaved with generic "Helpers" and terminal-scoped code between
~8018 and ~11791. The module boundary is defined **by function identity, not
line range** (see §4). Phase 1 cherry-picks the explorer functions and leaves
the terminal-generic helpers in place.

---

## 3. Target module set

Minimal, cohesive, each independently shippable and revertible. Fewer/larger
moves beat many small ones for risk — every extra file boundary is another
load-order edit and another test-harness touch.

| New file | Source region | ~Lines | Interface width | Phase |
|---|---|---|---|---|
| `terminal-icons.js` | SVG icon `const`s (`:6-23`, plus explorer/md icons defined nearby) | ~25 | Zero deps; consts read everywhere | **0 (warm-up)** |
| `explorer-viewer.js` | Explorer tabbed viewer, source/diff/highlight render, markdown preview + link nav, image viewer, copy-path menu, breadcrumb, per-tab view/scroll state, saved-tab persistence | ~4,500 | Narrow: reads shared state, calls a handful of generic helpers; boot/socket call a few entry points | **1** |
| `voice-input.js` | Mic capture pipeline, recording indicator, push-to-talk | ~1,300 | Narrow: mic button, focused-terminal lookup, socket emits | **2** |
| *(optional)* `terminal-grid.js` | Xterm factory, card DOM, grid build/teardown, pane wiring, active-terminal | ~2,500 | Wider (touches state heavily) — defer, reassess after Phases 1–2 | 3 |

After Phases 0–2, `terminals.js` drops from ~13,510 to roughly **~7,700**
lines and — more importantly — the three planned frontend features
(Highlight.js pipeline, Diff2Html renderer, editor state machine) land in a
~4,500-line domain file that reviews cleanly, exactly as the audit asks.

Phase 3 is **explicitly optional** and out of scope until the source-and-diff
work lands; the grid/core cluster reads and writes state so densely that its
interface is wide, and extracting it buys less than Phases 0–2.

---

## 4. The mechanical extraction procedure (per module)

Identical every time. Do one module per commit/PR.

**Step 1 — Enumerate the function set (not line range).**
Build the list of functions/consts to move by identity. For `explorer-viewer.js`:

```
grep -nE '^    (async function|function|const|let|class) ' web/static/js/terminals.js \
  | grep -iE 'explorer|highlight|renderExplorerSource|renderExplorerSideBySide|markdown|_md|Md[A-Z]|imageViewer|copyPath|breadcrumb|diff'
```

Refine by hand against the section map. Record the chosen identifiers in the PR
description.

**Step 2 — Dependency check (the safety gate).**
For each function in the set, confirm it references only:
- shared state that stays in `terminals.js` (fine — read at call time), and
- other functions either in the move set or staying behind (fine — global scope).

Confirm **none of the moved lines is a top-level executing statement** (a call,
assignment, or `addEventListener` at 4-space indent). Grep the candidate line
ranges for top-level statements that are not `function`/`const`/`let`/`class`
declarations; any such line stays in `terminals.js`. This is the single check
that prevents a TDZ break.

**Step 3 — Cut, don't retype.**
Move the exact lines to the new file. Preserve the 4-space indentation
(harmless; keeps the diff a pure move so review is trivial and
`git log -M`/`--find-copies` detects it). Add only a one-line file header
comment noting the file was extracted from `terminals.js` per this plan.

**Step 4 — Wire the `<script>` tag.**
In `templates/terminals.html`, add the new tag **after `shared.js`, before
`terminals.js`**, with cache-busting:

```html
<script src="{{ url_for('static', filename='js/explorer-viewer.js') }}?v={{ version }}"></script>
```

**Step 5 — Update the test harness (same commit).**
Add the new filename to the asset list in `tests/test_api.py:_page_html`
(currently `js/shared.js`, `js/launcher.js`, `js/terminals.js`, the two CSS
files). Without this, every `_page_html`-based content assertion that checks a
string now living in the extracted file silently stops covering it — a false
green, not a failure.

If an `ExtractedFrontendAssetsTestCase` exists (audit §5 references it from the
3.5/6.4 extractions), extend its file/`<script>`-tag/load-order assertions to
the new file; otherwise add a small case asserting (a) the template references
the new file, (b) it loads before `terminals.js`, (c) the file exists and is
non-empty.

**Step 6 — Verify (see §5).**

**Step 7 — Commit.** One module, one commit. The revert is: delete the file,
remove the `<script>` tag, restore the lines, revert the test edits — a clean
single-commit rollback.

---

## 5. Verification per phase

1. `make check` — full `tests/run_tests.py` (630 tests) + `ruff`. Content
   assertions must stay green *because* Step 5 kept the asset list complete.
2. **Byte-identity check** of moved code — confirm the move introduced no edits:
   concatenate the new file's moved region with what remains and diff against
   the pre-move function bodies (or simply review that the PR diff is
   delete-here / add-there with identical text). Any real content delta fails
   the "move-only" contract.
3. **Manual smoke** via the `run` skill / `/run`, per module:
   - Phase 0 (icons): every toolbar/pane button still renders its icon;
     theme/mode/browser/voice toggles show correct glyphs.
   - Phase 1 (explorer viewer): open an explorer pane; browse a tree; open a
     source file (syntax highlight), a Markdown file (preview + link nav + the
     appearance popover), an image, and a Git diff (side-by-side); use the
     copy-path context menu; switch tabs and confirm per-tab scroll/zoom
     restore; restore a saved session with explorer tabs.
   - Phase 2 (voice): press-and-hold mic, push-to-talk into the focused
     terminal, recording indicator, transcription result routing.
4. Confirm **no new console errors** on load (a TDZ/order mistake surfaces here
   immediately as a `ReferenceError` during boot).

Do **not** batch phases. Land, verify, and let each sit before the next so a
regression has a single obvious cause.

---

## 6. Sequencing

| Phase | Module | Rationale |
|---|---|---|
| **0** | `terminal-icons.js` | Warm-up. ~25 lines of dependency-free consts. Exercises the *entire* pipeline — new file, `<script>` tag, `_page_html` asset-list edit, extraction test — with essentially zero behavioural surface. Proves the harness before the big move. |
| **1** | `explorer-viewer.js` | The audit's mandated pre-work. Biggest payoff: de-risks all three planned frontend features. Do it before Phase 1 of the source-and-diff proposal. |
| **2** | `voice-input.js` | Cohesive, narrow interface, cleanly contiguous (~6033–7333, minus the terminal-scoped search overlay). Independent of Phase 1. |
| **3** | *(optional)* `terminal-grid.js` | Reassess only after 1–2 and only if the grid/core surface still taxes review. Wide state interface → lower reward, higher care. |

---

## 7. Guardrails for this work

- **Move-only.** No renames, no signature changes, no dead-code removal, no
  "while I'm here" cleanups in the same commit. Behaviour-preserving relocation
  exclusively. Cleanups, if wanted, are separate follow-up commits after the
  move is verified.
- **Keep classic scripts.** No `type="module"`; no bundler. The whole plan
  rests on shared global scope.
- **State never moves.** The `State` section, socket init, and boot stay in
  `terminals.js`, which stays last in load order.
- **No top-level executing statements move.** Only declarations.
- **CLAUDE.md rule 6 already forbids growing `terminals.js`** — this plan is
  how that file finally shrinks. New feature surface goes into the new domain
  files (or their own), never back into `terminals.js`.
- **Docs:** update `CHANGELOG.md` only if a user-visible line is warranted
  (this is internal refactor — likely a single "Internal" note or none), and
  update the audit's §5 item 5 status once Phase 1 lands.

---

## 8. Risks & mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| A moved top-level executing statement causes a boot-time `ReferenceError` | Low | Step 2 dependency check + Step 3 "declarations only"; caught instantly by §5.4 console check |
| A `_page_html` content assertion silently stops covering moved code | Medium | Step 5 asset-list edit is mandatory and in the same commit; reviewer checks the list grew |
| Load-order regression (new file after `terminals.js`) | Low | Extraction test asserts the new tag precedes `terminals.js`; §5.4 catches it |
| Interleaved explorer/generic helpers split incorrectly | Medium | Boundary defined by function identity (§4 Step 1–2), not line range; manual smoke of every explorer view (§5.3) |
| Accidental logic edit hidden in a large move | Medium | Byte-identity check (§5.2); PR diff must read as delete-here/add-there |

**Non-goals:** ES modules, a build step, TypeScript, renaming anything,
consolidating duplicated logic, or changing any runtime behaviour. All are
out of scope; several would violate the move-only contract.
saso
---

## 9. Definition of done (per phase)

- New file exists, loaded after `shared.js` and before `terminals.js` with
  `?v={{ version }}`.
- Moved code is byte-identical to its former self.
- `tests/test_api.py:_page_html` asset list includes the new file; extraction
  test covers its presence + load order.
- `make check` green; no new console errors on load; manual smoke of the
  module's surface passes.
- `terminals.js` is strictly smaller; no top-level state or executing statement
  left the file.
- Single-commit revert restores the prior state exactly.
