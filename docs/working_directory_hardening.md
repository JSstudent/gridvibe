# Working-Directory Hardening Plan

Status: **stages 1-3 landed**; stage 4 proposed.
Scope: ISSUE-2026-044 (explorer opens at the launch root, not the navigated
directory) and ISSUE-2026-045 (a saved workspace restores an agent pane at the
launch directory, not the one the agent was started in).
Last updated: 2026-08-21

---

## 1. The single root cause

**GridVibe has no concept of a pane's *current* working directory.** It has a
*launch* directory — `TerminalSession.directory` (`sessions/manager.py:53`) —
written when the pane is created and thereafter treated as though it were still
true. A shell that `cd`s changes nothing GridVibe can see.

Every symptom in both reports is that one fact, observed from a different
surface:

| Symptom | Surface reading the stale value |
| --- | --- |
| Explorer opens at the launcher root, not the navigated directory | `POST /api/sessions/<id>/mode` (`web/api.py:2884-2946`) |
| Explorer root pins to the launcher directory, so a repo below it never shows a Git tree | `_explorer_root_directory()` (`web/explorer.py:99`) |
| Navigating down the Files tree still shows no Git tree | `_get_git_repo_state()` anchors on the root (`web/explorer.py:2628`) |
| A saved workspace restores an agent at the launch directory | `_snapshot_session()` / `buildWorkspaceTerminalEntry()` (`web/runtime_state.py:152`, `web/static/js/terminals.js:2451`) |
| Splitting a navigated terminal starts the new pane at the launch directory | `split_session()` (`web/api.py:2670`) |

There is exactly one place that tries to close the gap —
`_resolve_live_terminal_cwd()` (`web/terminal_io.py:377`) — and it is a
best-effort *write into the interactive shell*, called from only two routes. Its
failure mode is silence, which is the flakiness in the report.

---

## 2. Evidence, per mechanism

*Recorded against the code as it stood when this plan was written. §2.2 and the
silent-failure half of §2.1 are closed by stage 1, and the rest of §2.1 by stage 2
— the probe is now the last of three sources rather than the only one (see §5).
§2.3 and §2.4 still stand.*

### 2.1 The cwd probe is a keystroke injection with a 0.75 s deadline

`_resolve_live_terminal_cwd()` types a `printf`/`echo`/`Write-Output` line into
the pane's shell (`_send_connection_input`), then polls the rolling output
buffer for its markers for up to 0.75 s. Consequences, all reachable today:

- **It fails whenever the shell is not at an idle prompt.** A build, a pager, a
  TUI, a slow WSL boot, or a remote link with more than 0.75 s of latency all
  return `None`, and the caller falls back to `session.directory` — the launch
  value — *without telling anyone*. That is the intermittency: the same gesture
  gives a different root depending on what the shell happened to be doing.
- **It types into whatever is running.** `POST .../mode` with
  `refresh_cwd: true` is reachable on a pane running an agent, so the probe
  command lands in the agent's input box rather than at a shell prompt.
- **It is the only writer, and it writes at query time.** Nothing observes the
  cwd continuously, so there is no value to fall back to that is better than
  the launch directory.

### 2.2 The explorer root pins whenever the cwd is inside it

`change_session_mode()` (`web/api.py:2929`, and `:2893` for SSH) computes:

```python
root_directory = _explorer_root_directory(session) or next_directory
```

and `_explorer_root_directory()` (`web/explorer.py:99`) falls back to
`session.directory`. For a pane that was *launched as a terminal* — which never
has an explicit explorer root — that expression always yields the launch
directory. The code below it only re-roots when the cwd has escaped the root
entirely (pinned by
`test_switch_ssh_terminal_to_explorer_refreshes_live_remote_cwd_outside_previous_root`,
`tests/test_api.py:6135`); a cwd *inside* the root keeps the root.

That rule is right for a pane that really was given a root (a Local Repository
pane, or one that has round-tripped explorer → terminal → explorer, which is
what `tests/test_api.py:5972` protects). It is wrong for a terminal pane, and
the two are indistinguishable today because the fallback manufactures a root
that was never configured.

Result: launcher directory `…\Desktop`, `cd Desktop\gridvibe_colab`, open
explorer → root `…\Desktop`, listing `gridvibe_colab`, and a Git sidebar that
reports "not inside a Git worktree" forever.

### 2.3 The Git sidebar is anchored on the root and only on the root

`_get_git_repo_state()` calls `_get_git_context(backend, root_path, root_path)`
— the browsed directory is never passed. Every Git route resolves the same way
through `backend.root_directory()` (`web/api.py:1627-1797`: `git/repo`,
`git/state`, `stage-all`, `unstage-all`, `discard-all`, `commit`, `publish`, and
the per-file routes via `resolve_candidate`).

So the sidebar's `git rev-parse` runs in the root:

- a repo **above** the root already works (`repo_root` may be an ancestor;
  `_explorer_git_changed_files()` then filters visible changes to inside the
  root — `web/explorer.py:2568`);
- a repo **below** the root never works, and navigating into it changes nothing,
  because navigation moves the browsed path and the anchor is not the browsed
  path.

This is the second half of ISSUE-2026-044 and it is independent of the cwd bug:
even with a perfect cwd, an explorer opened above a repo still has no sidebar.

### 2.4 Runtime agent promotion records the agent but not the place

`_track_terminal_agent_input()` (`web/terminal_io.py:837`) watches submitted
lines and, on a registered agent binary, promotes the pane:

```python
session_manager.update_session_metadata(
    session_id,
    startup_mode="agent", initial_command_mode="agent",
    agent_selection=agent_selection, custom_agent="", initial_command=…,
)
```

`directory` is not in that update — the one moment at which GridVibe *knows* the
agent's working directory is a prompt line it has already parsed, and it throws
that context away. Save Workspace then captures `session.directory`
(`web/static/js/terminals.js:2451` client-side, `web/runtime_state.py:152`
server-side), and restore replays `cd <launch dir>` followed by the agent
command (`_run_startup_sequence`, `web/terminal_io.py:491-507`).

That is exactly the reported sequence: launch on the repository root, `cd` into
a subdirectory, type `codex`, Save Workspace, restart — the agent comes back in
the right *mode* and the wrong *place*.

---

## 3. The contract to establish

> **A pane's launch directory is where it started. Its working directory is
> where it is now. Everything that asks "where is this pane?" — opening an
> explorer, splitting, saving a preset, capturing a workspace, restoring one —
> reads the second, and falls back to the first only when it can say so.**

Three supporting rules:

1. **A derived root is not a configured root.** An explorer root that was never
   chosen by the user must not behave like one. Only an explicitly configured
   root pins.
2. **Observation never writes to the shell.** The working directory is learned
   from output the shell already produces, or from the OS, never by typing at a
   prompt the user may be using. The existing probe survives only as an
   explicit, last-resort fallback on a pane with no other source.
3. **The Git anchor is a property of the directory being browsed, and one
   anchor serves the whole sidebar.** Whatever repository the sidebar displays
   is the repository *Commit*, *Stage All*, *Discard All* and *Publish* act on.

---

## 4. Design

### 4.1 Where the live value lives

Add one field to `TerminalSession`:

```python
current_directory: Optional[str] = None   # observed; None = never observed
```

- **Not** added to `_SESSION_SNAPSHOT_FIELDS`. Instead `_snapshot_session()`
  (`web/runtime_state.py:186`) writes `current_directory or directory` into the
  snapshot's `directory` slot, so the persisted shape is unchanged and every
  existing restore path keeps working with no migration.
- One accessor, `effective_directory(session)`, is the only reader. Every
  consumer in §1's table goes through it; nothing else reads `session.directory`
  to answer "where is this pane now".
- It joins `update_session_metadata`'s allowlist (`sessions/manager.py:945`) and
  `to_dict()`, because the client needs it for the pane header and for
  `buildWorkspaceTerminalEntry()`.

`directory` keeps its meaning — the launch value, and what a *reconnect* uses,
unless §8 D3 decides otherwise.

### 4.2 How the value is observed — three sources, in order

**Source A — shell integration (primary).** At startup, alongside the `cd` the
startup sequence already writes, install a prompt hook that emits the cwd as an
invisible OSC sequence on every prompt:

| Shell | Sequence | Installation |
| --- | --- | --- |
| bash / zsh / sh (SSH + WSL) | `OSC 7 ; file://<host><path> ST` | appended to `PROMPT_COMMAND` / `precmd_functions`, never replacing |
| PowerShell | `OSC 9 ; 9 ; <path> ST` | wraps the existing `prompt` function |
| cmd | `OSC 9 ; 9 ; $P ST` | the `prompt` builtin, using its `$e` escape |

The server parses both forms out of the output stream in the reader loops
(`_stream_ssh_output`, `_stream_local_output`) and stores the result on the
connection. Properties that make this the right primary source:

- **Zero cost per query** — the value is already known when a route asks.
- **Correct while an agent is running.** The shell emits nothing while `codex`
  holds the terminal, so the stored value is the cwd *at the moment the agent
  was started* — precisely what ISSUE-2026-045 needs.
- **No write at query time**, so §3 rule 2 holds.
- **Invisible.** xterm.js has no handler for OSC 7 or OSC 9;9 and discards
  unhandled OSC identifiers, so nothing is rendered. Verify against the pinned
  `web/static/vendor` build before relying on it (task S2.1).

Costs, stated rather than hidden: the installation line is echoed once in the
pane at startup, next to the `cd` that is already echoed; a custom `PROMPT` set
through cmd's AutoRun is replaced; and an unrecognized remote shell simply never
emits, falling through to source B or C.

**Source B — the OS (corroboration, POSIX only).** For a local POSIX pane we own
the shell's PID (`web/terminal_io.py:1080`), so `os.readlink("/proc/<pid>/cwd")`
is authoritative and free. For an SSH pane the same read is available over a
**second exec channel** on the pooled transport once the shell's PID is known —
recorded once at startup by the same line that installs source A. This never
touches the interactive channel, so it is safe while an agent is running.
Windows local panes have no equivalent without a new dependency — see D1.

**Source C — the existing marker probe (last resort).** Kept, but demoted:
called only when A and B have produced nothing, never when
`startup_mode == "agent"`, and its failure is **reported** rather than swallowed.

`effective_directory()` returns A/B/C in that order, then `directory`, and
carries *which* source answered so callers can distinguish "observed" from
"assumed".

### 4.3 What the explorer does with it

Replace the `_explorer_root_directory(session) or next_directory` expression
with an explicit resolution:

```
configured_root = the root the user actually chose (launch config, or a
                  later "Set root here"), or None
observed_cwd    = effective_directory(session)
repo_root       = git worktree root containing observed_cwd, if any

open_root = configured_root
            or clamp(repo_root or observed_cwd, floor=launch_directory)
open_path = observed_cwd
```

`clamp(candidate, floor)` returns `floor` when `candidate` is a strict ancestor
of `floor`, else `candidate`. Three consequences, all intended:

- `cd` into a repo below the launcher directory → the root becomes **the repo**,
  the Git sidebar works, and the reader can still navigate up to the repository
  root (which rooting at the bare cwd would have prevented).
- A pane launched *inside* a repository subdirectory does not silently widen its
  root above the directory the user selected in the launcher.
- A cwd that has left the launch directory entirely re-roots exactly as today.

*Stage 1 implements this resolution in
`_resolve_explorer_open_root()` (`web/explorer.py`), for both the local and the
SSH half of the mode switch.*

`configured_root` requires distinguishing an explicit root from a derived one.
Today `explorer_root_directory` is `None` for a terminal pane and is filled in by
every explorer→terminal round trip (`_resolve_pane_terminal_directory`,
`web/explorer.py:3386`), which is what makes the root sticky. Stage 3 records the
distinction; D2 holds the two ways to do it.

### 4.4 What the Git sidebar does with it

The sidebar's anchor becomes the **worktree containing the browsed directory**,
resolved by one helper and echoed back on every Git payload:

- `GET .../git/repo` and `GET .../git/state` take the browsed `path` (already
  known to the client as `pane._explorerPath`) and anchor on it.
- Every mutating route resolves the anchor **the same way from the same
  parameter**, so the sidebar and its buttons can never address different
  repositories.
- `_git_repo_revision()` (`web/explorer.py:2593`) gains the anchor's
  *root-relative* identity — never an absolute path, which would break the
  deliberate local/SSH token parity — so navigating between two repositories
  under one root registers as a change and the watcher re-fetches.
- Root confinement is unchanged: the browsed path is already inside the root by
  construction, and `_explorer_git_changed_files()` keeps filtering visible
  changes to inside the root.

This is read-side plumbing plus an identical change to six mutation routes. It is
deliberately the last stage, because §4.3 alone already fixes the reported case
and this fixes the residual one — an explorer deliberately opened above a repo.

---

## 5. Staged implementation

Each stage is independently shippable and independently valuable. Stage 1 alone
closes the flakiness; stage 2 is the machinery; stages 3-4 finish the contract.

### Stage 1 — Stop pinning, stop lying (no new machinery) — **LANDED**

| Change | Files |
| --- | --- |
| Split "configured root" from "fallback to `directory`"; the mode switch uses the §4.3 resolution | `web/explorer.py`, `web/api.py:2884-2946` |
| Resolve the Git worktree root of the cwd when choosing the open root | `web/explorer.py` (reuse `_get_git_context`'s `rev-parse`) |
| A failed cwd probe on an explicit `refresh_cwd` is reported to the client, and the pane says which directory it opened on | `web/api.py`, `web/static/js/terminals.js` |
| Never probe a pane whose `startup_mode == "agent"` | `web/api.py:2884`, `web/terminal_io.py:377` |

Tests: extend `tests/test_api.py`'s mode-switch cases — a terminal pane whose cwd
is a repo below the launch directory roots at the repo; a pane with a configured
root still pins (`test_switch_roundtrip_preserves_explorer_root_for_parent_navigation`
must pass untouched); a failed probe returns an observable outcome instead of a
silent launch-directory root.

#### What was done

**`web/explorer.py`**

- `_configured_explorer_root_directory(session)` — reads **only**
  `explorer_root_directory`, with no fallback to `session.directory`. This is the
  split: `_explorer_root_directory()` keeps its fallback (an explorer pane
  resolving paths still needs one), while everything deciding *where an explorer
  opens* asks the configured-only accessor, so a pin belongs to a root someone
  really chose.
- `_local_path_inside(root, candidate)` — the `commonpath` + `normcase`
  containment check that was written inline in the mode switch, extracted so both
  path flavours are one predicate each (`_remote_path_inside` is the remote one).
- `_resolve_explorer_open_root(configured_root, observed_cwd, launch_directory,
  repo_root, *, contains)` — §4.3 as one path-flavour-agnostic policy function.
  The clamp is expressed as `contains(candidate, floor) and not contains(floor,
  candidate)`, so an inclusive `contains` gives "strict ancestor" without a
  second equality predicate per flavour.
- `_resolve_git_worktree_root(backend, path)` — the `rev-parse --show-toplevel
  --is-inside-work-tree` half of `_get_git_context()`, lifted out and returning
  `(repo_root | None, probe_error | None)`. `_get_git_context()` now calls it, so
  the sidebar's anchor question and the open-root question have one
  implementation rather than two spellings of the same `rev-parse`.
- `_explorer_cwd_repo_root(backend, path)` — the open-root caller's wrapper: a
  probe that cannot answer is not an error here, it just means "no repo", so the
  caller falls back to the working directory.

**`web/api.py`**

- `_refresh_pane_cwd(session_id, session, requested)` — one helper that both
  performs the probe and reports its outcome (`requested` / `resolved` /
  `reason`). An agent pane returns `reason="agent_pane"` **without probing**.
- `change_session_mode()`'s explorer branch, both the SSH and the local half,
  now resolve `configured_root` → validate it exists → probe for the cwd's
  worktree root **only when no configured root holds the cwd** (so the common
  pinned case costs no `git`, and the SSH case costs no extra exec channel) →
  `_resolve_explorer_open_root(...)`. The old
  `_explorer_root_directory(session) or next_directory` expression and its
  inline containment check are gone from both halves.
- The response is now `to_dict()` **plus** `cwd_probe` when a requested probe did
  not resolve, carrying the reason and the directory the pane actually opened on.

**`web/terminal_io.py`**

- `_resolve_live_terminal_cwd()` refuses an agent pane at the top and logs at
  DEBUG. The refusal lives at the probe, not only at its caller, because the
  probe *types into the pane* — on an agent pane the marker command would land in
  the agent's input box.

**`web/static/js/terminals.js` + `web/static/css/terminals.css`**

- `showExplorerCwdNotice(index, probe)` paints a dismissible in-pane bar under
  the explorer bar naming the directory the pane opened on. Informational, so it
  carries a Dismiss and no retry — the mode switch itself succeeded. It is a
  `role="status"` element in the pane, never `showGridVibeNotice()`.
- `.explorer-cwd-bar` joins the `.explorer-fs-bar` selector list and restates
  nothing but its tone (`--explorer-text` instead of `--gv-danger`), per the
  styling guardrail on reusing a rule rather than copying its declarations.

**`tests/test_api.py`** — five new cases:
`…roots_on_repo_below_launch_directory` (the reported case: launch above a repo,
cd into `repo/src`, root becomes `repo`),
`…does_not_widen_root_above_launch_directory` (launched inside `repo/src`, the
root stays `repo/src`), `…reports_a_failed_cwd_probe`,
`…switch_agent_pane_to_explorer_never_probes_the_shell`, and
`test_live_terminal_cwd_probe_refuses_an_agent_pane`. Full suite green (1873
tests) and `ruff` clean.

#### Behaviour change to be aware of

One existing expectation moved, and it is the intended consequence of §4.3
rather than an incidental one:
`test_switch_ssh_terminal_pane_to_explorer_preserves_host` asserted that a
terminal pane launched at `/srv/app` and switched to the explorer at
`/srv/app/src` rooted at `/srv/app`. That root was the launch directory wearing a
root's clothes — exactly the manufactured pin this stage removes — so the pane
now roots at `/srv/app/src`. The test's own subject (host/username preservation)
is untouched; only the root assertion changed, with a comment saying why.

The cost, stated rather than hidden: when the working directory is **not** in a
repository, the root is the working directory itself, so the reader cannot
navigate up to the launch directory the way they could before. §4.3 chose this
deliberately (the repo-root case is what preserves upward navigation where it
matters), but it is the one place where stage 1 takes something away. If that
turns out to be the wrong trade, the fix is local: give
`_resolve_explorer_open_root()` a `repo_root or launch-directory-if-it-contains-
the-cwd` candidate instead of `repo_root or observed_cwd`.

#### Deliberately *not* done in stage 1

- No `current_directory` field, no OSC parsing, no prompt hook — the probe is
  still the marker-injection one, still best effort, and still the only source.
  It is now *honest* about failing rather than silent, which is what closes the
  flakiness; stage 2 is what removes the failure mode.
- `_explorer_root_directory()` and `_remote_explorer_root_directory()` keep their
  fallback to `session.directory`, because every other explorer consumer resolves
  paths for a pane that *is* an explorer, where the fallback is right.
- D2 is still open. Stage 1's `configured_root` is "the pane carries a non-empty
  `explorer_root_directory`", which is true for a Local Repository pane and for
  any pane that has round-tripped explorer → terminal → explorer. Distinguishing
  a *user-chosen* root from one back-filled by `_resolve_pane_terminal_directory()`
  is still stage 3's job.
- Stage 4's Git anchor is untouched: an explorer deliberately opened **above** a
  repository still has no sidebar. Stage 1 only stops the explorer from being
  opened above one by accident.

### Stage 2 — Live working-directory tracking — **LANDED**

| Change | Files |
| --- | --- |
| `current_directory` field, `to_dict()`, metadata allowlist | `sessions/manager.py` |
| OSC 7 / OSC 9;9 parser with a per-connection split-sequence residue buffer; parse **outside** `connection_lock` (guardrail 2) | `web/terminal_io.py`, or a new `web/terminal_cwd.py` if it grows past ~150 lines |
| Prompt-hook installation per shell kind, quoted for the target shell (`_powershell_single_quote` / `shlex.quote`, guardrail 4) | `web/terminal_io.py:477` |
| `/proc/<pid>/cwd` reader (local POSIX; SSH via a bounded second exec channel) | `web/terminal_io.py` |
| `effective_directory()` plus source reporting | `web/terminal_io.py` |
| `terminal.shell_integration` (bool, default `true`) through `RuntimeConfigState` — a real kill switch, since the hook mutates the user's prompt | `web/config.py`, `default_config.json`, `web/static/js/app-settings.js` |

The parser is pure text-in/values-out and gets a behavioural test,
`tests/test_terminal_cwd.py`: both sequence forms, a sequence split across two
reads, a `/mnt/c/...` WSL path normalizing to `C:\...`
(`_normalize_probed_local_cwd` already does this), a malformed sequence ignored,
and a bounded residue buffer that a hostile stream cannot grow.

Guardrail note: the replay buffer stays **verbatim** — parsing observes the
stream, it does not filter it (the mouse-reporting contract in `CLAUDE.md`).

#### What was done

**`web/terminal_cwd.py`** (new, ~230 lines — past the ~150 the table made the
condition, so it is its own module rather than more of `terminal_io.py`). Pure
text-in/values-out, no `web` imports, so its tests execute it.

- `parse_cwd_events(chunk, residue)` → `(events, residue)`. Reads OSC 7
  (`file://<host><path>`), OSC 9;9 (a native path) and GridVibe's own
  `OSC 777 ; gridvibe-pid ; <n>`, each with a BEL **or** ST terminator. A
  payload may contain neither BEL nor ESC, so the pattern cannot run away
  across a chunk looking for a close.
- The residue is bounded **by construction, not by a trailing check**: the
  search for a still-arriving sequence only looks inside the last
  `CWD_RESIDUE_MAX_CHARS` (2048) characters, so a stream that opens
  `ESC ] 7 ;` and never closes it has its fragment dropped rather than carried,
  and the next real sequence is still read. Scanning starts from `ESC ]`
  occurrences rather than every ESC, so an ESC-dense TUI chunk costs a `find`
  loop and not a quadratic walk.
- `decode_osc7_target()` percent-decodes the URL form and accepts a bare path;
  `normalize_observed_cwd(cwd, shell_kind, *, on_windows)` does the two
  translations (`/mnt/c/…` → `C:\…` for a WSL pane, and the leading slash the
  `file:///C:/…` form requires). `on_windows` is a parameter rather than a read
  of `os.name` so both sides are testable from either host, and
  `_normalize_probed_local_cwd()` now delegates to it — one implementation, not
  two spellings.
- **A hook is only *typed* at a shell that cannot be handed one.** A typed
  line is echoed into the pane — twice, when it is sent before the shell has
  drawn its first prompt — so a pane GridVibe starts itself is given its hook
  at spawn: `shell_integration_environment()` returns `PROMPT` for cmd and
  `PROMPT_COMMAND` for bash (plus the `WSLENV` entry `wsl.exe` needs in order
  to forward it, appended to any the user already has), and
  `shell_integration_arguments()` returns PowerShell's `-NoExit -Command`,
  which is an argument rather than input and so is not echoed either. Nothing
  appears in a local pane at all. Only `remote_shell_integration_command()` is
  typed, because `sshd` forwards only what its `AcceptEnv` allows and writing
  an rc file to the remote host is not a thing a terminal gets to do; that line
  opens with a space (out of history), appends to an existing `PROMPT_COMMAND`,
  covers zsh's `precmd_functions`, reports the pid once, and is 202 characters
  rather than the 400 the first attempt typed.
- **Nothing GridVibe holds is interpolated into any of these strings** —
  `$PWD`, `$$`, `$P` and the PowerShell provider path are the shell's own
  values — so guardrail 4's shell-quoting rule is met by having nothing to
  quote, not by quoting carefully.

Every mechanism was run against the real thing rather than reasoned about:
`bash` 5.2 and Git's `sh` take `PROMPT_COMMAND` from the environment (and an
interactive bash keeps it through its rc files), `cmd.exe` takes `PROMPT` from
the environment, PowerShell started with the hook as a `-Command` argument
emits `ESC ] 9 ; 9 ; <path> ST` **and still renders the original prompt**
through the copied `_GridVibePrompt`, and the remote line installs in `bash`
while preserving an existing `PROMPT_COMMAND` (`_gv;echo mine`).
`PtyProcess.spawn` in the pinned pywinpty (3.0.5) takes the `env` the local
path now passes it. What is *not* proven here is whether ConPTY/WinPTY forwards
an unknown OSC back to us on a local Windows pane; if it does not, that pane
observes nothing and falls back to the probe exactly as it did before stage 2.

**`web/terminal_io.py`**

- `_observe_terminal_output_cwd(session_id, connection, output)` — source A,
  called from `_stream_ssh_output` and from all three branches of
  `_stream_local_output`, directly after the chunk is cached and **before** the
  emit. It takes **no lock**: the pane's pump thread is the only writer of that
  connection's residue, and a per-chunk regex scan under `connection_lock`
  would sit in front of every other pane's output (guardrail 2). It bails on
  the first character when the chunk holds no ESC and no residue is pending.
  The chunk is still cached and replayed **verbatim** — this observes the
  stream, it does not filter it.
- Only a *changed* directory writes metadata and broadcasts. The hook fires on
  every prompt, so without that check an idle Enter would be a broadcast.
- `_local_process_cwd()` / `_remote_process_cwd()` / `_process_reported_cwd()`
  — source B. A local POSIX pane reads `/proc/<pid>/cwd` off the `Popen` it
  already owns. A remote pane runs `readlink /proc/<pid>/cwd` on a **second
  exec channel** of its own transport, never the interactive one, so it is safe
  while an agent is running; the **drain** carries the bound rather than a
  `| head -c` pipeline, which would report head's exit status and turn a failed
  remote command into an empty successful one. The pid comes from the one-shot
  `gridvibe-pid` sequence the POSIX hook emits for an SSH pane only.
- `effective_directory(session_id, session, *, allow_probe=False)` →
  `(directory, source)` with `CWD_SOURCE_SHELL_INTEGRATION` / `_PROCESS` /
  `_PROBE` / `_LAUNCH`. The launch directory is still an answer — it is just
  labelled as an assumption, which is the whole point of §3's rule 2. The probe
  is refused for an agent pane **here as well as inside the probe**, so a
  caller that opts in cannot type into an agent's input box by accident.
- `_local_shell_integration()` folds the hook into a local shell's argv and
  environment at spawn (`_connect_local_session` passes the result to both the
  WinPty and the POSIX branch), and `_run_startup_sequence()` types a line only
  for an **SSH** connection — before the `cd`, so the first prompt after it
  already reports where the pane ended up. Both paths are gated on
  `runtime_config.terminal_shell_integration`.

**`sessions/manager.py`** — `current_directory: Optional[str] = None` on
`TerminalSession`, in `to_dict()` and in `update_session_metadata`'s allowlist,
with a comment saying it is read through `effective_directory()` and never
directly. Deliberately **not** in `_SESSION_SNAPSHOT_FIELDS`: stage 3 is what
writes `effective_directory()` into the snapshot's existing `directory` slot,
so the persisted shape still does not move.

**`web/config.py`, `default_config.json`, `web/api.py`,
`templates/partials/app_settings_modal.html`, `web/static/js/app-settings.js`**
— `terminal.shell_integration` (bool, default `true`) joins `RuntimeConfigState`
and the `/api/app-config` read/normalize pair, with a checkbox in App Settings.
It gates **installing** the hook, not reading it: a user whose own shell
configuration already emits OSC 7 is still followed with the setting off, and
an older `config.json` that never mentions the key keeps working (absent means
on, on the client too).

**`web/api.py`** — `_refresh_pane_cwd()` now asks `effective_directory(…,
allow_probe=True)` and reports the `source` it got, so `reason` is
`"probe_failed"`/`"agent_pane"` only when *nothing* answered. The `/shell`
route asks the same question instead of probing directly (switching shells
mid-build now lands where the pane is) and clears `current_directory` when it
restarts the pane, because the old shell's last report is not an observation of
the new one.

**Tests** — `tests/test_terminal_cwd.py` (28 cases: both sequence forms, both
terminators, a sequence split across a read boundary *and* one split inside its
terminator, a completed sequence reported once, malformed input ignored, the
residue bound against a hostile stream, the pid line, both path translations,
and each shell family's installed line, including a round trip over what bash
and cmd actually emit). Eleven more in `tests/test_api.py`: the observer records
without a write, a split chunk still reports, an unchanged directory is not
rebroadcast, an agent pane is *observed* though never probed, the four
`effective_directory()` precedence cases, an agent pane's explorer switch now
opening on the observed directory, hook-before-`cd` ordering, pid only on a
remote pane, and the kill switch. Full suite green (1913 tests) and `ruff`
clean.

#### Two existing tests were adjusted (and why)

- The eleven `patch.object(api, "_resolve_live_terminal_cwd", …)` sites became
  `patch.object(web_terminal_io, …)`. The probe is no longer called from
  `web/api.py`'s namespace but from inside `effective_directory()`, so the old
  patch target stopped intercepting it. Every assertion is unchanged; only the
  module the mock is installed on moved.
- The six `_run_startup_sequence` cases that pin the exact `cd` a shell family
  gets now run with `terminal_shell_integration` patched off. Their subject is
  the `cd` and its pacing, not the line in front of it, and the hook has its own
  cases beside them — including the one that pins the ordering.

#### Behaviour changes to be aware of

- **An SSH pane echoes one extra line at startup.** It is echoed *twice*, like
  the `cd` beside it has always been, because both are written before the shell
  has drawn its first prompt — the terminal echoes the characters as they
  arrive and the shell's own line editor redraws them once the prompt is up.
  That doubling is pre-existing and is not addressed here; the fix would be to
  wait for the shell to settle before writing, which changes every SSH pane's
  startup timing and is its own change.
- **Local panes echo nothing**, because their hook is installed at spawn.
- **cmd's `PROMPT` is replaced** with the default `$P$G` plus the sequence; a
  `PROMPT` set through AutoRun is lost for that pane. bash/zsh keep their
  existing hook when the line is typed (SSH), but a **local** shell whose own
  rc file assigns `PROMPT_COMMAND` overwrites the inherited one, and that pane
  falls back to `/proc` or the probe.
- **An agent pane can now answer "where are you?"** without being probed, so
  switching one to the explorer opens on the directory the agent was started
  in. `reason: "agent_pane"` now means "nothing was ever observed on this
  pane", not "we refuse to ask".
- **`session_status` payloads carry `current_directory`**, and a pane that
  changes directory broadcasts once. Nothing on the client reads the field yet
  — stage 3 is its consumer (`buildWorkspaceTerminalEntry()` and the pane
  header).

#### Deliberately *not* done in stage 2

- Nothing is persisted from the new value: `_snapshot_session()`, Save
  Workspace, split, and runtime agent promotion all still read
  `session.directory`. That is stage 3, and ISSUE-2026-045 stays open until it
  lands.
- The Git anchor is still the explorer root (stage 4), so an explorer
  deliberately opened *above* a repository still has no sidebar.
- No `psutil` and no `ctypes` PEB read — see D1, now decided.

### Stage 3 — Persist the place — **LANDED**

| Change | Files |
| --- | --- |
| Agent promotion stamps the observed directory at promotion time | `web/terminal_io.py:895` |
| Snapshot writes `effective_directory()` into `directory` | `web/runtime_state.py:186` |
| Save Workspace / Save Session read the live value | `web/static/js/terminals.js:2451` |
| Split inherits the source pane's live directory | `web/api.py:2670` |
| Record whether an explorer root was configured or derived, so §4.3's `configured_root` is real | `web/workspaces.py:600-650`, `sessions/manager.py` |

Tests: `tests/test_multi_workspace.py` — an agent pane promoted at runtime in a
subdirectory captures and restores there; `tests/test_api.py` — a split of a
navigated terminal starts in the navigated directory;
`tests/test_session_persistence_contract.py` must pass untouched, because the
persisted shape does not change.

#### What was done

**`sessions/manager.py`**

- `TerminalSession.explorer_root_configured: Optional[bool] = None` plus a
  `__post_init__` that resolves an unstated flag from the root itself. That is
  what makes D2 hold at the seam rather than at one caller: every
  *construction* path — the launcher, a saved preset, a restored snapshot, and
  `create_session()` called directly — takes its root from a launch config, so
  a root present at build time is a chosen one. A **derived** root only ever
  arrives later, through `update_session_metadata`, which states `False`
  explicitly. Deriving it in `_session_launch_fields()` instead would have left
  every other construction path silently answering "not configured", which is
  exactly the bug the flag exists to prevent.
- The flag is in `to_dict()` and the metadata allowlist, and deliberately
  **not** in `_SESSION_SNAPSHOT_FIELDS` — see the snapshot note below.
- `merge_browser_tabs()` clears `current_directory`. The pane's shell is
  closing behind that switch, so its last report stops being an observation of
  anything live, and a browser pane never navigates the filesystem, so nothing
  would replace it.

**`web/explorer.py`**

- `_configured_explorer_root_directory()` now answers only when the flag is
  set. Its docstring names both ways a root can be manufactured: the
  `session.directory` fallback stage 1 removed, and the resolved root the mode
  switch has to *store* because the live explorer needs a confinement
  boundary — which is the one this stage closes.
- `_resolve_pane_terminal_directory()` stops back-filling. A pane leaving
  explorer or browser mode is handed the root it was confined to only when that
  root was configured; otherwise it leaves with none, and the next switch
  re-derives from where the pane actually is. It still hands back the
  *resolved* form of a configured root rather than the raw stored string, so
  the value a round trip stores does not change shape.

**`web/api.py`**

- Both halves of the terminal→explorer switch record `explorer_root_configured`
  alongside the root they store: `bool(configured_root)`, so a pin belongs to
  the resolution that pinned rather than to the fact that a root got written.
- The explorer/browser→terminal switch stores the configured root and its flag
  together.
- Every mode switch clears `current_directory`. Nothing is lost — the directory
  the observation named is what `directory` now holds — and leaving it set would
  put a dead shell's report into the next snapshot's `directory` slot.
- `split_session()` clones where the pane *is*: `effective_directory()` for a
  terminal pane, and the existing `_resolve_pane_terminal_directory()` branch
  for an explorer or browser pane. It inherits the source pane's root only when
  that root was configured. The probe is not allowed — a split must not type at
  the pane it is cloning.
- `_refresh_pane_cwd()`'s `requested` now gates the **probe**, not the
  question. Reading an observation the pane already produced costs nothing and
  writes nothing, so a mode switch that did not ask for a refresh no longer
  falls back to an assumption it had no reason to prefer. Only `requested`
  outcomes are reported to the client, so the payload is unchanged.

**`web/terminal_io.py`**

- `_track_terminal_agent_input()` stamps the observed directory at promotion.
  That is the one moment GridVibe knows where the agent is being started: the
  shell is still at its prompt, and a beat later the agent owns the terminal
  and emits no prompt of its own. It writes `current_directory`, never
  `directory` — the launch slot keeps meaning "where this pane started" (§4.1)
  — and it writes nothing at all when the source is `CWD_SOURCE_LAUNCH`, because
  an assumption is not an observation. The probe is not allowed, for the same
  reason as the split.
- `_startup_directories(session)` → `(target, fallback)` is D3, and both spawn
  paths take it: `_run_startup_sequence()`'s `cd` and `_connect_local_session`'s
  spawn `cwd`. The `cd` is written to try the second when the first fails —
  `cd A 2>/dev/null || cd B` for POSIX, `cd /d "A" 2>nul || cd /d "B"` for cmd,
  and a `Test-Path` branch for PowerShell rather than a trailing
  `-ErrorAction`, because a failed `Set-Location` writes a red error into the
  pane before the fallback runs and the first line of a reconnect should not
  look like the reconnect broke. `fallback` is empty when there is nothing to
  fall back to, so an unobserved pane sends exactly the line it always did.

**`web/runtime_state.py`**

- `_snapshot_session()` writes the observed directory into the existing
  `directory` slot. It reads **only** `current_directory`, not
  `effective_directory()`: this runs inside the runtime-state lock hold, and
  the other two sources are an SSH exec channel and a keystroke at a prompt —
  neither slow nor network work belongs under a shared lock (guardrail 2). An
  absent observation falls back to the launch value exactly as before.
- The persisted shape does not move: no new key, and
  `explorer_root_configured` is deliberately absent, because a root that
  reaches a launch config *is* one somebody chose and `__post_init__` says so
  on the way back in. `tests/test_session_persistence_contract.py` passes
  untouched.

**`web/static/js/terminals.js`**

- `buildWorkspaceTerminalEntry()` reads `session.current_directory ||
  session.directory`. An explorer pane still answers with its root, which is
  the boundary a relaunch has to reproduce.

**Tests** — three in `tests/test_multi_workspace.py` (the ISSUE-2026-045 round
trip end to end: hook reports `/srv/app/api`, `codex` promotes the pane, the
capture replays `api` and not `app`, the persisted shape gains no key, and a
restore lands there; plus an unobserved pane still captured at its launch
directory). Ten in `tests/test_api.py`: a split of a navigated terminal and of
an unobserved one, both halves of D2 (a derived root re-derives on the next
switch, a configured root survives the round trip and still pins), the mode
switch dropping a dead shell's report, three D3 cases including each Windows
shell family's fallback form, and agent promotion both stamping and inventing
nothing. Full suite green and `ruff` clean.

#### Open decisions, decided

- **D2 — recorded, with the recommendation's own proviso applied.** The
  recommendation was the second option — the convention that
  `explorer_root_directory` is written only when the user chose it, with no new
  field — *"if and only if the round-trip behaviour it protects is genuinely
  preserved"*. It is not, and the reason is structural rather than incidental:
  the terminal→explorer switch **has** to store the root it resolved, because
  the live explorer is confined to it, and once stored nothing downstream can
  tell that root from one the user picked in the launcher. Dropping it on the
  way out costs the launched-explorer round trip its root
  (`test_switch_roundtrip_preserves_explorer_root_for_parent_navigation`);
  keeping it re-manufactures exactly the pin stage 1 removed, one round trip
  later. So the flag is explicit, as in the first option — but its stated cost
  (a snapshot field and a migration default) is not paid: it is **live-only**,
  and `__post_init__` re-establishes it from the presence of a root at every
  construction. The persisted shape does not move and nothing needs migrating.
  The residual, stated rather than hidden: a derived root that survives a
  restart comes back configured, because a root carried through a snapshot is
  one the workspace deliberately carries and re-deriving it would need the
  pane's shell to be back and observed first, which it is not at launch time.
- **D3 — the observed directory, falling back to the launch directory**, as
  recommended, and the fallback is expressed in the `cd` itself rather than
  checked beforehand: a remote path cannot be stat'd cheaply from here, and the
  shell is already standing in the right place to answer. A reconnect and a
  restore of the same pane now replay the same value, which is what deferring
  the decision to this stage was for.

#### Behaviour changes to be aware of

- **A split of a navigated terminal starts in the navigated directory.** So
  does a restored one, and a saved preset records it. This is the point of the
  stage, and it is what closes ISSUE-2026-045.
- **A terminal pane that round-trips through explorer mode no longer carries a
  root out.** Switching it back to the explorer re-derives from where the pane
  is, instead of pinning to wherever it was the first time. A pane launched as
  a Local Repository or SSH explorer is unaffected — its root is configured and
  still pins.
- **A reconnected SSH pane `cd`s to the directory it was in.** If that
  directory has since been removed, the same line falls back to the launch
  directory rather than leaving the shell wherever `sshd` dropped it.
- **A WSL pane's baked-in startup directory has no fallback.** `--cd` is an
  argument, not a command, so `launch_cwd_applied` suppresses the `cd` that
  carries the `||`. A WSL pane whose observed directory has vanished therefore
  starts wherever `wsl.exe` decides to. Narrow, and the fix — dropping the
  baked-in `--cd` for the observed case — would change every WSL pane's startup
  timing, so it is deliberately not made here.

#### Deliberately *not* done in stage 3

- Stage 4's Git anchor is untouched, so an explorer deliberately opened *above*
  a repository still has no sidebar. ISSUE-2026-044 stays open for that half;
  ISSUE-2026-045 is closed.
- `test_terminals_page_exposes_session_menu_actions` still asserts against
  source text; its one touched line was re-expressed against the new
  derivation rather than converted, because `buildWorkspaceTerminalEntry()` is
  DOM-bound and a genuine behavioural conversion is the `terminals.js` split,
  not this stage.
- D4 is still stage 4's.

### Stage 4 — Decouple the Git anchor from the root

| Change | Files |
| --- | --- |
| One anchor helper; `git/repo` + `git/state` take the browsed path | `web/explorer.py:2628`, `web/api.py:1627-1663` |
| The six mutation routes resolve the same anchor | `web/api.py:1664-1797` |
| Anchor identity in the sidebar revision token | `web/explorer.py:2593` |
| Sidebar names the repository it is anchored on; client sends the browsed path | `web/static/js/explorer-viewer.js:3574`, `web/static/js/explorer-git-watch.js:570` |

**Architecture trigger (`CLAUDE.md` guardrail 6): this is "the next substantial
change to the Git sidebar", so it extracts `explorer-git-sidebar.js` as a pure
move first** — to the same standard as `explorer-tabs.js` and
`explorer-diff.js`: every extracted line byte-identical, `explorer-viewer.js` a
pure deletion, existing tests passing on their existing assertions — and only
then makes the behavioural change on top.

---

## 6. Test plan (behavioural, per `CLAUDE.md`)

- `tests/test_terminal_cwd.py` (new) — the OSC parser, executed against real byte
  streams including split sequences.
- `tests/test_api.py` — mode-switch root resolution matrix: {configured root,
  derived root} × {cwd inside launch dir, cwd in a repo below it, cwd outside
  it} × {probe answers, probe fails}; the agent-pane probe refusal; a split
  inheriting the live directory.
- `tests/test_multi_workspace.py` — capture and restore of a runtime-promoted
  agent pane in a subdirectory, local and SSH.
- `tests/test_api.py` Git-route cases — the sidebar and the mutations resolve one
  anchor; a repo below the root produces a sidebar once browsed into.
- Regression, must pass untouched:
  `test_switch_roundtrip_preserves_explorer_root_for_parent_navigation`,
  `tests/test_session_persistence_contract.py`, and
  `tests/test_multi_workspace.py` across stage 4's pure move.

`make check` (or `python tests/run_tests.py` + `python -m ruff check .`) before
handing any stage back.

---

## 7. Documentation to update on landing

*Done for stage 2: the §3 contract is in the Regression Guardrails of both
`CLAUDE.md` and `AGENTS.md` (guardrail 4, Correctness), `README.md` has a
**Shell integration** section under Configuration plus the new config key, and
`CHANGELOG.md` carries the user-visible entry.*

*Done for stage 3: the configured-versus-derived amendment is in the
explorer-presentation contract of both `CLAUDE.md` and `AGENTS.md`, guardrail 4
gained the persistence half of the rule, `CHANGELOG.md` carries the
user-visible entry, and `docs/testing_issues.md` closes ISSUE-2026-045.
ISSUE-2026-044 stays open: its second half — the Git anchor — is stage 4.*

- `CLAUDE.md` **and** `AGENTS.md`: the §3 contract joins the Regression
  Guardrails — a new rule under *Correctness* ("a pane's working directory is
  observed, never assumed; observation never writes to the shell") and an
  amendment to the explorer-presentation contract for the configured-versus-
  derived root.
- `README.md`: shell integration, what it installs, and the
  `terminal.shell_integration` kill switch.
- `CHANGELOG.md`: user-visible behaviour change — the explorer and saved
  workspaces follow the terminal's current directory.
- `docs/testing_issues.md`: resolve ISSUE-2026-044 and ISSUE-2026-045.

---

## 8. Open decisions

- **D1 — Windows local panes. DECIDED (stage 2): source A alone.** No
  `psutil`, no `ctypes` PEB read. `_local_process_cwd()` returns "" on Windows
  with a comment saying why, so a Windows pane rests on the prompt hook and
  falls back to the probe. Both Windows shells install a hook (cmd through the
  `prompt` builtin, PowerShell by wrapping `prompt`), and PowerShell's was
  verified emitting against a real shell; adding a C extension to the
  dependency set to corroborate a source that already answers is not a trade
  worth making. If it turns out to be, the seam is one function.
- **D2 — How "configured root" is recorded. DECIDED (stage 3): an explicit
  live-only flag.** The recommendation was the no-new-field convention, under
  the proviso that the round trip `tests/test_api.py:5972` pins stays genuinely
  preserved — and it cannot be, because the terminal→explorer switch must store
  the root it resolved for the live explorer to be confined to, after which
  nothing can tell it from a chosen one. So `explorer_root_configured` is
  explicit, as the first option had it; its stated cost is avoided by keeping
  it **live-only** and re-establishing it in `TerminalSession.__post_init__`
  from the presence of a root, so no snapshot field and no migration are
  needed. `_resolve_pane_terminal_directory()` stops back-filling either way.
  That test passes untouched. See stage 3's *Open decisions, decided*.
- **D3 — Does reconnect use the launch directory or the last observed one?
  DECIDED (stage 3): the observed one, falling back to the launch directory.**
  As recommended. `_startup_directories()` answers for both spawn paths, and
  the fallback is expressed in the `cd` itself (`cd A || cd B`, per shell
  family) rather than checked beforehand — a remote path cannot be stat'd
  cheaply from the server, and the shell is already standing in the right place
  to answer. A reconnect and a restore of the same pane now replay the same
  value, which is what deferring the decision to this stage was for.
- **D4 — Should the explorer offer "Set root here"?** *(stage 4 — still open.)* Stage 4 makes the Git
  sidebar follow the browsed directory, which removes most of the need. A
  breadcrumb re-root would also give the user a way to *widen* a root, which is a
  confinement-boundary change and needs its own argument. Recommendation: defer;
  revisit after stage 4 ships.
