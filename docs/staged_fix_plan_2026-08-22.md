# Staged fix plan — branch review `szua_gridvibe_opt-with-wrk`

Derived from [`branch_review_opt_with_wrk_2026-08-22.md`](branch_review_opt_with_wrk_2026-08-22.md).
Five stages, highest impact/risk first. Each stage is independently shippable and ends with
a manual test **you** run in the real app.

## How to run a stage

1. Implement the **Fix** items, including the automated test named in each.
2. `make check` (Windows without `make`: `python tests/run_tests.py` && `python -m ruff check .`).
   Baseline before any of this work: 1947 tests OK, ruff clean — a stage is not done until
   it is back there.
3. Run the **Manual verification**. Every stage's test states what you see *before* the fix,
   so run it once on the current build first if you want the contrast.
4. Apply the **Documentation** items, then commit the stage.

One commit per stage keeps the audit traceable. Stages 1–4 are independent of each other;
Stage 5 assumes Stage 1 has landed (its README paragraphs describe Stage 1's behaviour).

> **Note on `CLAUDE.md` / `AGENTS.md`:** both are in `.gitignore`, so guardrail edits below
> will not show up in the diff you are tracking. Make them anyway — they are what stops the
> rule being re-broken — but do not expect git to record them.

---

## Stage 1 — The explorer root and the launch floor

**Findings:** F1 (High), F2 (High), F15 (test gap)
**Risk:** highest. Both defects write wrong values into `runtime_state.json`, so every hour
they stay in is another saved workspace carrying them.

### Problem

Two halves of the same fact — *what* root the explorer is confined to, and *whether anybody
chose it* — are recorded inconsistently.

**F1.** `web/api.py:3036` (SSH branch) and `web/api.py:3076` (local branch) store

```python
explorer_root_directory=root_directory,
explorer_root_configured=bool(configured_root),   # describes the wrong value
```

`_resolve_explorer_open_root()` returns `configured_root` **only while it still holds the
observed working directory**. When the shell has walked outside it the function correctly
falls through to a derived root — but the flag is still computed from `configured_root`, so
a root nobody chose is stored wearing the configured flag. It then behaves as a pin:
`_configured_explorer_root_directory()` returns it, `_resolve_pane_terminal_directory()`
hands it back out, and `_snapshot_session()` persists it.

**F2.** `TerminalSession.launch_directory` is the floor that stops a repository root widening
the explorer above the folder you picked. It is never persisted — absent from `to_dict()`
(`sessions/manager.py:137`), from `_SESSION_SNAPSHOT_FIELDS` (`web/runtime_state.py:153`),
and from the `fields` dict in `SessionManager.install_group()`. Because `_snapshot_session()`
deliberately writes the *observed* directory into the snapshot's `directory` slot,
`__post_init__` rebuilds `launch_directory` from it and the floor moves to wherever the pane
happened to be. The same pane in the same directory then opens the explorer at a different
root before and after a restart.

### Fix

| Where | Change |
| --- | --- |
| `web/api.py:3036`, `web/api.py:3076` | `explorer_root_configured=bool(configured_root) and root_directory == configured_root`. Compare the resolved values (both branches have already normalised: `sftp.normalize` remotely, `os.path.realpath` locally). |
| `sessions/manager.py:137` `to_dict()` | Add `"launch_directory": self.launch_directory` beside `"directory"`. |
| `web/runtime_state.py:153` `_SESSION_SNAPSHOT_FIELDS` | Add `"launch_directory"`, with a comment saying why it is a second directory field: `directory` answers "where is this pane", `launch_directory` answers "what may the explorer not widen past". |
| `sessions/manager.py` `install_group()` `fields` | Add `"launch_directory": config.get("launch_directory")`. `None` already means "not stated" and `__post_init__` falls back to `directory`, so a snapshot written before this field existed keeps working unchanged. |

`split_session` (`web/api.py:2705`) already gets F1 right — there `root_directory` *is* the
configured root. Leave it.

**Tests to add**

- The F15 gap: a sibling to `test_a_configured_root_survives_the_round_trip_and_still_pins`
  (`tests/test_api.py:6628`) that drives the pane's shell **outside** the configured root and
  asserts `explorer_root_configured is False` with the derived root stored. This is the only
  branch where the two values disagree, which is why F1 shipped green.
- A `_snapshot_session()` round trip: a session launched at `C:\repo`, observed into
  `C:\repo\a\b`, snapshotted and rebuilt, still reports `launch_directory == C:\repo`.
- One end-to-end: restore-then-switch-to-explorer roots on the repository, not on the
  subdirectory the pane was restored into.

### Manual verification

**Part A — the derived root must not pin (F1).** Prepare a plain, **non-Git** folder with a
subfolder, e.g. `C:\Temp\gv-check\deep`.

1. Launcher → 1 terminal, connection **Local Repo**, command mode **File Explorer**,
   directory `C:\Users\SasoPC\Desktop\Projects\gridvibe_main`. Launch.
2. In the pane, click **📁 ⇄ 💻** to go to the terminal. Run `cd C:\Temp\gv-check`.
3. Click **📁 ⇄ 💻** back to the explorer. It opens on `gv-check` — expected either way.
4. **📁 ⇄ 💻** to the terminal again. Run `cd C:\Temp\gv-check\deep`.
5. **📁 ⇄ 💻** back to the explorer and read the breadcrumb.

   - **Before the fix:** the root is `gv-check`, and **⬆️** can still step up to it — the
     derived root from step 3 pinned the pane.
   - **After the fix:** the root is `deep`, and **⬆️** is unavailable at it — the pane opens
     where the shell actually is.

**Part B — the floor must survive a restart (F2).** Use a real Git repo; `gridvibe_main`
works.

1. Launcher → **2** terminals, connection **Local Repo**, command mode **Command** (a plain
   terminal, not the explorer), both with directory
   `C:\Users\SasoPC\Desktop\Projects\gridvibe_main`. Launch.
2. In **both** panes run `cd web`.
3. In pane 1, click **📁 ⇄ 💻**. The explorer opens rooted on **`gridvibe_main`** (the Git
   worktree), with `web` selected. This is the control — note it.
4. Top bar → **Workspace ▸ Save Workspace**.
5. Restart GridVibe (launcher → the restart button, *Save workspace & restart*), then restore
   the workspace.
6. Pane 2 comes back sitting in `...\gridvibe_main\web`. Click **📁 ⇄ 💻** on it.

   - **Before the fix:** it roots on **`web`** — you cannot navigate up to the repository,
     and the Git sidebar is scoped to `web`. Different from step 3 for the same pane in the
     same directory.
   - **After the fix:** it roots on **`gridvibe_main`**, exactly as pane 1 did in step 3.

### Documentation

**User-facing — yes.** Both halves change what a restart restores.

- **`CHANGELOG.md`** (Unreleased, two entries in the existing voice):

  > **(fix) An explorer root GridVibe worked out for you no longer becomes a pin.** Switching
  > a terminal to the file explorer picks a root from where the shell is, and that root has to
  > be remembered while the explorer is open because it is what confines it. GridVibe recorded
  > it as though you had chosen it whenever the pane had ever been given a root of its own, so
  > the folder it happened to open in once became the folder it opened in forever. The
  > distinction is now recorded against the root actually stored, so a root you chose still
  > pins and a root GridVibe worked out is let go after that one explorer.

  > **(fix) A restart no longer moves the folder the explorer refuses to widen past.** The
  > explorer will not open above the directory you launched a pane in. That directory was not
  > saved with the workspace, so after a restart GridVibe took wherever the pane had ended up
  > as the place it had started — and a pane restored inside a subfolder could no longer open
  > on its repository. Where a pane was launched is now saved alongside where it is, so the
  > same pane in the same folder opens the same explorer before and after a restart.

- **`README.md`** — audit §4 gaps 2 and 3 become true only once this stage lands, so write
  them here:
  - **File Explorer** intro (line ~128): after "same directory, no re-navigation", say the
    explorer roots on the Git repository containing that directory and never widens above the
    folder the pane was launched in.
  - **Sessions & Workspace ▸ Save & restore** (line ~119): "directories" → say panes come back
    in the directory they were *working in*, falling back to the one they launched in if it is
    gone.
  - **Icons & Shortcuts ▸ 🪟** (line ~170): a split clones where the pane *is*, not where it
    started.

- **Guardrails** (`CLAUDE.md` + `AGENTS.md`, Regression Guardrails §4): extend the existing
  `explorer_root_configured` bullet with the two rules this stage buys —
  *a flag that qualifies a stored value is computed from the value actually stored, never from
  the candidate it was chosen among*, and *a floor that is only meaningful across a restart is
  persisted, or it is not a floor*.

---

## Stage 2 — Large-content rendering fidelity

**Findings:** F3 (Medium), F10 (Low)
**Risk:** high consequence, low blast radius. F3 shows content that is silently wrong; both
fixes are small and local.

### Problem

**F3.** `web/static/js/explorer-worker-core.js`, `parseSideBySideDiff()`:

```js
if (line.startsWith('-') && !line.startsWith('---')) { …delete… }
if (line.startsWith('+') && !line.startsWith('+++')) { …add… }
```

A removed line whose *content* begins with `--`, or an added line whose content begins with
`++`, is read as a `---`/`+++` file header and dropped — and because the counter is not
advanced with it, the corruption is worse than a missing line. Run against a real patch, the
current parser produces:

```
L delete 4 "line 4"   || R add 4 "LINE 4"
L delete 5 "line 6"   || R add 5 "LINE 5"      ← left 5 shows line 6's text
L delete 6 "line 7"   || R add 6 "LINE 7"      ← right 6 shows line 7's text
L delete 7 "line 8"   || R add 7 "LINE 8"
```

So the pane shows **the wrong text under the wrong line numbers, pairing unrelated lines side
by side**, for every line after the dropped one — not just a gap. Triggers are ordinary: `---`
YAML front matter and horizontal rules in Markdown, `--flag` in argument files, `++i;` in
C/C++/Java/JS. The guard is a pure move from `explorer-viewer.js`, but the `large` diff tier
promoted this parser from "Diff2Html fallback" to the primary renderer above 160 KiB /
2,500 lines, so a latent bug became a routine one.

**F10.** `explorerLargeSourceChunkHtml()` correctly prepends a `\n` (the HTML parser eats one
LF after `<pre>`, so the chunk's first character survives). But a chunk that *ends* with `\n`
— which every newline-aligned cut does — renders a trailing empty line inside its own `<pre>`
before the next block box starts, so a spurious blank row appears every ~5,000 lines /
256 KiB.

### Fix

| Where | Change |
| --- | --- |
| `explorer-worker-core.js` `parseSideBySideDiff()` | Drop both `startsWith('---')` / `startsWith('+++')` guards, leaving `if (line.startsWith('-'))` and `if (line.startsWith('+'))`. A `---`/`+++` header only ever appears **before** the first `@@`, and the parser already discards everything there with `if (!oldLine && !newLine) return;`. Inside a hunk, `-`/`+` is unambiguous. **Verified:** this exact edit, run over the patch produced by the manual repro below, restores both lines with correct numbering and still emits no header rows. |
| Same function | While there: a multi-file patch restarts at a new `diff --git` header, which today falls through as an unrecognised line. Reset `oldLine`/`newLine` to `0` on a line matching `^diff --git ` so a second file's headers are discarded the same way the first file's were, rather than being parsed as content. |
| `explorer-viewer.js` `explorerLargeSourceChunkHtml()` | Strip one trailing `\n` from the chunk when emitting. The chunker's losslessness contract in `explorer-tiers.js` is untouched — this is a rendering concern, not a content one. |

**Tests to add**

- `tests/test_explorer_workers.py` (or `test_explorer_tiers.py`, wherever the parser is
  exercised): a patch containing a deletion of `--legacy-flag` and an addition of `++counter;`,
  asserting both rows survive **and** that the rows after them keep their numbers — the
  numbering is the half that actually shows the corruption. Run through the real module in Node.
- A two-file patch, asserting the second file's `---`/`+++`/`index` headers do not become rows.
- `tests/test_explorer_large_file_tier.py`: the chunk boundary is seamless — joining the
  rendered chunks' text reproduces the source with no inserted blank line. Keep the existing
  "the chunks are lossless" assertion; this is the rendering half of it.

### Manual verification

**Part A — the diff (F3).** In a scratch repo. The tricky lines go near the **top** on purpose:
the backend truncates a diff at 4,000 lines (`EXPLORER_GIT_DIFF_MAX_LINES`), so anything
appended at the end would never reach the pane.

The base commit needs a line starting with `--` (so its *deletion* reads `---…`) and the working
copy needs one starting with `++` (so its *addition* reads `+++…`) — that is what exercises both
guards.

```bash
mkdir -p /c/Temp/gv-diff && cd /c/Temp/gv-diff && git init
seq 1 3000 | sed 's/^/line /' > big.txt
sed -i '5s/.*/--legacy-flag/' big.txt
git add -A && git commit -m base
seq 1 3000 | sed 's/^/LINE /' > big.txt
sed -i '6s#.*#++counter;#' big.txt
git add -A
```

1. Open a GridVibe explorer pane rooted on `C:\Temp\gv-diff`.
2. Open `big.txt` and switch to the **Diff** tab. The patch is ~6,000 lines, so the pane shows
   the **Very large diff** notice (and the truncation banner above it) — that is the tier this
   parser now serves.
3. Look at the **top** of the diff, lines 3–9. Read the *text* against the *numbers*, not just
   whether rows are present.

   - **Before the fix** (this is the real current output):

     ```
     5   line 6          |  5   LINE 5
     6   line 7          |  6   LINE 7
     ```

     `--legacy-flag` and `++counter;` are gone, every line below is labelled one too low on the
     side that lost a line, and left 5 (`line 6`) is sitting opposite right 5 (`LINE 5`).

   - **After the fix:**

     ```
     5   --legacy-flag   |  5   LINE 5
     6   line 6          |  6   ++counter;
     7   line 7          |  7   LINE 7
     ```

4. Scroll to the bottom and confirm the last visible numbers on both sides still agree with each
   other — the truncation banner will say the patch was cut, which is expected and unrelated.

**Part B — the large file (F10).** Generate a numbered file so the boundary is visible:

```bash
seq 1 25000 | sed 's/^/L/' > /c/Temp/gv-diff/huge.txt
```

4. Open `huge.txt` in the same pane. The **Large file view** notice appears.
5. `Ctrl+F` is unavailable in this tier by design, so scroll to roughly 20 % of the file and
   find the `L5000` / `L5001` pair (the first chunk boundary — 5,000 lines).

   - **Before the fix:** a blank line sits between `L5000` and `L5001`.
   - **After the fix:** `L5001` follows `L5000` directly. Check `L10000`/`L10001` too.

6. Select from `L4995` to `L5005`, copy, and paste into a text editor — the eleven lines must
   come out with nothing between them.

### Documentation

**User-facing — yes**, for both.

- **`CHANGELOG.md`** (Unreleased):

  > **(fix) Very large diffs no longer misread lines that look like a file header.** A changed
  > line whose text begins with `--` or `++` — a `---` rule or front-matter marker in Markdown, a
  > `--flag`, a `++i;` — was mistaken for the `---`/`+++` header at the top of a patch and left
  > out. Worse than the missing line, everything below it kept the numbering it would have had,
  > so the rest of the file was shown under the wrong line numbers with unrelated lines paired
  > against each other. This only affected diffs large enough for the plain side-by-side view,
  > which is exactly where it is hardest to spot.

  > **(fix) The large-file view no longer inserts a blank line every few thousand lines.** Very
  > large files are painted in blocks, and each block's last line break was drawn as an extra
  > empty row before the next block began — a stray blank line roughly every 5,000 lines, in the
  > pane and in anything you copied out of it.

- **`README.md`** — no change. The **Very large files & diffs** row already describes the tiers
  correctly; this stage makes them behave as described.

- **Guardrails** (`CLAUDE.md` + `AGENTS.md`, §4 Correctness): add —
  *inside a hunk, a diff line's leading `-`/`+` is the marker and nothing else; never
  disambiguate a header from content by prefix, because `--`/`++` are ordinary line content.*

---

## Stage 3 — What typing in a large file costs

**Findings:** F5 (Medium), F6 (Medium)
**Risk:** medium. Frontend-only, well bounded by the tier, and directly felt by the user.

### Problem

The in-place editor's underlay was reworked so a keystroke no longer re-tokenizes the whole
document — that part landed and works. Two whole-document costs stayed behind.

**F5.** `explorer-edit-overlay.js`, `spliceExplorerEditUnderlayRows()`:

```js
if (plan.inserted !== plan.removed) {
    for (let at = plan.start + plan.inserted; at < container.children.length; at += 1) {
        row.dataset.explorerLine = String(at + 1);
        gutter.textContent = String(at + 1);
    }
}
```

Plain typing keeps the counts equal and costs nothing. **Enter, backspace-join, and any paste
containing a newline** walk from the splice point to the end of the document — two DOM writes
per row. Enter near the top of a 20,000-row file is ~40,000 writes inside one animation frame,
which is the shape of cost the splice exists to remove.

**F6.** Per rAF while typing, `paintExplorerEditUnderlay()` still does a full pass four times
over: `explorerSourceRowModel()` builds a record object and a row object per line;
`model.records.map(r => r.text)` builds a second full array; `lineSplicePlan()`'s prefix scan
compares every line string for an edit near the end of the file; and the miss `unshift`s into
the page-wide `_explorerLineRecordCache`, whose 8 slots then fill with dead intermediate
drafts and evict every read-only pane's records.

### Fix

**F5 — number the gutter positionally.** The underlay is the one place where this is safe, and
the reason matters: it renders with `foldControls: false` and an empty `collapsedLines` set, so
its rows are contiguous `1..N` with nothing hidden. The read-only Source view **omits** rows
inside collapsed Markdown sections, so a DOM-order counter there would number wrongly — do not
generalise this.

| Where | Change |
| --- | --- |
| `terminals.css` | `counter-reset` on `.explorer-edit-underlay .explorer-source-lines`, `counter-increment` per `.explorer-source-line`, and `content: counter(…)` on `.explorer-source-line-number`. Scope every selector under `.explorer-edit-underlay` so the Source view keeps its rendered numbers. |
| `explorer-edit-find.js:102`, `:440`, `:502` | These are the only readers of `data-explorer-line` inside the underlay. Replace the attribute lookups with positional indexing (`container.children[line - 1]`, and `indexOf.call(container.children, row) + 1` for the upward read), which the contiguity above guarantees. |
| `explorer-edit-overlay.js` `spliceExplorerEditUnderlayRows()` | The renumber loop then deletes entirely. |

Comment the contiguity precondition where the counter is declared — it is the whole reason the
change is legal.

**F6 — stop walking the document four times.**

| Where | Change |
| --- | --- |
| `explorer-edit-overlay.js` | Keep the record array from the previous frame on the pane and derive `lines` from `model.records` once (it is already `model.records`; the extra `.map()` is what to drop — hold the record objects and compare `record.text` in place). |
| `explorer-viewer.js` `explorerSourceLineRecords()` | Give the editor a pane-local slot instead of the shared LRU, so transient drafts stop evicting the read-only panes' records. The simplest shape: an optional `cache` argument, with the overlay passing `pane._explorerEditRecordCache`. |
| `explorer-repaint.js` `lineSplicePlan()` | Optional, measure first: the prefix scan is O(document) for an edit near the end of the file. If the profile shows it, bound it — the plan already refuses anything wider than `MAX_SPLICE_ROWS`, so a prefix walk beyond `document − MAX_SPLICE_ROWS` from the tail cannot change the answer. |

**Tests to add**

- `tests/test_explorer_repaint.py` / a new overlay test: after a splice that changes the line
  count, the row at the old line 12,000 still reports line 12,001 to the find — proving the
  positional read and the counter agree.
- `tests/test_explorer_edit_find.py`: find still resolves a match's line number with the
  attribute gone.
- A cache test: painting the underlay N times does not evict a second pane's line records.

### Manual verification

Generate a real file to type in:

```bash
seq 1 15000 | sed 's/^/const value/; s/$/ = 1;/' > /c/Temp/gv-diff/typing.js
```

1. Open an explorer pane on `C:\Temp\gv-diff`, open `typing.js`, click **Edit** (✏️).
2. Put the caret at the **end of line 5** and hold a letter key down for ~3 seconds. Both before
   and after the fix this should feel smooth — it is the control, and it must not regress.
3. Now put the caret at the end of **line 5** and press **Enter** ten times, about one per second.

   - **Before the fix:** each Enter drops a visible frame — the caret and the gutter lag the
     keypress by a beat.
   - **After the fix:** Enter is indistinguishable from typing a letter.

4. Select 200 lines from the middle, cut (`Ctrl+X`), then paste (`Ctrl+V`) at the top.
   Both operations should land in one frame.
5. Scroll to the bottom of the file and confirm the **last gutter number is 15,001** (a trailing
   newline opens a final empty line) and that the numbers are continuous across the edit you
   made — this is what proves the CSS counter is numbering correctly, not just cheaply.
6. `Ctrl+F` inside the editor, search for `value14000`, and press Enter. The match must be found
   and scrolled to — this is the positional-index path from the fix.
7. `Esc` to cancel the edit (do **not** save), then re-open the file and confirm the Source view's
   line numbers are unchanged, including a Markdown file with a collapsed section (open any
   `.md` file, collapse a heading, and confirm the numbers still skip the hidden lines).

Step 7 is the one that catches an over-generalised counter.

### Documentation

**User-facing — yes** (a felt latency change).

- **`CHANGELOG.md`** (Unreleased):

  > **(perf) Pressing Enter in a large file no longer stutters.** Typing in the in-place editor
  > was made cheap a while back, but anything that changed the number of lines — Enter, joining
  > two lines with backspace, pasting — still renumbered every line below the edit on the spot.
  > Near the top of a very long file that was tens of thousands of updates inside a single frame,
  > so the caret arrived a beat after the key. Line numbers now follow the document on their own
  > and only the lines that actually changed are rebuilt.

- **`README.md`** — no change. Nothing it claims changes.

- **Guardrails** — none. Add the contiguity precondition as a code comment where the counter is
  declared instead; it is a local invariant, not a repo-wide rule.

---

## Stage 4 — Work that outlives the moment it was for

**Findings:** F11 (Low), F12 (Low), F4 (Medium)
**Risk:** low. Three small, independent cleanups; none changes a contract.

### Problem

**F11.** `explorerRunSourceRenderJob()` captures `pane` and the `code` element. A group switch
suspends the job and a closed cached group abandons it — both covered. But a **single pane
replaced in place** (`replaceSessionPaneMode` at `terminals.js:6356`, and pane close) never
clears `_explorerSourceRenderJob`. The captured `code` is detached, so
`explorerRenderedSourceContainer(code) !== onScreen` stays false and the stand-down branch never
fires; the build runs to completion appending rows into a tree nobody can see, competing for
frames with the pane that just replaced it.

**F12.** `explorerRequestSignal(pane, slot)` stores one `AbortController` per slot on
`pane._explorerRequestAborters` and nothing clears the map on teardown. Six slots are in use
(`file`, `preview`, `diffParse`, `highlight`, `editHighlight`, `changeMarks`). The memory is
trivial; what matters is that a pane torn down mid-flight leaves its worker job running, so a
closed pane can still hold a pool worker on a document nobody is looking at.

**F4.** `web/api.py:3481` runs `_track_terminal_agent_input(...)` **before**
`_send_connection_input(...)`. Its agent-promotion branch calls `effective_directory()`, which
for a remote pane with a known `shell_pid` and no shell-integration observation falls through to
`_remote_process_cwd()` — `transport.open_session(timeout=3.0)` plus a bounded `recv` on a fresh
exec channel. That sits between the user pressing Enter on `claude` and Enter reaching the shell,
on the `async_mode="threading"` handler thread that also serves that pane's later input. Lock
discipline is already correct; only the ordering is wrong.

### Fix

| Where | Change |
| --- | --- |
| `terminals.js` `replaceSessionPaneMode()` (and the pane-close path) | Before the pane object is replaced, call `explorerAbandonSourceRenderJob(terminals[index])` for an explorer pane — the same call `enterExplorerEditMode()` already makes for the same reason. |
| `explorer-viewer.js` | Add `cancelExplorerRequestSlots(pane)` that aborts and clears every slot in `pane._explorerRequestAborters`, and call it from the same teardown point. |
| `web/api.py:3481` | Send first, track after: `_send_connection_input(...)` then `_track_terminal_agent_input(...)`. Nothing in the tracker feeds the send — it only reads the sanitized text — so the swap is behaviour-preserving for everything except the latency. |

Note on F4's alternative: an "observation only, no I/O" flag on `effective_directory()` would
also work and is arguably more honest about what the promotion path wants. The reorder is
smaller and fixes the symptom completely; take the flag only if a second caller turns up wanting
the same thing.

**Tests to add**

- `tests/test_explorer_source_frame.py`: a frame-sliced build whose panel is replaced by a
  *non-explorer* surface stops on its next slice and flushes its queued readers.
- A pane-teardown test asserting every request slot is aborted.
- `tests/test_api.py`: assert the input handler's call order — this is one place a behavioural
  assertion is awkward, so if it has to be a source-order check, keep it to the two call names
  and not their arguments (see F14 in Stage 5).

### Manual verification

F12 has no clean manual test on its own; it rides on step 3 below and on the automated test.

1. Open an explorer pane on `C:\Temp\gv-diff` and open `huge.txt` (the 25,000-line file from
   Stage 2). Let it finish painting.
2. Open DevTools → **Performance**, start recording, and immediately click **📁 ⇄ 💻** to switch
   that pane to a terminal. Stop after ~3 seconds.

   - **Before the fix:** frames after the mode-switch response still show
     `explorerAppendSourceRows` / `insertAdjacentHTML` work — the build is filling a tree that is
     no longer on screen.
   - **After the fix:** that work stops at the switch.

3. In the same recording, check the **worker** tracks (DevTools → Performance, or Sources ▸
   Threads). After the switch no explorer worker should still be running a highlight job for the
   file you just left.

4. **F4 — SSH input.** On an SSH pane with **Shell integration** on (App Settings), type `claude`
   and press Enter.

   - Enter must echo immediately in both builds. The defect needs a remote shell that reported
     its pid but never emitted a directory, which is hard to force deliberately — so this step is
     a *no-regression* check, and the fix's real evidence is the reordered call plus its test.
   - Confirm the pane still promotes to an agent pane (the header title and the 🔄 dropdown change
     as before) and that a **Save Workspace** taken while the agent runs still restores it in the
     directory the agent was started in. That is the behaviour the reorder must not break.

### Documentation

**User-facing — marginally.** F11 is a visible stutter on a mode switch; F4 is an input delay.
F12 is internal.

- **`CHANGELOG.md`** (Unreleased), one combined entry:

  > **(perf) Switching a pane away from a large file stops the work it was doing.** A file big
  > enough to be painted over several frames kept painting after you switched the pane to a
  > terminal or a browser preview, into a view nobody could see and in competition with the one
  > that had just replaced it. Background syntax colouring for that file kept running too. Both
  > now stop when the pane does.

- **`README.md`** — no change.
- **Guardrails** — none new; F11 is a missing call site against the rule that is already written.

---

## Stage 5 — Loose ends and documentation reconciliation

**Findings:** F7 (Low/Medium), F8 (Low), F9 (Low), F13 (Low), F14 (Low), plus the whole of
audit §4.
**Risk:** lowest. Five unrelated small items; do them in any order, or drop any one.

### Problem

**F7 — Find is disabled on a large file's own Diff panel.**
`explorerPaneAllowsFind()` keys only on `explorerPaneSourceTier(pane)`, and it gates both the
header render (`explorer-viewer.js:6970`) and the search itself (`:5370`). One find input serves
all three panels, so a file large enough for the plain view also takes Find away from the
**Diff** panel — whose content is capped at 256 KiB by `EXPLORER_GIT_DIFF_MAX_BYTES` and could
answer perfectly well. The tier notice explains it, but the notice is painted in Source and the
reader who switches to Diff sees only a dead control.

This is the *remaining* half of the already-shipped "Find works again in a commit diff opened
after a very large file" fix, which reset the tier when a **commit** diff opens
(`applyExplorerSourceTier(pane, '')` at `explorer-viewer.js:7287`). A large file's own worktree
diff still inherits its verdict. **Preview is a separate question and should stay as it is** —
the preview of a 4 MiB Markdown file is itself enormous and the Preview find walks its whole
subtree unbounded, which is the freeze the tier exists to remove.

**F8 — the lazy Markdown preview can dead-end on "Rendering preview…".**
`ensureExplorerPreviewLoaded()` paints the loader, then returns `null` without repainting when
`data.state_revision` disagrees with `pane._explorerFileStateRevision`. It relies on the
open-file change listener to notice and reload — but `explorer-git-watch.js` suspends itself
after repeated failures, and nothing else repaints that panel. Guardrail 8 wants a retry
affordance.

**F9 — dead payload fields.** `payload["cwd_probe"]["source"]` and `["requested"]`
(`web/api.py:3097`) are never read — `terminals.js:6443` reads `resolved`, and
`showExplorerCwdNotice()` reads `reason` and `directory`. `requested` is additionally always
`true` in the emitted object. `CWD_SOURCE_PROCESS` and `CWD_SOURCE_PROBE` are produced but no
caller ever distinguishes them from each other.

**F13 — `printf` format-string exposure in the POSIX prompt hooks.** `web/terminal_cwd.py`
expands `$PWD` **into printf's format argument**, so a directory containing `%`
(`/srv/100%done`) is read as a conversion specification and the sequence comes out malformed.
Not a security issue — nothing GridVibe holds is interpolated — but `%` is a legal path
character. Adjacent asymmetry: `decode_osc7_target()` calls `unquote()` unconditionally, so a
path containing a literal `%2F` decodes to `/`.

**F14 — 10 new source-text assertions.** The working rules ask the legacy
`assertIn("function …")` pattern to shrink when touched; this branch added ten more, including
one that pins a source literal down to its trailing comma. `tests/test_api.py` now carries 231.

### Fix

| Finding | Change |
| --- | --- |
| **F7** | Make Find a property of the shown panel. `explorerPaneAllowsFind(pane, view)` consults the tier for `source` **and `preview`**, and always allows `diff` (bounded by `EXPLORER_GIT_DIFF_MAX_BYTES`). Both call sites — the header render at `:6970` and `applyExplorerSearch()`'s early return at `:5370` — pass the active view, so they still cannot promise different things. The `sourceTierAllows()` contract in `explorer-tiers.js` is unchanged; it remains the answer for Source. |
| **F8** | Replace the silent `return null` with a one-line in-panel message and a retry: *"The file changed while the preview was rendering."* plus a **Retry** button that clears `_explorerPreviewLoaded` and calls the loader again. Reuse the existing error-bar styling rather than adding a surface. |
| **F9** | Drop `source` and `requested` from the emitted `cwd_probe` object (`web/api.py:3097`) — the client reads neither, and the server only emits the object when both would be constant. Keep `CWD_SOURCE_PROCESS`/`CWD_SOURCE_PROBE` if the log lines use them; otherwise collapse them. Guardrail 5. |
| **F13** | `printf '\033]7;file://%s\033\\' "$PWD"` in `_POSIX_PROMPT_COMMAND` and in `_REMOTE_HOOK`'s `_gv`. Then decide the encoding question once: either percent-encode on the way out and keep `unquote()`, or emit raw and stop unquoting. Emitting raw and dropping the unconditional `unquote()` is the smaller change and matches what the shells actually emit; whichever you pick, both ends must agree, and `tests/test_terminal_cwd.py` should pin it. |
| **F14** | Convert the ten new assertions to behavioural or contract-level checks where practical (several already have a Node-executed module to run instead), and leave the rest. This is cleanup, not a blocker — if any resists conversion, leave it and note why. |

**Tests to add**

- F7: the find predicate answers `true` for `diff` while the source tier is `large`, and
  `false` for `source` and `preview`.
- F8: a revision mismatch leaves a retry affordance rather than the loader text.
- F13: `tests/test_terminal_cwd.py` — a directory containing `%` round-trips through the hook
  and the parser unchanged.

### Manual verification

1. **F7.** In the Stage 2 scratch repo, commit `huge.txt` and then change a handful of its
   lines so it has a small worktree diff:

   ```bash
   cd /c/Temp/gv-diff && git add -A && git commit -m huge
   sed -i '100s/.*/CHANGED-ONE/; 200s/.*/CHANGED-TWO/' huge.txt
   ```

   Open `huge.txt` in an explorer pane — the **Large file view** notice appears — then switch to
   its **Diff** tab and press `Ctrl+F`, searching for `CHANGED`.
   - **Before the fix:** no find bar at all — the file's own diff inherits the large file's
     verdict, even though the patch is four lines long.
   - **After the fix:** the find bar appears, marks both matches, and the counter steps.
   - Then switch back to **Source** and confirm `Ctrl+F` is still unavailable there and the tier
     notice still says so — the Source half must not change.

2. **F8.** Open a Markdown file, switch to the **Preview** tab, and while it says "Rendering
   preview…" modify the file on disk from another terminal (`echo x >> file.md`).
   - **Before the fix:** the panel can be left on "Rendering preview…" indefinitely.
   - **After the fix:** it says the file changed and offers **Retry**, which loads the new
     content. (If the timing is hard to hit, throttle DevTools → Network to *Slow 3G* first.)

3. **F13.** Create `C:\Temp\gv 100%done` (or `/tmp/100%done` on a remote host), `cd` into it from
   a terminal pane, then click **📁 ⇄ 💻**.
   - **Before the fix:** the explorer opens on the wrong directory, or the pane shows the
     "could not tell where the terminal was" notice.
   - **After the fix:** it opens on `gv 100%done`.

4. **F9 / F14.** No manual test — `make check` is the verification.

### Documentation

**User-facing — partly.** F7, F8 and F13 change what you see; F9 and F14 do not.

- **`CHANGELOG.md`** (Unreleased):

  > **(fix) Find works in a very large file's own diff.** A file big enough for the plain
  > large-file view turns Find off, because that view has no per-line rows to point at. That
  > verdict was applied to the whole pane, so the file's Diff tab — a patch of a few lines, and
  > bounded however large the file is — had no find box either. This was already fixed for a
  > commit diff opened from the Git sidebar; the file's own diff now behaves the same way.
  > Source and the Markdown preview of such a file still have Find off, and still say so.

  > **(fix) The Markdown preview says so when it gives up.** If the file changed in the moment
  > between GridVibe reading its text and rendering its preview, the panel was left showing
  > "Rendering preview…" with nothing to click. It now says what happened and offers Retry.

  > **(fix) A folder with a `%` in its name no longer breaks directory tracking.** The sequence
  > each terminal's prompt uses to report where it is treated a `%` in the path as a formatting
  > instruction, so panes sitting in such a folder reported nothing usable — and the explorer
  > opened somewhere else, or said it could not tell.

- **`README.md`** — the remaining audit §4 items, none of which depend on this stage's code:
  - **Line 274 is wrong today.** "**Both** JSON state files are written the same careful way…"
    — there are **three** durable stores through `web/state_files.py`: `runtime_state.json`,
    `saved_sessions.json`, **and `config.json`** (`web/config.py:211`). The Local Files table two
    lines above already lists all three. Change to "Each of these JSON state files…".
  - **Git row** (line ~134): the fixed pin is cleared when the pane becomes a terminal. The
    sentence "Both settings survive Save Workspace and restart" currently over-promises; add the
    one case where the pin is deliberately dropped, and that follow-browsing is unaffected.
  - **Git row**: name **Unstage All**, and that unlike Discard All beside it there is no
    confirmation because it touches the index only.
  - **File Explorer** section: the in-pane notice when the pane's directory could not be read
    ("The terminal did not answer where it is. Opened at …" / the agent-pane variant) is a
    user-facing surface with no mention anywhere.
  - *Optional:* **Search** row — repository search no longer preselects the first hit. The row
    makes no claim either way, so add a line only if the changed feel is worth calling out.

- **Guardrails** — none.

---

## Explicitly out of scope

**F16 — the large diff tier's DOM build is not frame-sliced.** `renderExplorerLargeDiff()` →
`paintExplorerSideBySideDiff()` assembles the whole model as one `innerHTML` write. It is bounded
by the backend's 256 KiB / 4,000-line cap (~8,000 cells), so it is survivable today. Source got
both halves of the treatment (an 8 ms frame budget in `explorerRunSourceRenderJob`); Diff got its
parse moved to a worker and its build left on the main thread.

Not a defect — an asymmetry. **Revisit it if `EXPLORER_GIT_DIFF_MAX_BYTES` or
`EXPLORER_GIT_DIFF_MAX_LINES` is ever raised**, because that cap is the only thing keeping this
build short enough to get away with.

---

## Stage summary

| Stage | Findings | Risk | CHANGELOG | README | Guardrails |
| --- | --- | --- | --- | --- | --- |
| 1 · Explorer root & launch floor | F1, F2, F15 | High | 2 entries | 3 edits | 2 clauses |
| 2 · Large-content fidelity | F3, F10 | Medium-high | 2 entries | — | 1 clause |
| 3 · Typing cost in a large file | F5, F6 | Medium | 1 entry | — | — |
| 4 · Work that outlives its pane | F11, F12, F4 | Low-medium | 1 entry | — | — |
| 5 · Loose ends & docs | F7, F8, F9, F13, F14, §4 | Low | 3 entries | 5 edits | — |
