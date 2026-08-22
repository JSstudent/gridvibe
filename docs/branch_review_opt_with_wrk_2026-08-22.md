# Branch review — `szua_gridvibe-opt` → `szua_gridvibe_opt-with-wrk`

Point-in-time audit, 2026-08-22, at `a596f9f`. Merge base `e9eb029`.

> This is an **audit**, not a contract. Per the working rules it is never cited from
> code, tests, or the maintained documents. Anything here that should outlive the
> document belongs in the Regression Guardrails list in `CLAUDE.md` / `AGENTS.md`.

---

## 1. What was reviewed

26 commits, 63 files, **+16,659 / −4,376**. Documentation diffs were read only where
they explained a code change.

| Theme | Where |
| --- | --- |
| Large-file / large-diff presentation tiers, worker offload | `explorer-tiers.js`, `explorer-worker*.js`, `explorer-diff.js` |
| Repaint / scroll / frame-sliced render restructuring | `explorer-repaint.js`, `explorer-scroll{,-adapter}.js`, `explorer-viewer.js`, `explorer-edit-overlay.js` |
| Working-directory observation (shell integration) | `web/terminal_cwd.py` (new), `web/terminal_io.py`, `sessions/manager.py`, `web/runtime_state.py` |
| Git sidebar scope (pin / follow) + Unstage All | `web/explorer.py`, `web/api.py`, `explorer-git-sidebar.js` (new, move) |

**Baseline health:** `python tests/run_tests.py` → 1947 tests, **OK** (9 platform skips).
`python -m ruff check .` → **All checks passed**. Node 22.18 and Git are present, so the
Node-executed suites really ran.

---

## 2. Findings

Severity is about user-visible consequence, not effort.

### F1 · High · **Verified** — a *derived* explorer root is persisted as a *configured* one

`web/api.py:3013` (SSH branch) and `web/api.py:3075` (local branch):

```python
root_directory = _resolve_explorer_open_root(configured_root, next_directory, …)
…
explorer_root_directory=root_directory,
explorer_root_configured=bool(configured_root),   # ← describes the wrong value
```

`_resolve_explorer_open_root()` returns `configured_root` **only while it still holds the
observed working directory**. Once the terminal has walked outside it, the function
correctly falls through to the derived root (repo worktree, cwd, or the launch floor) —
but the flag is still computed from `configured_root`, so the derived root is stored
wearing the configured flag.

Reproduced against the real routes:

```
after launch: …\tmp\alpha   True     # chosen root
to terminal : …\tmp\alpha   True
(shell walks to …\tmp\beta, switch back to explorer)
back to expl: …\tmp\beta    True     # ← derived root, flagged configured
EXPECT       : …\tmp\beta    False
```

This is exactly the failure `explorer_root_configured` was introduced to prevent — the
field's own docstring in `sessions/manager.py` says a derived root "confines the live
explorer without ever becoming a pin the next switch obeys". It also propagates:
`_configured_explorer_root_directory()` now returns it, `_resolve_pane_terminal_directory()`
hands it back out on the way to terminal mode, and `_snapshot_session()` writes it into
`runtime_state.json`, so the pane's originally chosen root is lost across a restart.

**Fix:** `explorer_root_configured=bool(configured_root) and root_directory == configured_root`
in both branches. `split_session` (`web/api.py:2705`) is already correct — there
`root_directory` *is* the configured root.

---

### F2 · High · **Verified** — `launch_directory` does not survive a restart, so the widen-guard floor moves

`TerminalSession.launch_directory` is documented as "the one thing here that never moves
afterwards", and `_resolve_explorer_open_root()` reads it as the floor that stops a
repository root widening the explorer above the directory the user picked.

It is **not persisted**: absent from `to_dict()`, from `_SESSION_SNAPSHOT_FIELDS`, and from
the `fields` dict in `SessionManager.install_group()`. Combined with `_snapshot_session()`
deliberately writing `current_directory` into the snapshot's `directory` slot, the floor
collapses onto wherever the pane happened to be:

```
live  launch_directory     : C:\repo
snapshot directory         : C:\repo\a\b        # observed cwd, by design
snapshot has launch?       : False
restored launch_directory  : C:\repo\a\b        # ← floor moved
```

Consequence — same pane, same directory, two answers:

| | floor | explorer root chosen |
| --- | --- | --- |
| before restart | `C:\repo` | `C:\repo` (repo root) |
| after restart | `C:\repo\a\b` | `C:\repo\a\b` |

That is the "one gesture, two different roots on two different days" symptom the whole
working-directory change set was written to remove, reintroduced through the restart.

**Fix:** add `launch_directory` to `to_dict()` and `_SESSION_SNAPSHOT_FIELDS` (it is a
second directory field, but it answers a different question than `directory` and
`__post_init__` already treats absent as "not stated", so old snapshots degrade cleanly).

---

### F3 · Medium · **Verified** — the handwritten side-by-side diff parser drops `--` / `++` content lines

`web/static/js/explorer-worker-core.js`, `parseSideBySideDiff()`:

```js
if (line.startsWith('-') && !line.startsWith('---')) { …delete… }
if (line.startsWith('+') && !line.startsWith('+++')) { …add… }
```

A removed line whose *content* begins with `--`, or an added line whose content begins
with `++`, is indistinguishable from a `---`/`+++` file header and is silently dropped —
and because the line counter is not advanced, every line after it is numbered wrong on
both sides. Confirmed in Node:

```
@@ -1,3 +1,3 @@ / " keep" / "---legacy-flag" / "+++new-flag" / " tail"
→ rows: hunk, context(1,"keep"), context(2,"tail")     # two lines gone, "tail" is line 3
```

Real triggers are common: `---` YAML front-matter delimiters and horizontal rules in
Markdown, `--flag` in argument files, `++i;` in C/C++/Java/JS.

The code itself is a **pure move** from `explorer-viewer.js` (the same guard stood at
lines 3909/3957/4520 on the base branch), so this is carried-over rather than newly
written. What changed is the blast radius: the handwritten renderer used to be reached
only when Diff2Html was unavailable, and it is now the **primary** renderer for the
`large` diff tier (> 160 KiB or > 2,500 lines). A pre-existing latent bug became a
routinely reachable one.

**Fix:** anchor on position rather than prefix — a `---`/`+++` header only ever appears
before the first `@@`, and the parser already tracks that with `if (!oldLine && !newLine) return;`.
Once inside a hunk, `-`/`+` is unambiguous, so the `startsWith('---')` guards can go.

---

### F4 · Medium — a blocking SSH channel open sits on the terminal-input path

`web/api.py:3481`:

```python
_track_terminal_agent_input(session_id, connection, sanitized_input)
_send_connection_input(connection, sanitized_input)
```

The tracking call runs **before** the keystroke is forwarded. Its agent-promotion branch
(`web/terminal_io.py:1180`) calls `effective_directory()`, which for a remote pane with a
known `shell_pid` but no shell-integration observation falls through to
`_remote_process_cwd()` — `transport.open_session(timeout=3.0)` plus a bounded `recv` on
a fresh exec channel. Worst case that is several seconds between the user pressing Enter
on `claude` and Enter reaching the shell, on the `async_mode="threading"` handler thread
that also serves that pane's later input.

Narrow in practice (it needs the remote hook to have reported a pid without ever emitting
an OSC 7, and with `terminal.shell_integration` off no pid is captured at all, so
`_remote_process_cwd` returns immediately). Lock discipline is correct — the call is made
*after* `connection_lock` is released. But the ordering is free to fix.

**Fix:** send first, track after; or pass an explicit "observation only, no I/O" mode on
the promotion path, which is all the comment there actually wants.

---

### F5 · Medium — the editor underlay renumbers every row below the edit

`explorer-edit-overlay.js`, `spliceExplorerEditUnderlayRows()`:

```js
if (plan.inserted !== plan.removed) {
    for (let at = plan.start + plan.inserted; at < container.children.length; at += 1) {
        row.dataset.explorerLine = String(at + 1);
        gutter.textContent = String(at + 1);
    }
}
```

Typing plain characters keeps the counts equal and costs nothing. **Enter, backspace-join,
and any paste containing a newline** walk from the splice point to the end of the
document — two DOM writes per row. Pressing Enter near the top of a 20,000-row file is
~40,000 DOM writes inside one animation frame, which is the shape of cost the splice was
introduced to remove.

**Fix:** render the gutter from a CSS counter (`counter-reset` on `.explorer-source-lines`,
`counter-increment` per row, `content: counter(…)` on the gutter). Numbering then follows
the DOM for free and the splice becomes genuinely O(edit). `data-explorer-line` still has
to be maintained for the rows the splice touched, but not for the tail.

---

### F6 · Medium — the underlay's per-frame cost is still O(document)

Per rAF while typing, `paintExplorerEditUnderlay()` does all of:

1. `explorerSourceRowModel(draft, …)` → `explorerSourceLineRecords()` (full walk, one
   record object per line) **plus** a `rows` array with one object per line;
2. `model.records.map(record => record.text)` — a second full array;
3. `lineSplicePlan(previous, lines)` — a prefix scan that, for an edit near the *end* of
   the file, compares every line string;
4. a miss + `unshift` into the global `_explorerLineRecordCache`.

The Highlight.js pass genuinely left the frame (that is the win). The document walks did
not, so the claim "the cost of a keystroke [is no longer] O(document)" holds for
tokenization only. The tier caps the underlay at 20,000 rows / 4 MiB, so it is bounded —
but it is the reason typing in a 15,000-line file still costs more than it should.

Related: `_explorerLineRecordCache` holds 8 entries shared page-wide. During typing every
frame inserts one transient draft, so within half a second the cache contains nothing but
dead intermediate drafts and every read-only pane's records have been evicted. Self-healing,
but the cache is doing the opposite of its job while the editor is open. Worth keying the
editor's records on the pane instead of putting them in the shared LRU.

---

### F7 · Low/Medium — Find is disabled on Preview and Diff because the *Source* buffer is large

`explorerPaneAllowsFind()` keys only on `explorerPaneSourceTier(pane)`, and
`sourceTierAllows('large', 'find')` is `false`. The header hosts one find input for all
three panels, so opening a 5 MiB source file also takes Find away from the **Diff**
panel — whose content is capped at 256 KiB by `EXPLORER_GIT_DIFF_MAX_BYTES` and could
answer perfectly well.

The comment defends the decision ("Find is a capability of the tier, not of the view"),
and the notice does say so — but the notice is painted in Source, and the reader who
switches to Diff and finds a dead control has no explanation in front of them.

**Options:** either let Find live per panel (Diff and Preview keep it; Source does not,
with the existing notice), or repeat the "Find is unavailable" line on the Diff/Preview
panels while the source tier is `large`. The first is better; the second is a one-liner.

---

### F8 · Low — the lazy Markdown preview can dead-end on "Rendering preview…"

`ensureExplorerPreviewLoaded()` sets `preview.textContent = 'Rendering preview...'`, then
returns `null` without repainting when `data.state_revision` disagrees with
`pane._explorerFileStateRevision`. The comment relies on the open-file change listener to
notice the same revision move and reload — but `explorer-git-watch.js` suspends itself
after repeated failures (`_explorerGitWatchSuspended`), and nothing else repaints that
panel. The reader is then left looking at a loader with no retry, which guardrail 8
("failure states need a retry affordance") exists to prevent.

Low probability — it needs a write landing inside the window between the file GET and the
preview GET *and* a suspended watcher. Cheap fix: paint a one-line "The file changed while
the preview was rendering — Refresh" instead of returning silently.

---

### F9 · Low — dead payload fields (guardrail 5)

- `payload["cwd_probe"]["source"]` and `["requested"]` (`web/api.py:3097`) are never read.
  `terminals.js:6443` reads only `resolved`, and `showExplorerCwdNotice()` reads only
  `reason` and `directory`. `requested` is additionally always `true` in the emitted
  object, since the server only emits it under `if cwd_probe["requested"] and not …resolved`.
- `CWD_SOURCE_PROCESS` and `CWD_SOURCE_PROBE` are produced by `effective_directory()` but
  no caller ever distinguishes them — every consumer tests `!= CWD_SOURCE_LAUNCH`. They
  are defensible as a debugging vocabulary; they are not currently read.

---

### F10 · **Non-issue** — a chunk's trailing newline does not paint an extra row

The original finding inferred an extra line box from the text stored in each `<pre>`:
`explorerLargeSourceChunkHtml()` prepends one sacrificial `\n` for the HTML parser and a
newline-aligned chunk normally ends with the source's own `\n`. That inference does not match
browser layout. A final preserved newline remains in `textContent`, but it does not instantiate
another empty line box after the last non-empty line.

Manual checks in both native/WebView2 and browser mode showed `L5000` followed directly by
`L5001` (and the same at later 5,000-line cuts). A focused reproduction using GridVibe's exact
adjacent `<pre class="explorer-source-chunk">` structure and CSS agreed in Chromium and Edge:
at a 20 px line height each two-line chunk measured 40 px, the two-chunk host measured 80 px,
and selection across the boundary produced exactly one newline between `L5000` and `L5001`.

**Resolution:** no code change. Stripping the newline would change faithful DOM text to solve a
row that neither engine paints and neither manual mode shows.

---

### F11 · Low — a frame-sliced Source build can outlive its pane

`explorerRunSourceRenderJob()` captures `pane` and the `code` element. The two documented
exits are covered: a group switch calls `explorerSuspendSourceRenderJob()`, and a closed
cached group calls `explorerAbandonSourceRenderJob()`. But a **single pane replaced in
place** — `replacePaneWithTerminal` / `replacePaneWithBrowser`, or a pane close — does not
clear `_explorerSourceRenderJob`. The captured `code` element is detached, so
`explorerRenderedSourceContainer(code) !== onScreen` stays *false* and the stand-down
branch never fires; the job runs to completion appending rows into a tree nobody can see.

Bounded (the build finishes and stops), so this is wasted frames rather than a leak — but
it competes with the pane that just replaced it, which is precisely what the suspend rule
was added for on the group path.

**Fix:** call `explorerAbandonSourceRenderJob(terminals[index])` in `replaceSessionPaneMode`
alongside the existing teardown.

---

### F12 · Low — abort controllers are never released

`explorerRequestSignal(pane, slot)` stores one `AbortController` per named slot on
`pane._explorerRequestAborters` and nothing clears the map on pane teardown. Six slots are
in use (`file`, `preview`, `diffParse`, `highlight`, `editHighlight`, `changeMarks`). The
memory is trivial; the consequence that matters is that a pane torn down mid-flight leaves
its worker job running instead of aborting it, so a terminated pane can still be holding a
pool worker on a document nobody is looking at.

**Fix:** iterate the slots in the same teardown path as F11.

---

### F13 · Low — `printf` format-string exposure in the POSIX prompt hooks

`web/terminal_cwd.py`:

```python
_POSIX_PROMPT_COMMAND = 'printf "\\033]7;file://$PWD\\033\\\\"'
_REMOTE_HOOK = ' _gv(){ printf "\\033]7;file://$PWD\\033\\\\"; }; …'
```

`$PWD` is expanded **into the format argument**. A directory whose name contains `%`
(`/srv/100%done`, `/tmp/50%off`) makes `printf` read it as a conversion specification, so
the emitted sequence is malformed and the observation for that directory is lost or
corrupted. Not a security issue — the module's own comment is right that nothing GridVibe
holds is interpolated, so there is nothing to quote — but `%` is a legal path character.

**Fix:** `printf '\033]7;file://%s\033\\' "$PWD"`.

Adjacent: OSC 7 payloads are conventionally percent-encoded, and `decode_osc7_target()`
calls `unquote()` unconditionally when the payload contains `%`. A path with a literal
`%2F` in it therefore decodes to a `/`. Emitting `%s` above without encoding and decoding
unconditionally are two halves of the same asymmetry; whichever way it is resolved, both
ends should agree.

---

### F14 · Low — test-style regression: 10 new source-text assertions

The working rules ask the ~296 legacy `assertIn("function …")` assertions to shrink when
touched. This branch added ten more, plus statement-level literals:

```python
self.assertIn("function explorerHighlightLinesForRender(", html)
self.assertIn("const HIGHLIGHT_WORKER_MIN_CHARS = 64 * 1024;", html)
self.assertIn("const hitIndex = explorerRepoSearchHitIndex(state, path, line);", click)
self.assertIn("'stage', 'unstage', 'revert', 'commit', 'stage-all', 'unstage-all', 'discard-all',", …)
```

The last one pins an exact source literal down to its trailing comma. `tests/test_api.py`
now carries 231 such assertions.

This is against a genuinely strong picture elsewhere: `test_explorer_tiers.py`,
`test_explorer_repaint.py`, `test_explorer_scroll.py`, `test_explorer_workers.py`,
`test_terminal_cwd.py` and `test_explorer_large_file_tier.py` all *execute* their modules
in Node, which is exactly what the rules ask for.

---

### F15 · Info — the coverage gap that let F1 ship green

`test_a_configured_root_survives_the_round_trip_and_still_pins` (`tests/test_api.py:6628`)
exercises only the branch where the configured root **still contains** the working
directory. No test drives the pane's shell *outside* the configured root, which is the
only branch where the flag and the stored root disagree.

---

### F16 · Info — the large diff tier's DOM build is not frame-sliced

`renderExplorerLargeDiff()` → `paintExplorerSideBySideDiff()` assembles the whole model as
one `innerHTML` write. It is bounded by the backend's 256 KiB / 4,000-line cap (~8,000
cells), so it is survivable — but it is the one CPU-heavy surface in this change set whose
*parse* moved to a worker while its *build* stayed a single uninterruptible task. Source
got both halves (`explorerRunSourceRenderJob`, an 8 ms frame budget); Diff got one.

Not a defect; an asymmetry worth recording before someone raises the backend diff cap.

---

## 3. What is right, and should not be "fixed"

Recorded so a later pass does not undo it.

- **Lock discipline.** `_observe_terminal_output_cwd()` runs on the pane's own pump thread
  outside `connection_lock`; `effective_directory()` takes the connection under the lock
  and does the remote read after releasing; `_track_terminal_agent_input()` calls it after
  its `with connection_lock:` block closes; `_snapshot_session()` reads only the
  already-known `current_directory`. Guardrail 2 is respected throughout.
- **Pooled SSH.** `_acquire_ssh_sftp` / `_release_ssh_sftp` are correctly paired through
  `try/finally` in both mode-switch branches, and the new `_explorer_cwd_repo_root()` probe
  reuses the already-held client rather than opening its own.
- **`GIT_DIFF_CONTEXT_WIDTHS` is a name allowlist**, resolved server-side, with an unknown
  name a `400` rather than a fallback. No request can dictate a Git argument.
- **`WinPtyProcess.spawn(command_list, …)`** (21f2311) is strictly more correct than the old
  `list2cmdline()` → pywinpty `shlex.split(posix=False)` round trip, which re-parsed
  MSVC-quoted output with POSIX-ish rules. Verified against the installed pywinpty source.
- **Worker isolation.** `explorer-worker.js` imports only same-origin first-party modules
  plus the pinned vendored Highlight.js, carrying the page's cache-busting query string.
  No CDN, no build step, no second copy of the library.
- **Escaping holds on every new path.** `escHtml()` on large-tier chunks;
  `highlightExplorerCode(text, '')` falls straight through to `explorerMarkedEscHtml()` so
  the large diff tier's colourless cells are still escaped; `showExplorerCwdNotice()` builds
  its message with `textContent`.
- **Worker / synchronous highlight parity.** Both `pushText` implementations advance the
  offset by `rawLength + 1` and trim a trailing `\r` from the emitted run, so a CRLF file
  renders identically either side of the 64 KiB worker threshold. The worker additionally
  coalesces adjacent same-class runs, which is visually identical and correctly refuses to
  coalesce across a line boundary.
- **Git scope plumbing.** All ten routes take the same `(root, anchor)` pair from
  `_explorer_git_anchor_paths()`; the scope arrives as a root-confined `resolve_dir()` and
  the mutation target stays a separate root-confined `resolve_candidate()`. The sidebar
  names the repository every action then uses.
- **`_git_repo_revision` includes `repo_path`** before the token is computed, so navigating
  between sibling repositories invalidates the sidebar. Absolute paths stay out of the token.
- **Stand-down handling** for superseded / suspended / abandoned render jobs is careful and
  the queued-reader flush rules are right — F11 is a missing call site, not a broken design.

---

## 4. README review

`README.md` was updated on this branch (+19 lines) and the additions are accurate: the
`terminal.shell_integration` config key, the whole **Shell integration** section, the
**Very large files & diffs** row, lazy Markdown preview, the background worker, and the
Git pin/follow controls all match the code.

### Wrong today

**`README.md:274`** — "**Both** JSON state files are written the same careful way…"

There are **three** durable stores through `web/state_files.py`: `runtime_state.json`,
`saved_sessions.json`, **and `config.json`** (`web/config.py:211`, `write_json_atomically`).
The Local Files table two lines above already lists all three. Pre-existing, but stale.
Suggested: "Each of these JSON state files is written the same careful way…".

### Missing — new user-visible behaviour with no mention

1. **Leaving the explorer clears the fixed Git pin** (`a596f9f`). The Git row says "Both
   settings survive Save Workspace and restart" without the one case where the pin is
   deliberately dropped, so the sentence currently over-promises. Follow-browsing is
   unaffected and an explorer-mode restore still restores its pin — both worth a clause.
2. **Where the explorer roots.** The File Explorer intro still says only "same directory,
   no re-navigation". It now roots on the **Git worktree containing the folder the terminal
   is in**, clamped so it never widens above the directory the pane was launched in — and
   it follows the shell back *up* as well as down (`b6208e0`, `fed67f5`). The Git row's
   "derived when a terminal enters explorer mode" hints at it; the section that introduces
   the feature does not.
3. **Panes come back where you left them.** "Save & restore" lists "directories" without
   saying which. A restored, reconnected, or split pane now replays the *observed* working
   directory with the launch directory as the `cd`'s own fallback (`f61970f`, `a84219b`),
   and a split clones where the source pane *is* rather than where it started (the 🪟 row
   in the icon table says only "clones its connection").
4. **The working-directory notice.** A new in-pane bar ("This pane is running an agent, so
   its current directory was not read. Opened at …" / "The terminal did not answer where it
   is. Opened at …") is a user-facing surface with no documentation.
5. **Unstage All** (`452ee32`). The Git row lists "stage, unstage, commit, publish, or
   discard" — the bulk Unstage All that now sits beside Stage All / Discard All is not
   named, and its index-only, no-confirm behaviour is the interesting part.

### Optional

6. Repository search no longer preselects the first hit (`452ee32`). The Search row makes
   no claim either way, so this needs a line only if the changed feel is worth calling out.

Everything else — Agent CLIs, Voice Input, Sessions & Workspace, Browser Preview, Icons &
Shortcuts, Security, Local Files, Development — was checked against the current code and
is accurate.

---

## 5. Suggested order of work

| # | Finding | Why first |
| --- | --- | --- |
| 1 | **F1** | One-expression fix; corrupts durable state until it lands, and F15 is the test that should land with it. |
| 2 | **F3** | Silently wrong diffs on a now-common path; the fix removes code rather than adding it. |
| 3 | **F2** | Two-field persistence change; needs a snapshot-round-trip test. |
| 4 | **F11 + F12** | One shared teardown call site. |
| 5 | **F5** | CSS counters make the splice do what it claims. |
| 6 | **F4, F13, F8, F9** | Small, independent. F10 was validated as a non-issue. |
| 7 | **F6, F7, F16, F14** | Design/consistency work, not defects. |

Everything above is analysis only — no code was changed.
