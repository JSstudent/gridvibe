# Staged fix plan — branch review `szua_gridvibe_opt-with-wrk`

Derived from [`branch_review_opt_with_wrk_2026-08-22.md`](branch_review_opt_with_wrk_2026-08-22.md).
Five stages, highest impact/risk first. Each stage is independently shippable and ends with
a manual test **you** run in the real app.

## How to run a stage

1. Implement the **Fix** items, including the automated test named in each.
2. `make check` (Windows without `make`: `python tests/run_tests.py` && `python -m ruff check .`).
    Baseline before any of this work: 1947 tests OK, ruff clean — a stage is not done until
    it is back there.
3. Run the **Manual verification**. Every stage's test states what you see *before* the fix, so
    run it once on the current build first if you want the contrast. Any shell block inside a
    stage is setup, not verification — build it in Git Bash before you launch the pane, per
    [Test scaffolding](#test-scaffolding) below.

    Stages 1 and 2 split their verification into **Part A** and **Part B**. Each Part is a
    self-contained run-through and its steps start again at 1 — so a "step 3" always belongs to
    the Part it sits under, never to the stage as a whole. **Stages 3, 4 and 5 are not split**:
    each is one run-through numbered straight through, so a "step 7" there is the seventh step
    of that stage's single list.

4. Apply the **Documentation** items, then commit the stage.

One commit per stage keeps the audit traceable. Stages 1–4 are independent of each other;
Stage 5 assumes Stage 1 has landed (its README paragraphs describe Stage 1's behaviour).

> **Note on `CLAUDE.md` / `AGENTS.md`:** both are in `.gitignore`, so guardrail edits below
> will not show up in the diff you are tracking. Make them anyway — they are what stops the
> rule being re-broken — but do not expect git to record them.

## Test scaffolding

The manual tests build three throwaway folders under `C:\Users\SasoPC\Desktop\Projects`, beside
your real work, so the launcher's Local Repo picker can reach them without a detour:

| Folder | Used by | Must be |
| --- | --- | --- |
| `gv-check\deep` | Stage 1 Part A | **Not** a Git repository — the test turns on the explorer failing to find a worktree. `Projects` itself is not inside one, so a plain `mkdir` is enough. |
| `gv-diff` | Stages 2, 3, 4, 5 | Its own Git repo (`git init`). Holds `big.txt`, `huge.txt` and `typing.js` — build it once in Stage 2 and reuse it. |
| `gv 100%done` | Stage 5 | Any directory; the `%` in the name is the whole point. |

### Where and how to run the setup commands

**Run them outside GridVibe, in their own window, before you launch the pane a stage asks for.**
The launcher's directory field only accepts a folder that already exists, and generating a file
inside the pane you are currently testing sets the explorer's change watcher off in the middle
of the test.

Every setup block in this document is written for **Git Bash**, which is installed here at
`C:\Program Files\Git\git-bash.exe`. Two ways in:

- **Start menu → "Git Bash"**, then `cd /c/Users/SasoPC/Desktop/Projects`.
- **File Explorer** → right-click the `Projects` folder → **Open Git Bash here** (Windows 11:
    *Show more options* first). This drops you straight into the right directory.

Paste each block as written. `seq`, `sed -i` and `git` all behave as the blocks assume.

> **Expected noise:** `core.autocrlf` is `true` on this machine, so `git add` prints
> `warning: … LF will be replaced by CRLF the next time Git touches it`. That is correct and
> changes nothing about the tests — Git normalizes both sides of every diff identically.

<details>
<summary><b>If you would rather stay in PowerShell</b> — all three setups, verified equivalents</summary>

```powershell
$Projects = "C:\Users\SasoPC\Desktop\Projects"

# Stage 1 Part A — a plain, non-Git folder with a subfolder
New-Item -ItemType Directory -Force "$Projects\gv-check\deep" | Out-Null

# Stage 5 — the folder whose name contains a percent sign
New-Item -ItemType Directory -Force "$Projects\gv 100%done" | Out-Null

# Stage 2 — the test repository, plus the files Stages 3 and 4 reuse
$GV = "$Projects\gv-diff"
New-Item -ItemType Directory -Force $GV | Out-Null
Set-Location $GV
git init

# big.txt — the diff repro. Line 5 starts with `--` in the base commit,
# line 6 starts with `++` in the working copy: one trap for each guard.
1..3000 | ForEach-Object { "line $_" } | Set-Content big.txt
$l = Get-Content big.txt; $l[4] = '--legacy-flag'; Set-Content big.txt $l
git add -A; git commit -m base
1..3000 | ForEach-Object { "LINE $_" } | Set-Content big.txt
$l = Get-Content big.txt; $l[5] = '++counter;'; Set-Content big.txt $l
git add -A

# huge.txt — 25,001 rows, so the large-file tier fires on the row count
1..25000 | ForEach-Object { "L$_" } | Set-Content huge.txt

# typing.js — 15,001 rows, deliberately *under* the tier so the editor
# underlay is still active (that is what Stage 3 measures)
1..15000 | ForEach-Object { "const value$_ = 1;" } | Set-Content typing.js
```

Two later blocks have PowerShell forms too — Stage 5's F7 setup:

```powershell
Set-Location "$Projects\gv-diff"; git add -A; git commit -m huge
$l = Get-Content huge.txt; $l[99] = 'CHANGED-ONE'; $l[199] = 'CHANGED-TWO'; Set-Content huge.txt $l
```

</details>

**WSL** works too — Ubuntu-22.04 is installed. The commands are identical; only the path
changes, to `/mnt/c/Users/SasoPC/Desktop/Projects/...`.

### Cleaning up

`gv-diff` is a real repository, so it will appear anywhere GridVibe lists local repos until you
remove it. When you are done with all five stages:

```bash
cd /c/Users/SasoPC/Desktop/Projects
rm -rf gv-check gv-diff "gv 100%done"
```

Close any GridVibe pane rooted on those folders first — an explorer pane holds its root open,
and a workspace saved while one is live will try to restore it after you delete it.

---

## Stage 1 — The explorer root and the launch floor ✅ *landed*

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
subfolder, e.g. `C:\Users\SasoPC\Desktop\Projects\gv-check\deep`.

1. Launcher → 1 terminal, connection **Local Repo**, command mode **File Explorer**,
    directory `C:\Users\SasoPC\Desktop\Projects\gridvibe_main`. Launch.
2. In the pane, click **📁 ⇄ 💻** to go to the terminal. Run `cd C:\Users\SasoPC\Desktop\Projects\gv-check`.
3. Click **📁 ⇄ 💻** back to the explorer. It opens on `gv-check` — expected either way.
4. **📁 ⇄ 💻** to the terminal again. Run `cd C:\Users\SasoPC\Desktop\Projects\gv-check\deep`.
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

### Status — landed 2026-08-22 ✅

`make check`: **1950 tests OK** (1947 baseline + the 3 below), ruff clean. Both manual Parts
verified in the real app: Part A roots on `deep` with **⬆️** unavailable, Part B brings pane 2
back rooted on `gridvibe_main` exactly as pane 1 in step 3.

**Code — as planned, with one naming correction.**

| Where | What landed |
| --- | --- |
| `web/api.py:3042` (SSH), `:3085` (local) | `explorer_root_configured=bool(configured_root) and root_directory == configured_root`, with the comment saying *why*: the flag describes the root actually stored, not the candidate it was chosen among. |
| `sessions/manager.py` `to_dict()` | `"launch_directory": self.launch_directory` beside `"directory"`. |
| `web/runtime_state.py:163` `_SESSION_SNAPSHOT_FIELDS` | `"launch_directory"`, commented as the second directory field and why both are needed. |
| `sessions/manager.py` `_session_launch_fields()` | `"launch_directory": config.get("launch_directory")`. **The plan called this `install_group()`'s `fields` dict; no such method exists** — `_session_launch_fields()` is the one that builds it, for `create_sessions()` and `install_session_group()` alike. |

`split_session` was left alone as the plan says, and it is still correct: it passes no
`launch_directory`, so a split keeps cloning where the pane *is*.

**Tests — all three added, and each verified to fail against the pre-fix code** (temporarily
reverting the relevant edit, then restoring it):

| Test (`tests/test_api.py`, `ApiRoutesTestCase`) | Pre-fix failure |
| --- | --- |
| `test_a_shell_outside_the_configured_root_stores_a_derived_one` (F15) | `True is not false` — the derived root was stored wearing the configured flag. Also asserts the derived root does not pin the *next* switch. |
| `test_a_snapshot_round_trip_keeps_where_the_pane_was_launched` | `launch_directory` came back as `repo/a/b` instead of `repo`. |
| `test_a_restored_pane_still_opens_the_explorer_on_its_repository` | The rebuilt pane rooted on `web` instead of `repo`. |

The last two rebuild the pane through the real path — `_snapshot_session()` →
`_validate_session()` → `_session_launch_fields()`. One thing worth knowing for Stages 2–5:
`mode` is **not** a captured snapshot field, so a test that rebuilds a pane must supply it the
way `_prepare_launch_sessions()` does (from the group's connection mode) or the pane comes back
as SSH and the explorer switch answers `500`.

**Documentation.** README's three edits and the two guardrail clauses landed as written
(`CLAUDE.md` + `AGENTS.md`, gitignored as the note at the top of this document says).

The CHANGELOG went in differently, on purpose. The plan's two entries were written as new ones,
but Unreleased already carried **"The file explorer follows the terminal back *up*, not only
down"**, which claimed both halves this stage actually completes — it said GridVibe "now
remembers where a pane was actually launched" and "records alongside a saved root whether
anybody picked it", which was true within a session and false across a restart. Three adjacent
entries describing one behaviour, one of them over-claiming, is worse than one true entry, and
these all ship in the same release anyway. So both new entries were **folded into that one**,
which now names the floor not being saved with the workspace and the flag being recorded against
the root actually stored. The neighbouring **"An explorer root you never chose no longer follows
the pane around"** was left as it is: it describes the switch letting a derived root go, which is
a different defect from F1's mislabelling.

---

## Stage 2 — Large-content rendering fidelity ✅

**Findings:** F3 (Medium), F10 (validated non-issue)
**Risk:** high consequence, low blast radius. F3 showed content that was silently wrong; its
fix is small and local.

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

**F10 — non-issue.** The original finding inferred that the source newline at the end of a
chunk creates an empty final line box inside its `<pre>`. Browser layout does not work that
way: the newline remains in `textContent`, but no extra final row is painted. Manual native and
browser checks showed `L5000` / `L5001` and later boundaries directly adjacent. Focused
Chromium and Edge checks using GridVibe's exact adjacent chunk markup and CSS measured two
two-line chunks as 40 px each at a 20 px line height, an 80 px host, and exactly one selected
newline across the boundary. No renderer change is warranted.

### Fix

| Where | Change |
| --- | --- |
| `explorer-worker-core.js` `parseSideBySideDiff()` | Drop both `startsWith('---')` / `startsWith('+++')` guards, leaving `if (line.startsWith('-'))` and `if (line.startsWith('+'))`. A `---`/`+++` header only ever appears **before** the first `@@`, and the parser already discards everything there with `if (!oldLine && !newLine) return;`. Inside a hunk, `-`/`+` is unambiguous. **Verified:** this exact edit, run over the patch produced by the manual repro below, restores both lines with correct numbering and still emits no header rows. |
| Same function | A multi-file patch restarts at a new `diff --git` header. Flush unmatched deletions and reset `oldLine`/`newLine` to `0` there so every file's pre-hunk `---`/`+++`/`index` metadata is discarded rather than parsed as content. |

**Tests to add**

- `tests/test_explorer_workers.py` (or `test_explorer_tiers.py`, wherever the parser is
    exercised): a patch containing a deletion of `--legacy-flag` and an addition of `++counter;`,
    asserting both rows survive **and** that the rows after them keep their numbers — the
    numbering is the half that actually shows the corruption. Run through the real module in Node.
- A two-file patch, asserting the second file's `---`/`+++`/`index` headers do not become rows.

### Manual verification

**Part A — the diff (F3).** In a scratch repo. The tricky lines go near the **top** on purpose:
the backend truncates a diff at 4,000 lines (`EXPLORER_GIT_DIFF_MAX_LINES`), so anything
appended at the end would never reach the pane.

The base commit needs a line starting with `--` (so its *deletion* reads `---…`) and the working
copy needs one starting with `++` (so its *addition* reads `+++…`) — that is what exercises both
guards.

```bash
GV=/c/Users/SasoPC/Desktop/Projects/gv-diff
mkdir -p "$GV" && cd "$GV" && git init
seq 1 3000 | sed 's/^/line /' > big.txt
sed -i '5s/.*/--legacy-flag/' big.txt
git add -A && git commit -m base
seq 1 3000 | sed 's/^/LINE /' > big.txt
sed -i '6s#.*#++counter;#' big.txt
git add -A
```

1. Open a GridVibe explorer pane rooted on `C:\Users\SasoPC\Desktop\Projects\gv-diff`.
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

**Part B — the large file (F10 validation).** Generate a numbered file so the boundary is visible:

```bash
seq 1 25000 | sed 's/^/L/' > /c/Users/SasoPC/Desktop/Projects/gv-diff/huge.txt
```

1. Open `huge.txt` in the same pane you used for Part A. The **Large file view** notice appears.
2. `Ctrl+F` is unavailable in this tier by design, so scroll to roughly 20 % of the file and
    find the `L5000` / `L5001` pair (the first chunk boundary — 5,000 lines).

    - **Observed before any renderer change:** `L5001` follows `L5000` directly in both native
        and browser mode. The same is true at `L10000`/`L10001`.

3. Select from `L4995` to `L5005`, copy, and paste into a text editor — the eleven lines must
    come out with nothing between them.

### Documentation

**User-facing — yes**, for F3. F10 gets no changelog entry because it was a non-issue and no
behavior changed.

- **`CHANGELOG.md`** (Unreleased):

    > **(fix) Very large diffs no longer misread lines that look like a file header.** A changed
    > line whose text begins with `--` or `++` — a `---` rule or front-matter marker in Markdown, a
    > `--flag`, a `++i;` — was mistaken for the `---`/`+++` header at the top of a patch and left
    > out. Worse than the missing line, everything below it kept the numbering it would have had,
    > so the rest of the file was shown under the wrong line numbers with unrelated lines paired
    > against each other. This only affected diffs large enough for the plain side-by-side view,
    > which is exactly where it is hardest to spot.

- **`README.md`** — no change. The **Very large files & diffs** row already describes the tiers
    correctly; this stage makes them behave as described.

- **Guardrails** (`CLAUDE.md` + `AGENTS.md`, §4 Correctness): add —
    *inside a hunk, a diff line's leading `-`/`+` is the marker and nothing else; never
    disambiguate a header from content by prefix, because `--`/`++` are ordinary line content.*

### Status — landed 2026-08-22 ✅

`make check` equivalent: **1952 tests OK** (1950 before the stage + 2 Node-executed parser
regressions; 9 platform skips), ruff clean. Part A was verified in the real app: both special
content lines survive, the following rows retain their correct numbers, and the last visible
numbers agree. Part B established that F10 is a non-issue in native and browser mode, backed by
focused Chromium and Edge geometry/selection checks; `explorerLargeSourceChunkHtml()` was left
unchanged.

---

## Stage 3 — What typing in a large file costs ✅

**Findings:** F6 (Medium); F5 (Medium — verified real, **deferred**, see below)
**Risk:** medium. Frontend-only, bounded by the presentation tier, and directly felt by the
user.

> **This stage was re-aimed on 2026-08-22, before implementation.** It originally led with F5
> (the underlay's renumber loop). Two manual runs against the real app moved the target, and
> the code review that followed found two of F5's fix instructions to be wrong. The
> measurement is recorded first, because the conclusion is only as good as it.

### What was measured

The original manual test asked for ten Enters at the top of a 15,000-line file, predicting a
dropped frame per keypress. Observed instead: barely perceptible and intermittent — *"not
really, only if I hit at the right time or something; it does get better with holding longer,
like it needs to catch up."*

A second run isolated it. With the caret at the **end** of the file the splice lands at the
tail, so the renumber loop walks ~1 row instead of ~15,000 while everything else in the frame
is unchanged. It hitched the same — and the hitch arrived **half a second to a second after
the keypress**, which is not a dropped frame at all.

That delay is the **settle pass**, and its pipeline accounts for the whole interval:

| Step | Where | Cost |
| --- | --- | --- |
| Enter → rAF splice | `paintExplorerEditUnderlay()` | a full `explorerSourceRowModel()` (15k records + 15k row objects), the `.map()`, and `lineSplicePlan()` — O(document) even on the "cheap" path |
| debounce | `scheduleExplorerEditUnderlaySettle()`, `explorer-edit-overlay.js:349` | **180 ms** |
| tokenize | `workers.highlight(draft, …)` — 315 KB through Highlight.js | a few hundred ms, **off**-thread |
| repaint | `.then` → `paintExplorerEditUnderlay(index, { full: true, runs })` | **on** the main thread: `explorerSourceRowModel()` *again*, 15k row HTML strings, a join into a multi-MB string, an `innerHTML` parse of ~45k nodes, then `repaintExplorerEditFind()` over the whole draft |

The intermittence has the same root. Every keystroke calls `cancelExplorerEditHighlight()`,
aborting the in-flight worker job, so at roughly one keypress per second on a file this size
the settle sometimes completes and sometimes is cancelled — "only if I hit at the right time."
Holding a key re-arms the 180 ms debounce every frame, so it never fires at all: that is the
"better when held", and the one settle on release is the "catch up".

**Conclusion:** the felt cost of editing a large file is the settle's whole-document rebuild,
not the renumber loop. This stage targets the rebuild.

### Problem

**F6 — the underlay walks the whole document several times per edit.** Per rAF while typing,
`paintExplorerEditUnderlay()` builds `explorerSourceRowModel()` (a record object *and* a row
object per line), then `model.records.map(r => r.text)` for a second full array, then
`lineSplicePlan()` compares line strings across the untouched part of the document. The miss
then `unshift`s into the page-wide `_explorerLineRecordCache`, whose limit is
`min(max(panes, 1) × 2, 8)` — so with a **single** explorer pane it is **2 slots**, and every
typing frame evicts both the previous draft and the pane's own file records.

**The settle repaints 15,000 rows to change their colour.** `settleExplorerEditUnderlay()`
already establishes that the draft has not moved since the rows were painted (it returns early
when `pane._explorerEdit.draft !== draft`). So the document is the same document and the row
set is identical; the only thing the repaint changes is the **colour** of the code cells —
from the per-line fallback lexer the splice painted to the real Highlight.js runs the worker
returned. It expresses that as a full `innerHTML` replacement of the entire underlay. That is
the "a repaint is not a rebuild" guardrail, applied to every Source surface *except* this one.

### Fix

**Repaint the code cells whose colour actually changed.** For a keystroke that does not change
block structure, Highlight.js re-tokenizes the whole document and produces byte-identical
output for every line but the edited one, so the delta is 1–3 rows. For a structure-changing
edit — typing a `"` or a `/*` — the tail genuinely recolours, the delta blows the existing
repaint ceiling, and the plan correctly falls back to today's full rebuild.

| Where | Change |
| --- | --- |
| `explorer-repaint.js` | Add `highlightRepaintPlan({ previousKeys, nextKeys, rowCount, maxRepaintRows })` → `{ mode: 'skip' \| 'repaint' \| 'full', lines }`, reusing the existing `repaintCeiling()` so the ceiling scales with the document exactly as `sourceRenderPlan()`'s does. DOM-free and Node-tested, like everything else in this module. |
| `explorer-worker-client.js` | Expose a per-line run key off `HighlightLines` computed from the **decoded typed arrays** — `lineRunStarts[line] … lineRunStarts[line + 1]` indexing `classIds` and `lengths` — materializing no run objects and no substrings. A numeric `classId` is local to one worker answer because `compactHighlightMarkup()` assigns ids in first-encounter order, so it is **not comparable across two drafts**: the key must use `classes[classIds[run]]` plus the run length (or an equivalently canonical class-name representation), never the id itself. Materializing 15k lines to compare them would cost what this stage is removing; the CLAUDE.md contract that results are materialized one line at a time must survive. |
| `explorer-edit-overlay.js` `paintExplorerEditUnderlay()` | On the `full: true, runs` path, when the row set is unchanged, replace only the `<code>` cell of each line the plan names, through the existing `explorerSourceRowCodeHtml()` primitive, instead of writing `underlay.innerHTML`. Keep the whole-underlay write for `mode: 'full'` and for every path that is not a settle (mount, a failed splice, a wide paste). |
| `explorer-edit-overlay.js` | Hold a paint-key array aligned with the rows **currently in the DOM** on the pane (`pane._explorerEditUnderlayPaintKeys`), not the previous draft's run map. Seed it at mount from the runs actually painted (a normal `Map` is already materialized; `HighlightLines` uses the compact key above), or with an unknown/fallback sentinel when no real runs were painted. Apply every successful `lineSplicePlan()` to this array as well as the DOM: preserve the prefix and shifted suffix, and insert the sentinel for every rebuilt row. A failed splice or full fallback paint makes every row unknown. Unknown rows always repaint at settle; after a successful partial or full settle, publish the complete next-key array. Clear it in `teardownExplorerEditOverlay()` beside the other overlay slots. This is what represents the real hybrid surface — fallback-coloured rows beside preserved Highlight.js rows — and prevents a same-length edit or an inserted line being skipped merely because its old and new run shapes match. |
| `explorer-edit-overlay.js` | Cache the row model between the splice frame and the settle frame, keyed on draft identity. Today `explorerSourceRowModel()` runs twice per edit over the same draft — once for the splice, once for the settle — for 60k object allocations where 30k would do. |
| `explorer-viewer.js` `explorerSourceLineRecords()` | Give the editor a pane-local cache slot instead of the shared LRU, so transient drafts stop evicting the read-only panes' records. Simplest shape: an optional `cache` argument, with the overlay passing `pane._explorerEditRecordCache`. Worth doing on its own merits — it is a cross-pane defect independent of everything above. |
| `explorer-edit-overlay.js` | Drop `model.records.map(record => record.text)`: hold the record objects and have `lineSplicePlan()` compare `record.text` in place. |

**A partial repaint must still call `repaintExplorerEditFind(index)`**, exactly as the full one
does. Every replaced `<code>` cell detaches the live Ranges the find and the occurrence tint
had painted onto it, while ranges on rows the repaint did *not* touch survive. Re-deriving all
of them is one scan of the draft and is what the tail of `paintExplorerEditUnderlay()` already
does — do not "optimise" it into a partial re-derivation to match the partial paint.

#### What a paint key is allowed to compare

The key for a row is the canonical `(className, length)` sequence from its runs **plus that
row's Markdown `headingLevel`**. `explorerSourceRowCodeHtml()` uses the heading level to add the
`explorer-md-source-heading-*` wrapper even though the underlay disables fold controls, so a run
key alone does not describe the code-cell HTML. The numeric worker `classId` is also deliberately
absent: two answers may assign different ids to the same class name, or the same id to different
class names, when an earlier edit changes first-encounter order.

The previous side of the comparison is `pane._explorerEditUnderlayPaintKeys`, not a run map for
the previous draft. A splice paints its inserted rows through the fallback lexer and leaves the
rest of the old Highlight.js DOM standing; it therefore inserts unknown keys for exactly those
new nodes while shifting the preserved suffix's keys with their nodes. This preserves what the
screen actually contains. In particular, replacing one identifier character with another can
leave the Highlight.js class/length shape unchanged, but the edited row is still unknown and so
must be repainted out of its temporary fallback markup.

#### Why ignoring absolute offsets is legal here

Decoded runs carry **absolute** content offsets (`decodeHighlightResult()` validates
`start + length > text.length`, and `explorerRenderHighlightedRuns()` passes `run.start` into
`explorerCodeSpan()`). Inserting one character therefore shifts every offset below it, and a
naive comparison would report that every line changed.

It does not matter for a preserved row, because **the underlay always renders with empty search ranges** —
`explorerEditUnderlayHtml()` passes `[]`, `spliceExplorerEditUnderlayRows()` passes `[]`, and
the find paints through the CSS Custom Highlight API rather than into the markup. With no
ranges to intersect, `run.start` never reaches the output. A preserved row's text has not moved
with respect to its own DOM node, so its settle output is determined by that text, its canonical
`(className, length)` sequence and its Markdown heading level; the last two are the paint key.
Rows whose text was rebuilt by the splice carry the unknown sentinel and repaint unconditionally.

That is the whole precondition, and it is narrow — **comment it where the key is built.** The
read-only Source view *does* render search ranges into its markup, so the same key would be
wrong there.

Also worth checking while implementing: at this file size the worker is routinely cancelled
before it finishes, so the 180 ms debounce may be tuned for small files and simply be spending
worker starts on large ones. That is tuning, not part of the fix — measure before touching it.

### F5 — verified real, deferred

`spliceExplorerEditUnderlayRows()` walks from the splice point to the end of the document
writing `row.dataset.explorerLine` and the gutter's `textContent`, so Enter near the top of a
20,000-row file is ~40,000 DOM writes in one frame. The finding is real and the code is
unchanged. It is deferred because it is **below the perception threshold on this machine at
15,000 lines** — the original manual test failed to reproduce its own prediction, and the
end-of-file run hitched identically with the loop effectively switched off.

Recording what the review found, so none of it is re-derived wrongly later:

- **The proposed CSS-counter fix is legal but unmeasured.** Contiguity holds because
    `explorerSourceRowModel()` computes `allowMarkdownCollapse` as
    `markdownDocument && foldControls`; the underlay passes `foldControls: false`, so it never
    omits a row. The row's `headingLevel` is still preserved for the code-cell wrapper — only the
    fold control passed to `explorerSourceLineNumberHtml()` is suppressed — which is why Stage
    3's paint key includes it. But inserting a row invalidates the counter for every following
    sibling, so the O(n) moves from JS into style recalc rather than disappearing. Profile it
    before choosing it.
- **Two of the three named call sites are wrong.** Only `explorer-edit-find.js:102`
    (`explorerEditSpanRanges`, whose `root` is `explorerEditUnderlayFor(...)`) reads
    `data-explorer-line` inside the underlay. `:440` is `explorerSourceSelectionCarry()` and
    `:502` is `restoreExplorerSourceSelection()` — both called from `explorer-editor.js` (`:263`
    and `:466`) against the **read-only Source view**, where collapsed Markdown sections omit
    rows and positional indexing would number wrongly. That is precisely the defect the old
    step 8 was written to catch. The editor's own counterparts,
    `explorerEditorSelectionCarry()` / `restoreExplorerEditorSelection()`, work off
    `textarea.value` and touch no row at all.
- **Stale attributes would be left behind.** `explorerSourceRowHtml()` is shared, so underlay
    rows keep emitting `data-explorer-line`; dropping the renumber loop leaves every row below a
    splice carrying a wrong one, and `explorer-search.js:583` / `:664` query
    `[data-explorer-line]` scoped to the **card**, which contains the underlay during an edit.
    Suppress the attribute in the underlay rather than leaving it stale.
- **The counter half is not Node-testable.** The old plan's test — *"the row at line 12,000
    still reports 12,001 to the find, proving the positional read and the counter agree"* — can
    only prove the positional read; the counter is CSS and the stub DOM cannot evaluate it.
    `tests/test_explorer_repaint.py:1075` currently asserts on `row.dataset.explorerLine` and
    `row.firstElementChild.textContent`, and both assertions would have to change.

**If F5 is ever picked up, the low-risk shape is not the counter:** convert `:102` alone to
positional indexing (which also removes a `querySelector` scan of the whole row list per
distinct line), stop emitting `data-explorer-line` on underlay rows, and drop **only** the
attribute write from the loop. That halves the writes and removes the attribute-mutation style
invalidation — the more expensive of the two — with no CSS gamble and no shared-renderer
change.

### Also corrected from the original plan

- F6 named the wrong half of `lineSplicePlan()`: the **prefix** scan is O(document) for an edit
    near the end, but the **suffix** scan is the O(document) half for an edit near the start,
    which is what the original manual test at line 5 actually exercised.
- **The proposed bound on `lineSplicePlan()` is wrong — do not implement it.** Under-counting
    the prefix by `d` inflates `removed + inserted` by `2d`. Capping the prefix walk at
    `document − MAX_SPLICE_ROWS` makes an edit at the very end compute ≈400 against a ceiling of
    200, answering `mode: 'full'` and rebuilding the whole document on every keystroke there —
    the opposite of the intent. There is no sound early-out without knowing the suffix first,
    and the scan is a plain array walk dominated by the record build that follows it. Leave it.

**Tests to add**

- `tests/test_explorer_repaint.py`: `highlightRepaintPlan()` answers `skip` for identical run
    keys, `repaint` with exactly the changed lines for a one-line colour change, and `full` once
    the delta passes the document-scaled ceiling.
- `tests/test_explorer_workers.py`: two `HighlightLines` answers whose class dictionaries assign
    different numeric ids to the same class names produce equal per-line run keys; the same id
    naming different classes produces different keys. Asking for the keys does not increase
    `materialized`, so the lazy per-line-run contract remains true.
- A Node overlay test: a settle whose runs differ on one line replaces exactly one `<code>`
    cell and leaves every other row node identical (compare node identity, as the existing
    splice tests do), and still re-derives the find.
- A Node overlay test for the hybrid surface: a same-length edit whose old and new Highlight.js
    run shapes are identical still repaints the edited row because its splice inserted an
    unknown key; an inserted line shifts the preserved suffix's keys with its DOM nodes rather
    than comparing those nodes against the previous line numbers.
- A Markdown case where only a row's `headingLevel` changes repaints that code cell even when its
    Highlight.js run key does not.
- A settle test for the structure-changing case: runs differing across the tail fall back to the
    whole-underlay write.
- A cache test: painting the underlay N times does not evict a second pane's line records.

### Manual verification

Reuse `typing.js` from the Stage 2 scaffolding (15,000 lines, ~315 KB — deliberately *under*
the large-file tier so the underlay is active, and above `HIGHLIGHT_WORKER_MIN_CHARS` so the
worker path is the one under test):

```bash
seq 1 15000 | sed 's/^/const value/; s/$/ = 1;/' > /c/Users/SasoPC/Desktop/Projects/gv-diff/typing.js
```

1. Open an explorer pane on `C:\Users\SasoPC\Desktop\Projects\gv-diff`, open `typing.js`,
    click **Edit** (✏️).
2. Put the caret at the **end of the last line** and press **Enter** once. Then wait, watching
    the pane rather than the caret.

    - **Before the fix:** roughly half a second to a second later the pane hitches and the
        syntax colours visibly flip. The keypress itself is fine; the cost arrives after it.
    - **After the fix:** the colours still arrive, without the hitch.

3. Repeat at the **end of line 5**. Same result in both builds — the position of the edit is
    not what this stage changes, which is the point.
4. **The delta must not under-repaint.** Put the caret at the very start of line 1 and type
    `/*`. Within about a second the **whole file** must turn comment-coloured, top to bottom.
    Delete the `/*` and confirm it all returns. This is the structure-changing edit that has to
    exceed the ceiling and fall back to the full rebuild; a partial repaint here would leave the
    file half-commented.
5. Hold a letter key down for ~3 seconds at the end of line 5, then release. Typing stays smooth
    (it is the control and must not regress) and the colours settle in once after release.
6. Scroll to the bottom and confirm the last gutter number is **15,001** (a trailing newline
    opens a final empty line) and that the numbers are continuous across every edit made above.
7. `Ctrl+F` inside the editor, search `value14000`, press Enter — the match is found and
    scrolled to. Now type a character somewhere and let the settle fire: the find's marks and
    counter must survive it. This is what the mandatory `repaintExplorerEditFind()` call buys.
8. `Esc` to cancel the edit (do **not** save), re-open `typing.js`, and confirm the Source view
    renders normally.

Step 4 is the one that catches an over-narrow delta, and step 7 the one that catches a partial
repaint that forgot the detached Ranges. Neither has a cheap automated equivalent in a stub DOM.

**If DevTools is to hand**, the clearest evidence is a Performance recording across step 2:
before the fix there is a long task ~0.5–1 s after the keypress containing
`renderExplorerSourceLines` and an `innerHTML` parse; after it, that task is gone and only a
handful of code cells are written.

### Documentation

**User-facing — yes** (a felt latency change).

- **`CHANGELOG.md`** (Unreleased):

    > **(perf) Editing a large file no longer stalls a moment after you stop typing.** Typing in
    > the in-place editor was made cheap a while back: only the lines you actually changed are
    > rebuilt. But the pass that fills the real syntax colours back in — which runs a beat after
    > the last keystroke — still rebuilt every line in the file to do it, so a long file hitched
    > about a second after each edit, just as the colours arrived. Only the lines whose colours
    > genuinely changed are repainted now, which for an ordinary keystroke is one of them.

    The entry deliberately does **not** name Enter. The original draft (*"Pressing Enter in a
    large file no longer stutters"*) was written against F5 and would over-claim: Enter is not
    what was measured, and F5 is not what ships.

- **`README.md`** — no change. Nothing it claims changes.

- **Guardrails** — none new. The rule this stage applies, *a repaint is not a rebuild*, is
    already written in §3; the settle was a missing call site against it. Add the shift-invariant
    key's precondition (the underlay renders with empty search ranges, so `run.start` never
    reaches the output) as a code comment where the key is built — it is a local invariant, and a
    dangerous one to generalise to the read-only Source view.

### Status — landed 2026-08-23 ✅

`make check` equivalent: **1960 tests OK** (1952 before the stage + 8 below; 9 platform skips),
ruff clean. Every claim in the plan was re-checked against the code before implementing, and all
of them held: `paintExplorerEditUnderlay()` really did build a whole row model per animation
frame and `.map()` a second whole-document array; the shared line-record LRU really is
`min(max(panes, 1) × 2, 8)`, so **2 slots** with one explorer pane open; `explorerMarkedEscHtml()`
really does return `escHtml(value)` outright on empty ranges, which is the precondition the
shift-invariant key rests on; `explorerSourceRowCodeHtml()` really does add the heading wrapper
independently of `foldControls`; and `explorer-worker-core.js` really does assign class ids in
first-encounter order, so the key uses class **names**.

**Code — as planned, minus two items (below).**

| Where | What landed |
| --- | --- |
| `explorer-worker-client.js` | `HighlightLines.lineKey(line)` — the `(length, className)` sequence read straight off the typed arrays. No run objects, no substrings, nothing added to the cache, so `materialized` is unmoved. |
| `explorer-repaint.js` | `highlightRepaintPlan({ previousKeys, nextKeys, rowCount, maxRepaintRows })` → `skip` / `repaint` / `full`, reusing `repaintCeiling()`. It also answers `full` when the delta covers **every** row: m per-row parses can never beat one bulk write at m = all, and that rule is what keeps the small-file settle behaving exactly as before. |
| `explorer-edit-overlay.js` | `pane._explorerEditUnderlayPaintKeys` — seeded at mount from the runs actually painted, spliced with the rows (unknown sentinel for what the splice built, preserved keys shifted with their nodes), republished after every paint, cleared on teardown. `repaintExplorerEditUnderlayColours()` replaces only the named `<code>` cells, through the existing `explorerSourceRowCodeHtml()`. |
| `explorer-edit-overlay.js` | `paintExplorerEditUnderlay()` now builds **one** model instead of two on the full path, and `full` means "this paint may use real runs" rather than "rebuild everything". |
| `explorer-viewer.js` | `explorerSourceLineRecords(content, cache)` — optional caller-owned slot; the overlay passes `pane._explorerEditRecordCache`, so a moving draft neither hits the shared LRU nor evicts the panes that do. |

Two follow-on cleanups the plan did not name: `explorerEditUnderlayHtml()` and
`explorerEditUnderlayLines()` both became unreachable once the mount and the paint emit rows from
a model they already hold, so they were removed (Guardrail 5) and their documentation folded into
`explorerEditUnderlayModel()` / `explorerEditUnderlayRowsHtml()`.

**Two plan items deliberately not implemented.**

- **"Cache the row model between the splice frame and the settle frame, keyed on draft
    identity."** The two frames build models with *different* token maps — null for the splice, the
    worker's answer for the settle — so a draft-keyed model cache misses every time. The expensive
    half of that model is the line records, which is exactly what the pane-local record cache now
    holds, so this item collapses into that one.
- **"Drop `model.records.map(record => record.text)`."** Implementing it means changing
    `lineSplicePlan()`'s DOM-free contract (its Node tests pass string arrays) and adding a
    per-element branch to a hot comparison loop, to save one array of *existing string
    references* per frame. Poor trade against the rest of the stage; left alone.

**F5 remains deferred**, exactly as this stage's re-aiming concluded.

**Tests — all eight added, and each verified to fail against the pre-fix code** (by temporarily
disabling the relevant edit, then restoring it):

| Test | Pre-fix failure |
| --- | --- |
| `test_explorer_repaint.py::RepaintPolicyTestCase::test_a_settle_repaints_only_the_rows_whose_colour_moved` | n/a — new policy function |
| `test_explorer_workers.py::test_a_line_key_compares_across_answers_without_materializing_runs` | n/a — new method |
| `…::EditUnderlayRepaintTestCase::test_a_settle_replaces_only_the_code_cells_whose_colour_moved` | the whole underlay was rewritten; every row node replaced |
| `…::test_an_edit_whose_run_shape_did_not_move_still_repaints_its_row` | same |
| `…::test_an_inserted_line_shifts_the_preserved_keys_with_their_nodes` | same |
| `…::test_a_markdown_row_repaints_when_only_its_heading_level_moved` | same |
| `…::test_a_structure_changing_edit_still_rebuilds_the_whole_underlay` | passes either way — it pins the fallback the ceiling must keep |
| `…::test_typing_does_not_evict_another_panes_line_records` | the other pane's records were evicted by the draft churn |

The existing `EditUnderlayRepaintTestCase` harness grew a `mount(runs)` helper (the underlay as
`mountExplorerEditOverlay()` leaves it, paint keys included) and optional `language` / `lines`
parameters. Its four pre-existing tests pass on their original assertions.

**Documentation.** The CHANGELOG entry landed as written in the plan — deliberately not naming
Enter, since Enter is not what was measured. README unchanged. The plan said "Guardrails — none
new", and that is still right in the sense that no new *rule* was invented; but the existing §3
bullet **"The in-place editor's underlay does not tokenize per frame"** ended with "and repaints
in full once through a one-shot settle debounce after typing stops", which this stage makes false.
It has been extended in `CLAUDE.md` and `AGENTS.md` (both gitignored, per the note at the top of
this document) to name the settle as a repaint and to record the three preconditions the key rests
on — what the previous side of the comparison is, what the key describes, and why ignoring
absolute offsets is legal here and nowhere else.

**Manual verification: pending.** The eight steps below have not been run yet; steps 4 (the `/*`)
and 7 (the find surviving a settle) are the two with no automated equivalent.

---

## Stage 4 — Work that outlives the moment it was for ✅ *landed*

**Findings:** F11 (Low), F12 (Low), F4 (Medium)
**Risk:** low-to-medium. The input reorder is small. The explorer cleanup must distinguish
discarding a pane from abandoning one render surface inside a pane that remains live; getting
that distinction wrong can run a closed pane's queued readers against another visible pane.

### Problem

**F11.** `explorerRunSourceRenderJob()` captures `pane` and the `code` element. A group switch
suspends the job, and a cached-group close at least stops its frames (although it currently uses
the wrong callback-flushing operation). But a **single pane
replaced in place** (`replaceSessionPaneMode` at `terminals.js:6356`, and pane close) never
clears `_explorerSourceRenderJob`. The captured `code` is detached, so
`explorerRenderedSourceContainer(code) !== onScreen` stays false and the stand-down branch never
fires; the build runs to completion appending rows into a tree nobody can see, competing for
frames with the pane that just replaced it.

**F12.** `explorerRequestSignal(pane, slot)` stores one `AbortController` per slot on
`pane._explorerRequestAborters` and no pane-disposal path clears the whole map. Seven slots are
in use (`file`, `preview`, `diff`, `diffParse`, `highlight`, `editHighlight`, `changeMarks`). The
memory is trivial; what matters is that a pane torn down mid-flight leaves fetches running and
can leave its worker job running, so a closed pane can still hold resources on a document nobody
is looking at. `releaseExplorerResourcesIfIdle()` sometimes masks the worker half by terminating
the whole pool when no explorer pane remains anywhere, but it does not abort fetches and it does
nothing when another visible or cached explorer pane keeps the shared pool alive.

**F4.** `web/api.py:3490` runs `_track_terminal_agent_input(...)` **before**
`_send_connection_input(...)`. Its agent-promotion branch calls `effective_directory()`, which
for a remote pane with a known `shell_pid` and no shell-integration observation falls through to
`_remote_process_cwd()` — `transport.open_session(timeout=3.0)` plus a bounded `recv` on a fresh
exec channel. That sits between the user pressing Enter on `claude` and Enter reaching the shell,
on an `async_mode="threading"` handler thread. Python Socket.IO defaults `async_handlers=True`,
so later events for the same client can run in separate threads rather than waiting behind this
one: at minimum the submitted Enter is delayed, and a later input handler can overtake it and
write newer input first. Lock discipline is already correct; only the ordering is wrong.

### Re-verified against the code — 2026-08-23

All three findings still reproduce. The implementation shape needs the corrections below.

**F11 — real, and the plan's call-site list is incomplete.** `explorerRunSourceRenderJob()`
(`explorer-viewer.js:4420`) captures `pane` and `code`; its stand-down branch asks
`explorerRenderedSourceContainer(code) !== onScreen`, and a **detached but intact** subtree still
answers with the same container — so the branch cannot fire for a pane that was replaced whole,
exactly as the finding says. But a pane close does not reach `closeTerminalPane()`'s own body:
it goes `closeTerminalPane()` → `initialLoad()` → `buildGrid()` → **`teardownCurrentGrid()`**
(`terminals.js:4945`), which is the real choke point and also covers the last-pane close and every
group switch that does not cache. It disposes the xterm instances and never touches
`_explorerSourceRenderJob`. So the two call sites are **`teardownCurrentGrid()` and
`replaceSessionPaneMode()`**, not `replaceSessionPaneMode()` and a pane-close path.

One neighbouring path is already right and must be left alone: a group switch that *caches* the
outgoing group **suspends** rather than discards (`terminals.js:904`/`:920`), so the job resumes
with its position and queued readers intact. The cached-group **close** at `terminals.js:1138`
does need to change: it abandons the render job, which executes queued readers, when this path is
actually discarding the whole pane.

**F12 — real, and there are seven slots, not six.** The original draft missed `diff`
(`explorer-diff.js:1163`) beside `diffParse` (`:902`); the full set is `file`, `preview`, `diff`,
`diffParse`, `highlight`, `editHighlight`, `changeMarks`. A `cancelExplorerRequestSlots(pane)`
that walks the map catches all of them regardless. Two checks that make the fix safe both pass:
no disposal path walks and clears the whole `_explorerRequestAborters` map (only individual slots
are deleted), and **every one of the seven callers already handles `AbortError`** — there are seven
`explorerIsAbortError()` guards, one per slot — because `explorerRequestSignal()` already aborts
the previous controller on supersession. So cancelling every slot at teardown cannot produce an
unhandled rejection or a console line (guardrail 9). Aborting `highlight`/`editHighlight` does
genuinely stop the thread: `WorkerPool._abort()` terminates the worker running that job.

`releaseExplorerResourcesIfIdle()` does terminate the whole worker pool when the last explorer
pane disappears, so the worker leak is not unconditional. It is not a pane cleanup, though: it
does not abort fetches, and a visible or cached sibling explorer pane keeps the pool alive. The
three explicit disposal paths still need per-pane cancellation.

**F4 — real; the concurrency explanation needed correction.** It is
`web/api.py:3490`/`:3491`. The chain is unchanged: `_track_terminal_agent_input()` → the promotion branch →
`effective_directory(session_id, session)` → `_process_reported_cwd()` → `_remote_process_cwd()`,
which is `transport.open_session(timeout=REMOTE_CWD_READ_TIMEOUT)` with
`REMOTE_CWD_READ_TIMEOUT = 3.0` plus a bounded `recv` loop, all on the handler thread between the
keystroke and the shell. `async_mode="threading"` does not serialize later events behind that
handler: Python Socket.IO's default `async_handlers=True` runs them in separate threads, so newer
input can overtake the blocked Enter. The reorder is behaviour-preserving where the send succeeds
— `_send_connection_input()` reads nothing the tracker produces, and the tracker takes
`connection_lock` on its own while the send takes no lock, so the order has no locking
consequence. **One deliberate change to state:** if the send raises, the tracker no longer runs,
so a promotion cannot be recorded for input that never reached the shell. That is more correct.

**Two testability corrections.**

- The plan's F11 target — *"`tests/test_explorer_source_frame.py`: a frame-sliced build whose
    panel is replaced by a non-explorer surface stops on its next slice"* — describes a mechanism
    the fix does not use. The fix stands the job down **eagerly**, when the pane is replaced; the
    slice-time branch it describes already exists and is already covered. That file also loads only
    `explorer-viewer.js`, `explorer-tiers.js`, `explorer-repaint.js` and `explorer-tabs.js` — and
    **no Node harness anywhere loads `terminals.js`**, which is only ever asserted as source text in
    `test_api.py`.

    The shape that avoids both problems: put the teardown in `explorer-viewer.js` as **one**
    exported function, `explorerReleasePaneWork(pane)`, and have `terminals.js` call that single
    name from `teardownCurrentGrid()`, `replaceSessionPaneMode()` and the cached-group close. F11
    and F12 then land as one call per disposal site, the behaviour is Node-testable in
    `test_explorer_repaint.py`'s existing `ChunkedSourceBuildTestCase` harness (which already
    drives `explorerAbandonSourceRenderJob`), and the `terminals.js` side needs only a cheap
    contract-level check for the call.

- **F4's test can be behavioural, so F14's tension does not arise.** Patch
    `api._send_connection_input` and `api._track_terminal_agent_input` to append to a list, stub one
    `ssh_connections` entry, and call `api.handle_terminal_input({...})` directly — the order is
    then *observed at runtime* rather than read out of the source. The existing agent-promotion
    tests (`test_api.py:12350` onward) already provide the surrounding session setup.

**Queued-reader disposition — settled.** `explorerAbandonSourceRenderJob()` **executes** queued
readers. Those readers close over `index` and later re-read global `terminals[index]`. A cached
group close runs while `terminals` belongs to a different visible group, so flushing there can
apply a closed pane's scroll/search work to an unrelated pane in the same slot. A pane that is
being discarded has no reader left to satisfy. `explorerReleasePaneWork(pane)` must therefore
cancel the frame and **clear the callback queue without invoking it**, then abort and remove every
request controller. `explorerAbandonSourceRenderJob()` remains the live-pane operation for the
editor, tab, large-tier and other surface replacements whose pane survives and whose readers do
still need to run.

### Fix

| Where | Change |
| --- | --- |
| `explorer-viewer.js` | Add `cancelExplorerRequestSlots(pane)` that aborts every controller in `pane._explorerRequestAborters` and removes the map. Add exported `explorerReleasePaneWork(pane)` for **pane disposal**: cancel `_explorerSourceRenderJob`, clear `_explorerSourceRenderCallbacks` without executing them, then cancel all seven request slots. Do not implement this helper with `explorerAbandonSourceRenderJob()`, whose callback-flush semantics belong to a live pane. |
| `terminals.js` `teardownCurrentGrid()` | Release each outgoing explorer pane before clearing `terminals`/`sessionIds`. |
| `terminals.js` `replaceSessionPaneMode()` | First verify the target card and wrapper exist; only then release the outgoing explorer pane, immediately before dispatching to the replacement function. A failed precondition must leave the still-visible pane's work intact. |
| `terminals.js` `dropCachedGroupView()` | Replace the cached explorer pane's `explorerAbandonSourceRenderJob()` call with `explorerReleasePaneWork()`. This path discards rather than restores the pane, and global `terminals[index]` belongs to another group. |
| `web/api.py:3490` | Send first, track after: `_send_connection_input(...)` then `_track_terminal_agent_input(...)`. Nothing in the tracker feeds the send — it only reads the sanitized text — so the swap is behaviour-preserving for everything except the latency and the failure case noted above. |

F4's former "observation only, no I/O" alternative is **not equivalent**. It would remove the
latency, but in the exact remote fallback case it would also discard the only observed directory
available when the agent starts, weakening Save Workspace restore accuracy. Keep the send-first
reorder unless losing that observation becomes an explicit product decision.

**Tests to add**

- `tests/test_explorer_repaint.py` (`ChunkedSourceBuildTestCase`, which already drives
    `explorerAbandonSourceRenderJob`): releasing a pane's work stops a frame-sliced build on the
    spot, removes its queued readers **without executing them**, and aborts every one of the seven
    request slots. **Not** `test_explorer_source_frame.py`, and not "stops on its next slice" —
    see the re-verification above.
- `tests/test_api.py`: a contract-level check that `terminals.js` releases the outgoing explorer
    pane's work from **all three** disposal paths: `teardownCurrentGrid()`,
    `replaceSessionPaneMode()` and `dropCachedGroupView()`. This one is a served-asset assertion
    because no Node harness loads `terminals.js`; keep it to the call name, per F14.
- `tests/test_api.py`: the input handler's call order, **observed at runtime** — patch
    `api._send_connection_input` and `api._track_terminal_agent_input` to record into a list, stub
    one `ssh_connections` entry, call `api.handle_terminal_input(...)`, assert the order. No
    source-order check needed. Add the failure case too: when send raises, tracking is not called
    and no promotion is recorded for input the shell never received.

### Manual verification

F12 is timing-sensitive manually; the automated controller test is its primary evidence.

1. Open an explorer pane on `C:\Users\SasoPC\Desktop\Projects\gv-diff`, open DevTools →
    **Performance**, and start recording **before** opening `typing.js` (the 15,001-line,
    below-large-tier file from the scaffolding). Use CPU throttling if the paint and highlight job
    finish too quickly to catch.
2. Open `typing.js` and immediately use the pane's mode button to switch to a terminal — do **not**
    let the file finish painting first. Stop after ~3 seconds.

    - **Before the fix:** frames after the mode-switch response still show
        `explorerAppendSourceRows` / `insertAdjacentHTML` work, and a highlight worker can remain
        occupied — the pane is filling and colouring a tree that is no longer on screen.
    - **After the fix:** the row build stops at the switch and no explorer worker remains on that
        document.

3. Optional second render-path check: repeat with `huge.txt`, again switching before it finishes.
    That file takes the plain large-tier chunk pacer, so expect `insertAdjacentHTML` work but no
    syntax-highlight worker. It verifies that the shared source-render job is cancelled for both
    row and plain-chunk builds; it is not the F12 worker test.

4. **F4 — SSH input.** On an SSH pane with **Shell integration** on (App Settings), type `claude`
    and press Enter.

    - After the fix, Enter must echo immediately. The exact defect needs a remote shell that
        reported its pid but never emitted a directory, which is hard to force deliberately — so
        an ordinary shell-integration run is a *no-regression* check, and the fix's primary evidence
        is the behavioural call-order test.
    - Confirm the pane still promotes to an agent pane (the header title and the 🔄 dropdown change
        as before) and that a **Save Workspace** taken while the agent runs still restores it in the
        directory the agent was started in. That is the behaviour the reorder must not break.

### Documentation

**User-facing — yes.** F11 is a visible stutter on a mode switch; F4 is an input delay.
F12 is internal.

- **`CHANGELOG.md`** (Unreleased), two entries because the changes are unrelated:

    > **(perf) Switching a pane away from a large file stops the work it was doing.** A file big
    > enough to be painted over several frames kept painting after you switched the pane to a
    > terminal or a browser preview, into a view nobody could see and in competition with the one
    > that had just replaced it. Background syntax colouring for that file kept running too. Both
    > now stop when the pane does.

    > **SSH agent commands reach the shell before fallback directory observation.** When shell
    > integration had reported a remote shell pid but not its directory, recognizing a manually
    > started agent could spend up to the bounded remote-CWD timeout before forwarding Enter, and
    > a later input handler could overtake it. GridVibe now sends the input first, then records the
    > agent metadata; successful promotion and Save Workspace directory restore are unchanged.

- **`README.md`** — no change.
- **`AGENTS.md` / `CLAUDE.md` guardrail wording** — no new guardrail, but correct the existing
    Source-build rule: a cached group switch suspends/resumes and retains readers; a surface
    replacement inside a pane that remains live abandons and executes its readers; disposal of
    the whole pane cancels its work and drops its queued readers without executing them.


### Status — landed 2026-08-23 ✅

`make check` equivalent: **1965 tests OK** (1960 before the stage + 5 below; 9 platform skips),
ruff clean. All three findings were re-checked against the code before implementing and all three
held, including the two corrections this section had already recorded: the disposal choke point is
`teardownCurrentGrid()` (which `closeTerminalPane()` reaches through `initialLoad()` →
`buildGrid()`) and not a pane-close body of its own, and there are **seven** request slots, not
six. Two preconditions for cancelling every slot were re-confirmed: no path walks and clears the
whole `_explorerRequestAborters` map, and all seven callers already guard `explorerIsAbortError()`,
so blanket cancellation cannot produce a console line (guardrail 9).

**Code — as planned.**

| Where | What landed |
| --- | --- |
| `explorer-viewer.js` | `cancelExplorerRequestSlots(pane)` — aborts every controller in `pane._explorerRequestAborters` and drops the map. |
| `explorer-viewer.js` | `explorerReleasePaneWork(pane)` — pane **disposal**: cancel the frame, clear `_explorerSourceRenderCallbacks` **without executing them**, then cancel all seven slots. Written out longhand rather than on top of `explorerAbandonSourceRenderJob()`, whose callback-flush semantics belong to a pane that stays live. |
| `terminals.js` `teardownCurrentGrid()` | Releases each outgoing explorer pane inside the existing dispose loop, before `terminals`/`sessionIds` are cleared. |
| `terminals.js` `replaceSessionPaneMode()` | Card/wrapper precondition checked first — the same one each `replacePaneWith*()` already refuses on — then the outgoing explorer pane released, then the dispatch. A failed precondition still returns `false` and leaves the visible pane's work untouched, exactly as before. |
| `terminals.js` `dropCachedGroupView()` | `explorerAbandonSourceRenderJob()` → `explorerReleasePaneWork()`. |
| `web/api.py` | Send first, track after, with the reason and the one deliberate state change recorded in a comment at the call site. |

`captureCachedPaneUiState()`'s `explorerSuspendSourceRenderJob()` was left alone, as the
re-verification required: a cached switch is not a disposal.

**Tests — five added, each verified to fail against the pre-fix code.**

| Test | Pre-fix failure |
| --- | --- |
| `test_explorer_repaint.py::ChunkedSourceBuildTestCase::test_releasing_a_pane_drops_its_readers_and_aborts_its_requests` | n/a — `explorerReleasePaneWork` did not exist |
| `test_api.py::ExplorerPaneDisposalTestCase::test_all_three_disposal_paths_release_the_outgoing_explorer_pane` | n/a — the call name did not exist |
| `…::test_a_cached_group_switch_still_suspends_rather_than_releases` | passes either way — it pins the neighbouring path the fix must not touch |
| `test_api.py::TerminalInputSendOrderTestCase::test_input_is_sent_before_agent_tracking_runs` | observed `['track', 'send']` |
| `…::test_a_failed_send_records_no_agent_promotion` | the tracker ran before the failing send, so a promotion was recorded for input the shell never received |

The Node test runs against real `AbortController`s (added to the existing `ChunkedSourceBuildTestCase`
sandbox), so "was this slot aborted" is the signal's own answer rather than a stub's bookkeeping.

**One pre-existing assertion changed.**
`test_api.py::test_terminals_page_caches_group_views_across_switches` asserted
`explorerAbandonSourceRenderJob(terminal);` in `dropCachedGroupView()` — the behaviour this stage
deliberately replaces. It now asserts `explorerReleasePaneWork(terminal);`, with the comment above
it rewritten to say why a cached-group *close* is disposal while a cached-group *switch* is not.
No other existing assertion moved.

**Documentation.** Both CHANGELOG entries landed; the second was reworded slightly against the
draft above — "bounded remote-directory timeout" rather than naming the constant, and "a later
keystroke could overtake it" rather than describing handler threads, since neither detail is
visible to the reader. README unchanged. No new guardrail: the existing §3 bullet **"Work that
belongs to a detached pane is suspended"** ended with a two-case rule that this stage makes
incomplete, so it was rewritten in `CLAUDE.md` and `AGENTS.md` (both gitignored, per the note at
the top of this document) to state the three cases separately — suspend/resume retains the queue,
a live-pane surface replacement abandons and executes it, and disposal cancels the work and drops
the queue unexecuted — and to record why disposal must also abort the request slots.

**Manual verification: pending.** The four steps below have not been run yet. F12's primary
evidence is the automated controller test, as this section already noted.

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
    cd /c/Users/SasoPC/Desktop/Projects/gv-diff && git add -A && git commit -m huge
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

3. **F13.** Create `C:\Users\SasoPC\Desktop\Projects\gv 100%done` (or `/tmp/100%done` on a
    remote host), `cd` into it from a terminal pane, then click **📁 ⇄ 💻**.
    - **Before the fix:** the explorer opens on the wrong directory, or the pane shows the
        "could not tell where the terminal was" notice.
        ME: could not replicate the issue, could open on the gv 100%done with no problem, albe it its empty so i see directory is empty on preview
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
| 1 · Explorer root & launch floor ✅ | F1, F2, F15 | High | folded into 1 existing entry | 3 edits | 2 clauses |
| 2 · Large-content fidelity ✅ | F3; F10 non-issue | Medium | 1 entry | — | 1 clause |
| 3 · Underlay settle repaint ✅ | F6; F5 deferred | Medium | 1 entry | — | 1 clause extended |
| 4 · Work that outlives its pane ✅ | F11, F12, F4 | Low-medium | 2 entries | — | 1 clause corrected |
| 5 · Loose ends & docs | F7, F8, F9, F13, F14, §4 | Low | 3 entries | 5 edits | — |
