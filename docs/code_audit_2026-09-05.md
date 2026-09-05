# GridVibe code audit — 2026-09-05

Reviewed commit: `f8a30515832fdd0b5b277347152e2e019e5e6cad` (`f8a3051`).

This is a point-in-time engineering report, not a maintained behavior contract.
The original review changed no application code. The completion record below
describes the subsequent fixes; evidence, line numbers, and proposed verification
in the original findings refer to the reviewed commit. Finding identifiers are
local to this report, not entries in `testing_issues.md`.

## Implementation and verification — 2026-09-05

**All 19 findings are implemented with regression coverage.** The fixes landed
in `64c723b` (connection/pane ownership and settings transactions) and `2e3e3c3`
(search, safe rename, launcher setup, and final ownership guards). Documentation
was deferred until the user smoke-tested the application. The user subsequently
reported that it "checks out so far" and authorized this documentation update.

| Finding | Implemented behavior | Automated coverage |
| --- | --- | --- |
| F01 | Reserve connection identity before opening; old readers/connectors cannot publish into or retire a replacement. | `tests/test_terminal_transport.py` |
| F02 | Shell responses and busy cleanup follow the captured pane/session across group changes. | `tests/test_pane_callback_ownership.py` + `tests/pane_callback_ownership.cjs` |
| F03 | Browser frame callbacks capture pane/tab/session ownership; disposal unhooks load/link/popup handlers. | `tests/test_pane_callback_ownership.py` + `tests/pane_callback_ownership.cjs` |
| F04 | Incremental UTF-8 decoding preserves split characters and cwd sequences; invalid/incomplete bytes remain visible. | `tests/test_terminal_transport.py` |
| F05 | Per-connection serialized, bounded writes complete partial sends without replaying a whole command. | `tests/test_terminal_transport.py` |
| F06 | Validate root/section shapes and UTF-8; quarantine corrupt config and recover a validated backup without poisoning it. | `tests/test_config_transactions.py` |
| F07 | App Settings and voice preferences merge against the latest read inside the complete config transaction. | `tests/test_config_transactions.py`, `tests/test_api.py`, `tests/test_multi_workspace.py` |
| F08 | Bound and drain both remote streams; retain real completion/status, exact-limit versus overflow, and partial/error reporting. | `tests/test_search_audit_regressions.py`, `tests/test_api.py` |
| F09 | Shared Python matching verifies Git/remote candidates, including regex and Unicode case/word semantics. | `tests/test_search_audit_regressions.py` |
| F10 | Isolated regex workers enforce matching deadlines and bounded span production, and are released on timeout/early completion. | `tests/test_search_audit_regressions.py` |
| F11 | Vosk stop acquires the captured I/O lock or explicitly cancels; old results/errors cannot affect a restarted recording. | `tests/test_voice_stop_ownership.py` |
| F12 | Local POSIX startup closes every untransferred PTY descriptor on failure. | `tests/test_terminal_transport.py` |
| F13 | Exclusively create first-use trust files; refuse unreadable/malformed project or strict-mode system stores while preserving them. | `tests/test_api.py` (`KnownHostsPersistenceTestCase` and host-key policy tests) |
| F14 | POSIX fallback uses native exclusive rename or refuses unsupported guarantees. | `tests/test_rename_noreplace.py` |
| F15 | Apply confined include filtering before local body reads; bound reads even when a file grows. | `tests/test_search_audit_regressions.py` |
| F16 | Parent launcher handles Quit/EOF before setup or launch. | `tests/test_launcher_setup.py` (actual shell control flow) |
| F17 | Reuse fingerprinted, import-checked environments; invalidate changed/broken setup and expose root-launcher `--repair`. | `tests/test_launcher_setup.py` (helper, actual sh and batch control flow) |
| F18 | Convert name/content ranges, snippet offsets, and line lengths to UTF-16 at the JSON boundary. | `tests/test_search_audit_regressions.py` (including Node rendering) |
| F19 | Search debounce/request/paint validate pane ownership; suspension retains results, disposal releases state. | `tests/test_pane_callback_ownership.py` + `tests/pane_callback_ownership.cjs` |

Verification on the final code/test set: **2,584 tests in 121.464 seconds,
OK (10 skipped)** outside the Windows sandbox; Ruff and whitespace checks
passed. The extra POSIX native-rename test is skipped on Windows; native flags,
errors, and collision-preserving fallback behavior are covered with injected
fixtures. Launcher tests execute the real shell/batch branches with inert
installation and application-launch handoffs, so they do not install packages.

After documentation updates and removal of the obsolete probes, the full suite
passed again: **2,584 tests in 135.926 seconds, OK (10 skipped)**. Ruff and
whitespace checks passed; all completion-table test paths and the synchronized
new guardrails were checked. The retained Node harness ran through test discovery.

The user's smoke feedback is positive, but does not individually attest to all
Linux/macOS, live SSH, Vosk, browser/WebView, or installer scenarios listed later.
Those remain integration checks rather than claimed automated/live acceptance.
No cold/warm timing benchmark or live dependency upgrade was performed.

The one-off backend/frontend probes under `docs/audit_repros/` were removed after
their schedules were covered by desired-behavior tests. They asserted the old
defects and were never part of test discovery. `tests/pane_callback_ownership.cjs`
is retained as an active regression harness invoked by the Python suite.

## Outcome and evidence standard

**19 findings: 17 reproduced with focused probes; two confirmed from code.**
The highest-impact problems concern connection replacement, late UI callbacks,
configuration recovery, and search work that escapes its deadline. There are
also two concrete optimization opportunities. No safe dead-code removal was
confirmed.

- **Reproduced:** the current implementation was executed and the reported
  failure observed. Each entry identifies whether the boundary was a real
  filesystem/Git operation, a deferred JavaScript completion, or an injected
  transport/OS failure. A simulated failure is not a claim that a live SSH or
  native-window session was exercised.
- **Code-confirmed:** a specific reachable implementation establishes the
  limitation. These entries distinguish that evidence from an unperformed
  platform or performance test.
- **Manual checks:** prescribed below for actual browser/WebView, SSH, and POSIX
  integration. They supplement the evidence; none of the 19 entries is based
  only on an untested suspicion.

Priority means user impact. Implementation risk means the chance and scope of
regressions while fixing it; these are deliberately separate.

### Verification baseline

| Check | Result |
| --- | --- |
| Existing full suite, outside the sandbox | **2,536 tests, OK, nine skipped**, 135.720 seconds |
| Ruff, including the added Python probes | **All checks passed** |
| Backend audit probes | 13 findings reproduced |
| Frontend audit probes | Four findings reproduced |
| First-party scripts parsed together in template order | Launcher: eight scripts; workspace: 45 scripts; no duplicate lexical declarations |
| Working tree before review | Clean |

The initial sandboxed full run had one failure and one error in Windows Git
process-teardown tests. Both repeated inside the sandbox and both passed outside
it; the full unsandboxed run then passed. They are **not listed as application
bugs**. The affected tests were
`ExplorerGitProcessTreeTestCase.test_a_stalled_remote_returns_the_worker_thread_within_the_bound`
and `ApiRoutesTestCase.test_repo_git_timeout_bounds_a_remote_that_goes_quiet`.

### Scope and limits

The review concentrated on terminal transport and replacement, pane callback
identity, configuration persistence, explorer filesystem operations and search,
voice locking, launcher behavior, and the relevant existing tests. Lifecycle,
workspace snapshots, session-manager boundaries, shared durable-file primitives,
origin checks, and the issue history were also inspected. This was a risk-driven
cross-subsystem audit, not a claim to have inspected every line or every visual
state of the application.

CodeGraph was consulted first. Its index omitted newer modules and returned
source excerpts at stale offsets, so current file contents supplied the final
evidence. The line numbers below refer to the reviewed commit. Vendored
dependencies were not audited for vulnerabilities or available upgrades; no
live remote repositories, microphones, or native windows were used.

## Finding index

Every effect below is the short, user-facing statement requested for the finding.

| ID | Priority | Evidence | User-visible effect |
| --- | --- | --- | --- |
| F01 | High | Reproduced: transport replacement | A newly relaunched terminal can immediately disconnect because the old reader closes its replacement. |
| F02 | High | Reproduced: deferred response | Switching session groups during a shell/agent relaunch can reset and mislabel a different pane. |
| F03 | High | Reproduced: delayed frame load | A previous browser pane can overwrite another pane's URL and save that wrong URL. |
| F04 | Medium | Reproduced: split byte stream | Unicode characters occasionally disappear from SSH and byte-based local terminal output. |
| F05 | High | Reproduced: partial transport write | An SSH paste or startup command can arrive incomplete without an error. |
| F06 | High | Reproduced: temporary config files | Invalid configuration can reset settings, destroy the useful backup on the next save, or prevent startup. |
| F07 | Medium | Reproduced: stale-read interleaving | Two GridVibe processes can overwrite each other's unrelated settings changes. |
| F08 | Medium | Reproduced: capped remote stream | Remote search can report a complete, successful result even when output was cut off or the command failed. |
| F09 | Medium | Reproduced: real Git versus walk | The same regex finds different results when the user toggles ignored files or searches outside Git. |
| F10 | High | Reproduced: bounded child process | A pathological regex can leave search running far beyond its configured deadline. |
| F11 | Medium | Reproduced: refused voice lock | Stopping a busy Vosk recording can race audio processing and lose or misroute its final text. |
| F12 | Medium | Reproduced: simulated POSIX spawn failure | Repeated failed local-shell starts on Linux/macOS leak descriptors and can eventually break new sessions. |
| F13 | High | Reproduced: trust-store read failure | An unreadable SSH trust file can cause a previously known server to be treated as new and accepted. |
| F14 | High | Code-confirmed | On POSIX, a move/rename can overwrite a destination created concurrently by another program. |
| F15 | Low | Reproduced: file-open count | Narrowing a local search with an include filter still spends time reading excluded files. |
| F16 | Low | Reproduced: real POSIX shell | Choosing Quit in `GridVibe.sh` continues toward launch with an empty mode instead of exiting cleanly. |
| F17 | Low | Code-confirmed optimization | Every launcher run performs dependency installation work before opening an already-working application. |
| F18 | Medium | Reproduced: Unicode highlight | Search highlights the wrong characters when an emoji or another non-BMP character precedes a match. |
| F19 | Medium | Reproduced: delayed search callback | A search scheduled in one group can run against the pane that replaces it after a group switch. |

## Detailed findings

### F01 — A retired stream can tear down its replacement

**User effect:** A newly relaunched terminal can immediately disconnect because
the old reader closes its replacement.

**Evidence:** [web/terminal_io.py](../web/terminal_io.py),
`_finalize_stream()` at line 869, `_stream_ssh_output()` at 897,
`_stream_local_output()` at 964, and `_close_ssh_connection()` at 210.
Both stream functions call `_finalize_stream(session_id)` unconditionally in
`finally`. Finalization removes whatever connection currently owns that ID.
There is no comparison with the connection whose reader ended. Local streaming
also re-resolves the registry on each iteration, so an old reader can start
reading a replacement. Connector publication likewise checks session existence,
not a connection generation.

**Reproduction:** Backend probe F01 replaces the registry entry while the old
SSH `recv()` returns EOF. The real stream finalizer removes the replacement,
passes it to shutdown, and marks the session disconnected. No network or real
shell was needed.

**Fix:** Give each connect attempt a generation/identity. Capture it in the
reader and require it for publication, status changes, output publication, and
retirement. Retire only the captured connection. Preserve the existing lock
order and close transports outside shared locks. A missing session check alone
does not fix relaunches that intentionally keep the same session ID.

**Verify:** Force old-reader EOF/error after the replacement is installed; it
must neither close nor mark nor feed output into the new connection. Cover local
and SSH readers, overlapping connectors, and terminal/explorer transitions.

### F02 — Shell relaunch applies a late response to the current grid slot

**User effect:** Switching session groups during a shell/agent relaunch can reset
and mislabel a different pane.

**Evidence:** [terminal-shell.js](../web/static/js/terminal-shell.js),
`relaunchSessionShell()` at line 488. It captures the requested session ID before
`fetch`, but obtains `terminals[index]` again afterward and assigns the returned
session to that object. It then resets its terminal, updates the slot's host
label, and shows its connecting placeholder. The busy set is also slot-keyed.

**Reproduction:** Frontend probe F02 starts a relaunch for A, replaces slot 0 with
B, and resolves A's response. B's `_session.session_id` becomes A, while
`sessionIds[0]` remains B; B's terminal is reset once.

**Fix:** Capture the pane and session ID before waiting. Apply pane-owned state
to that pane, and perform DOM/reset work only if the slot still contains it.
Track busy ownership by pane/session identity. Do not cancel or resend the
already-issued server relaunch when the user changes groups.

**Verify:** Switch groups while a response is delayed, including failure and
return-to-A cases. B must remain untouched and A must leave its busy state.

### F03 — Old browser frames can update and persist another pane

**User effect:** A previous browser pane can overwrite another pane's URL and save
that wrong URL.

**Evidence:** [browser-pane.js](../web/static/js/browser-pane.js),
`browserHookFrameWindow()` at 575, `browserSyncTabFromFrame()` at 625, and
`browserWireFrame()` at 659. Frame callbacks retain a grid index and resolve the
current pane from it. Different panes initialize the same tab IDs (`btab-0`,
etc.), so finding a matching tab ID does not establish frame ownership.

**Reproduction:** Frontend probe F03 fires A's same-origin frame load after B
takes its slot. B's tab URL and `initial_command` become A's redirected URL, and
a presentation write is requested. This reproduces callback behavior; whether
a particular browser finishes an iframe load while detached is a separate
integration check.

**Fix:** Bind frame events and intercepted popup/link handlers to their owning
pane and tab object. Update that owner's state where appropriate; render or
persist through a current owner lookup, never a reused index. Remove handlers
when disposing frames. Check that a tab is still present before adopting a load.

**Verify:** Delay a same-origin redirect, switch groups, and close/reorder tabs.
The incoming browser pane must retain its URL, title, and saved strip. Include
callbacks queued immediately before a frame is detached or disposed.

### F04 — Per-chunk UTF-8 decoding drops split characters

**User effect:** Unicode characters occasionally disappear from SSH and
byte-based local terminal output.

**Evidence:** [web/terminal_io.py](../web/terminal_io.py),
`_stream_ssh_output()` and the byte-reading branches of `_stream_local_output()`
at lines 897 and 964. Each read uses a fresh
`decode("utf-8", errors="ignore")`. A transport read boundary need not be a
character boundary. Both incomplete pieces are discarded.

**Reproduction:** Backend probe F04 sends the UTF-8 encoding of `A€B` split into
`b"A\xe2"` and `b"\x82\xacB"` through the real SSH reader. Published output is
`AB`. The WinPty branch already receives strings and is not this byte-decoding
case. This is separate from the already-fixed explorer preview sampling issue.

**Fix:** Keep one incremental decoder per connection, reset it only on
connection replacement, and finalize it at EOF with an explicit invalid-byte
policy. Feed its decoded stream to both cwd observation and terminal output.

**Verify:** Split two-, three-, and four-byte characters at every boundary,
including inside cwd escape sequences; cover malformed bytes and EOF.

### F05 — SSH input ignores partial sends

**User effect:** An SSH paste or startup command can arrive incomplete without an
error.

**Evidence:** [web/terminal_io.py](../web/terminal_io.py),
`_send_connection_input()` at 317. The SSH branch calls `channel.send(input_data)`
once and ignores its byte count. The installed Paramiko `Channel.send()` contract
explicitly permits a partial send. Local `os.write()` similarly returns a count
that is ignored. `handle_terminal_input()` subsequently tracks the complete
input as though it arrived.

**Reproduction:** Backend probe F05 supplies a channel that accepts three bytes
of `abcdef\n`. The function makes one call, delivers `abc`, and returns normally.
This exercises the documented partial-write condition, not an assumed live
network failure.

**Fix:** Send encoded bytes completely through a bounded per-connection write
path, handling zero writes, closure, and timeouts. Preserve ordering between
concurrent input events without holding the global connection lock during I/O.
Do not automatically replay an entire command after a partial transmission.

**Verify:** A transport accepting only a few bytes per call must receive the exact
input once, including Unicode and the final newline. Test cancellation, timeout,
and two concurrent senders; failed sends must not promote an unsent agent command.

### F06 — Configuration recovery does not validate shape or all decode failures

**User effect:** Invalid configuration can reset settings, destroy the useful
backup on the next save, or prevent startup.

**Evidence:** [web/config.py](../web/config.py), `_load_json_file()` at 127,
`load_config()` at 145, and `_build_runtime_state()` at 302. A parsed non-object
becomes `{}` instead of entering recovery. Reads catch `JSONDecodeError` but not
`UnicodeDecodeError`. Several section normalizers assume a dictionary, so valid
JSON such as `{"ssh": null}` raises during startup/refresh.

**Reproduction:** Backend probe F06 uses temporary files. A primary `[]` with a
valid light-theme backup loads dark defaults; saving replaces the good backup
with `[]`. An invalid UTF-8 primary raises `UnicodeDecodeError`; a null SSH
section raises `AttributeError`. No real configuration was edited.

**Fix:** Validate the root and section shapes before publishing or backing up a
configuration. Route invalid content/encoding into the existing quarantine and
backup recovery mechanism, under the appropriate transaction lock. Decide
explicitly which malformed sections recover versus fall back; do not coerce
an unsupported whole file into a successful empty read.

**Verify:** Invalid UTF-8, array/scalar/null roots, malformed sections, corrupt
backup, and recovery followed by save. Keep a known-good backup and leave the
previous runtime generation intact on a refused refresh. No schema migration
should be necessary for valid existing objects.

### F07 — Configuration's cross-process lock covers the write, not the transaction

**User effect:** Two GridVibe processes can overwrite each other's unrelated
settings changes.

**Evidence:** [web/api.py](../web/api.py), `set_app_config()` at 930, and
[web/config.py](../web/config.py), `save_config()` at 200. The route's read/merge
is protected by an in-process lock. The OS lock begins only inside `save_config`,
after the value to save was computed. Serializing two stale replacements cannot
preserve both updates. Normalization also fills omitted fields from the current
process's cached runtime settings, which can lag another process's save.

**Reproduction:** Backend probe F07 executes the possible interleaving against
a temporary file: read A, read B, change A's theme, change B's font, save A,
save B. The font changes but the theme reverts. This is an executed stale-read
schedule, not a timed two-process stress test. The route's locking establishes
why separate processes can take that schedule.

**Fix:** Introduce one configuration update transaction that acquires the OS lock
before reading, validates and merges against that read, and commits before
releasing. Keep the raw atomic writer as an internal primitive so the transaction
does not recursively acquire the sidecar lock. Normalize partial requests against
the transaction's current value, then publish the committed runtime generation.

**Verify:** Two-process tests updating different fields with barriers around the
read and commit; both changes must survive. Also exercise recovery and refresh.
This concerns multiple processes sharing one installation, not ordinary windows
sharing a single server's `_config_lock`.

### F08 — Remote non-Git search bypasses completion and truncation accounting

**User effect:** Remote search can report a complete, successful result even when
output was cut off or the command failed.

**Evidence:** [web/explorer.py](../web/explorer.py),
`_SftpExplorerBackend.search_lines()` at 2344 and `find_names()` immediately below;
[web/explorer_search.py](../web/explorer_search.py),
`build_remote_grep_command()` at 362 and the remote find builder. These paths
execute `grep`/`find` piped through `head`, suppress diagnostics, read stdout, and
do not inspect command completion. Parsers can drop a cut final record, but the
generators never raise `SearchOutputTruncated`. The payload's output-truncation
flag therefore remains false. These are separate paths from the hardened Git
runner covered by the existing process-bound tests.

**Reproduction:** Backend probe F08 gives the actual SFTP search generator a
capped stream with one complete hit, a partial second hit, and stderr text. It
returns one hit, `error: null`, and `truncated.output: false`. The cap is reduced
in the fixture; production uses multi-megabyte bounds.

**Fix:** Reuse or extract a bounded remote command drain that records completion,
exit status, both streams, and timeout. Do not use the pipeline's final status as
the original command's status. Expose partial results truthfully; distinguish no
matches from permission/command failure. Release channels on every path.

**Verify:** No matches, exit 2, missing command, permission failures, exact limit,
limit-plus-one, missing exit status, and a quiet channel, for content and name
search. Preserve the shared local/remote payload shape.

### F09 — Search has two incompatible regex dialects

**User effect:** The same regex finds different results when the user toggles
ignored files or searches outside Git.

**Evidence:** [web/explorer_search.py](../web/explorer_search.py),
`compile_search_matcher()` at 148 and `build_git_grep_args()` at 172. Validation
and highlighting use Python `re`; Git/remote grep use extended POSIX regex
(`-E`). The local walk uses Python `re`. Toggling ignored files changes the local
engine, not just the eligible file set.

**Reproduction:** Backend probe F09 creates an untracked `sample.txt` containing
`123` in a temporary Git repository. Searching `\d+` with regex enabled returns
zero hits through Git and one through the local walk when `ignored=1`.

**Fix:** Establish one documented regex semantics boundary and make validation,
matching, and highlighting agree. Use acceleration only for queries it can
represent faithfully. Do not silently turn an accepted Python expression into a
different POSIX expression or broaden results when switching engines.

**Verify:** The same corpus/query matrix through Git, local fallback, and remote
fallback: character classes, escaped classes, grouping, anchors, whole-word,
case folding, invalid patterns, and non-ASCII text. Coordinate with F10 and F18.

### F10 — Regex matching cannot be interrupted by the search deadline

**User effect:** A pathological regex can leave search running far beyond its
configured deadline.

**Evidence:** [web/explorer_search.py](../web/explorer_search.py),
`walk_matches()` at 289, `collect_search_payload()` at 423, and name matching in
`collect_find_payload()`. Deadline checks surround the iteration, but Python
regex `search`/`finditer` themselves have no deadline. One expensive match can
consume the whole request long before the next check is possible.

**Reproduction:** Backend probe F10 runs the actual local walker on a file with
30 `a` characters followed by `!`, searching `(a+)+$`. With a 0.05-second search
deadline, the child still has not returned after four seconds; the parent kills
that isolated interpreter. The normal app was never given this query.

**Fix:** Put arbitrary regex execution behind an enforceable time/CPU bound,
such as a bounded worker process or an engine with suitable timeout guarantees.
Also bound match-range materialization before windowing. Merely adding more
`time.monotonic()` checks around the same blocking matcher cannot fix this.

**Verify:** Catastrophic backtracking, zero-width/high-match-count patterns,
long single lines, cancellation, and repeated requests. Assert bounded latency
and resource release, not just a final `truncated.deadline` flag. Keep adversarial
cases isolated from the main test-runner process.

### F11 — Vosk stop continues even when its session lock was not acquired

**User effect:** Stopping a busy Vosk recording can race audio processing and
lose or misroute its final text.

**Evidence:** [web/voice.py](../web/voice.py),
`_stop_vosk_voice_session()` at 925. It records
`acquired = session_lock.acquire(timeout=5)` but always proceeds with EOF send,
receive, and close. The boolean controls only whether the lock is released.
Meanwhile `_handle_vosk_audio_chunk()` can still be using the same WebSocket.

**Reproduction:** Backend probe F11 makes acquisition return false. The real
stop path nevertheless invokes all three WebSocket operations. The overlapping
operations are reproduced; the exact final-text symptom depends on network and
service timing.

**Fix:** Treat failed acquisition as a distinct stop outcome. Serialize the
flush or deliberately discard/close through an explicit cancellation path;
never start a concurrent receive. Keep connection and owner identity together
so an old error cleanup cannot remove a newly started recording's entry.

**Verify:** Delayed audio receive, stop timeout, service failure, and immediate
restart. Final text must be emitted at most once to its owner, and timeout must
produce an observable result with all holds released.

### F12 — POSIX spawn failure leaks both PTY descriptors

**User effect:** Repeated failed local-shell starts on Linux/macOS leak
descriptors and can eventually break new sessions.

**Evidence:** [web/terminal_io.py](../web/terminal_io.py),
`_connect_local_session()` at 1458. The POSIX branch calls `pty.openpty()`, then
`Popen()`, and closes the slave only after a successful spawn. If spawn raises,
neither descriptor is in the registry, and the exception path does not close
them. Repeated retries accumulate resources.

**Reproduction:** Backend probe F12 executes that branch with fake descriptors
101/102 and an injected missing-shell failure. Neither descriptor is closed.
This is an OS-call-level reproduction of the POSIX branch on the Windows host,
not a native Linux descriptor-count measurement.

**Fix:** Own both descriptors locally until successful transfer to the
connection. Close the slave in a guaranteed cleanup path and the master on any
failure before transfer; avoid double-closing transferred descriptors.

**Verify:** POSIX spawn failures and a real successful spawn, with descriptor
counts stable across repeated failures. Include a session closed during startup.

### F13 — Trust-store read failures leave permissive SSH acceptance enabled

**User effect:** An unreadable SSH trust file can cause a previously known server
to be treated as new and accepted.

**Evidence:** [web/hostkeys.py](../web/hostkeys.py),
`_load_persistent_host_keys()` at 21 and `_apply_host_key_policy()` at 63. A load
failure is logged and swallowed, after which `auto-add` and `known-hosts` still
install accepting policies. Without the saved key, a changed key cannot be
compared with its previous value. This is an existing documented fallback, not
a claim that the normal readable-store path accepts changed keys; strict mode
retains its rejecting missing-key policy.

**Reproduction:** Backend probe F13 injects a trust-store read failure and
observes `AutoAddPolicy` installed afterward. It does not perform a network
impersonation test or edit the user's `.known_hosts`.

**Fix:** Distinguish an absent, successfully created first-run store from an
unreadable existing store. Refuse that connection with a repairable trust-store
error when known-key verification is unavailable. Preserve the failed file.
Keep intentionally accepting first-use behavior for valid `auto-add` stores.

**Verify:** Missing store, unreadable/corrupt existing store, known unchanged
key, changed key, and strict/system-store behavior. Make the error visible in
terminal, explorer, and preflight consumers through the shared policy owner.

### F14 — The POSIX no-replace fallback is check-then-overwrite

**User effect:** On POSIX, a move/rename can overwrite a destination created
concurrently by another program.

**Evidence: code-confirmed.** [web/explorer.py](../web/explorer.py),
`_LocalExplorerBackend.fs_rename_noreplace()` at 1908. Windows uses its
non-replacing rename behavior; regular POSIX files normally use exclusive
hard-link creation. Directories, and files whose hard-link attempt takes a
fallback branch, reach `lstat(destination)` followed by ordinary `os.rename()`.
An external writer can create the destination between those calls. POSIX rename
can replace an existing file or empty directory. In-process path claims cannot
exclude that writer. The source comment itself acknowledges this residual race.

**Fix:** Use an actual atomic no-replace facility on supported platforms. If
that guarantee is unavailable, explicitly refuse the operation or choose a
documented safe alternative; do not present a pre-check as atomic exclusion.
Preserve Windows and SFTP behavior. No production filesystem race was attempted.

**Confirmation test for POSIX:** In a disposable fixture, synchronize a writer
to create a destination after the final check but before rename. Assert that
its contents/directory and the source both survive a collision refusal. Test
directories and forced hard-link fallback separately. This is an automated
race-injection test to run during implementation, not something to try against
valuable files manually.

### F15 — Include filters are evaluated after excluded files have been read

**User effect:** Narrowing a local search with an include filter still spends
time reading excluded files.

**Evidence:** [web/explorer_search.py](../web/explorer_search.py),
`walk_matches()` at 289. It resolves/stats the candidate, reads its whole allowed
body, checks binary content, then computes the relative path and applies the
include filter. The filter could reject it before body I/O and decoding.

**Reproduction:** Backend probe F15 searches with `include=*.py` in a temporary
directory containing only `excluded.log`. There are zero results, but one file
body open. This establishes wasted work; no unmeasured speedup is claimed.

**Fix:** Compute the confined relative identity and apply the include predicate
before body reads. Preserve current symlink/confinement semantics. Make reads
bounded even if a file grows after stat. Keep this small optimization separate
from regex-engine changes.

**Verify:** Instrument open/read counts with included and excluded files and
links. Benchmark a mixed source/log tree; compare elapsed time, bytes read, and
identical results. Include-only changes must not alter which paths are safe.

### F16 — Quit exits the command substitution, not the launcher

**User effect:** Choosing Quit in `GridVibe.sh` continues toward launch with an
empty mode instead of exiting cleanly.

**Evidence:** [GridVibe.sh](../GridVibe.sh), `prompt_mode()` and line 104,
`LAUNCH_MODE=$(prompt_mode)`. Choice 3 calls `exit 0` inside the substitution's
subshell. The parent receives an empty string and continues to
`webview_launcher.py --mode ""`. An input EOF similarly has no checked failure
at the assignment.

**Reproduction:** Backend probe F16 runs the actual prompt function with input
`3` in Git Bash's POSIX shell. Installation and real launch are excluded from
the harness. The parent reaches the launch marker with an empty mode.

**Fix:** Return an explicit quit result to the parent and handle it there; also
check assignment/read failure. Ask for the launch choice before doing work that
Quit should avoid. Keep browser/native selection behavior unchanged.

**Verify:** Native, browser, quit aliases, invalid input followed by a valid
choice, and EOF in an actual `sh` test. Quit/EOF must invoke no Python launcher.

### F17 — Working installations still run package upgrades on every launch

**User effect:** Every launcher run performs dependency installation work before
opening an already-working application.

**Evidence: code-confirmed optimization.** [GridVibe.sh](../GridVibe.sh), lines
93–109, and [GridVibe.bat](../GridVibe.bat), lines 103–115 and the desktop/voice
branches. Both scripts run pip tooling upgrades and dependency installation
before normal startup, even with a usable environment. Failures in those steps
abort launch. This is an avoidable startup dependency on installation work;
actual delay and offline outcomes depend on pip cache/network state and were
not benchmarked. No packages were installed or upgraded during this audit.

**Fix:** Distinguish first install, requirements/environment change, explicit
repair/update, and ordinary launch. A current, import-checked environment should
start directly. Invalidate any setup marker when requirements or interpreter
identity changes, and write it only after successful installation/verification.

**Verify:** Cold install, unchanged warm launch, changed requirements, moved or
broken environment, optional desktop/voice changes, and offline warm launch.
Measure cold/warm times before choosing a caching strategy. Keep a discoverable
repair action; do not silently skip a failed required dependency check.

### F18 — Search spans mix Python code points with JavaScript UTF-16 offsets

**User effect:** Search highlights the wrong characters when an emoji or another
non-BMP character precedes a match.

**Evidence:** [web/explorer_search.py](../web/explorer_search.py),
`collect_search_payload()` and `collect_find_payload()` produce Python string
indices. [explorer-search.js](../web/static/js/explorer-search.js),
`explorerSearchHitTextHtml()` at 198 and `explorerSearchHitSpans()` consume them
with JavaScript `slice`. `text_offset`/`line_length` have the same unit mismatch.
The Source paint's fallback re-find cannot reliably repair this, because the
matched substring it is given has already been sliced with the wrong units.

**Reproduction:** Frontend probe F18 gives the renderer `🚀 hit` with Python's
span `[2, 5]` for `hit`. The rendered mark contains ` hi`, leaving `t` outside.

**Fix:** Define one offset unit at the response boundary and consistently convert
spans, snippet offsets, and lengths. UTF-16 units are convenient for DOM ranges;
alternatively convert code-point offsets once on arrival. Audit name-search
consumers under the same contract rather than adding per-render guessing.

**Verify:** Multiple astral characters, multiple hits, windowed snippets, name
search, and Source highlighting; also retain ASCII/BMP and combining-mark cases.

### F19 — Repository-search work is not fully owned by its pane

**User effect:** A search scheduled in one group can run against the pane that
replaces it after a group switch.

**Evidence:** [explorer-search.js](../web/static/js/explorer-search.js),
`scheduleExplorerRepoSearch()` at 188 schedules `runExplorerRepoSearch(index)`.
The callback resolves whichever pane is now in that index. The search's own
abort controller and debounce timer live in `_explorerRepoSearch`, outside the
request slots canceled by [explorer-viewer.js](../web/static/js/explorer-viewer.js)
`explorerReleasePaneWork()` at 4182. Completion checks a per-state sequence but
still paints by index. Group detach and permanent disposal need distinct rules.

**Reproduction:** Frontend probe F19 schedules A's debounce, replaces the slot
with B, and fires it. The actual scheduled callback invokes the runner against
B. The runner's first action is to abort B's existing search; depending on B's
query it then clears or starts B's results. No live browser timing claim is
needed to establish the ownership error.

**Fix:** Capture and validate pane/session ownership in timers and before
slot-directed paints. Add search-specific suspend/dispose cleanup, including
its controller and debounce. Preserve cached-pane results intentionally rather
than allowing an old timer to choose a new owner.

**Verify:** Switch groups inside the debounce interval, close a pane with search
in flight, and restore a cached group. A must not abort or initiate B's work;
discarded panes must leave no outstanding search callbacks or requests.

## Dead code, collisions, and improvements not promoted to defects

- **No safe deletion candidate confirmed.** Python top-level function screening
  found no unreferenced candidate after checking first-party code, templates,
  and tests. The JavaScript screen surfaced `shortcutHelpChordNames`, but the
  shortcut/README contract tests call it. Root-level API/session-manager/cleanup/
  launcher shims are intentional compatibility surfaces, not redundant copies
  to delete. This screening does not prove every method/export is live.
- **No duplicate lexical declaration collision** appeared when each page's
  first-party scripts were parsed together. That check does not prove absence
  of runtime function overwrites or keyboard conflicts. The confirmed collisions
  are ownership collisions in F01–F03/F19 and semantic collisions in F09/F18.
- **No speculative module split added.** The documented explorer and workspace
  extraction triggers should guide future functional changes. File size alone
  was not turned into a new defect or a mandatory rewrite.
- **Existing safeguards are meaningful.** The suite covers presentation
  ordering, durable-store recovery, explorer bounds, window lifecycle, and many
  previous group-switch regressions. The new probes target boundaries those
  passing cases did not establish; passing tests were not treated as evidence
  that these neighboring paths are correct.
- **No unrelated dependency-upgrade backlog.** A vulnerability or performance
  claim about a vendored dependency needs its own evidence and was not inferred
  from version age.

## Running the maintained regression checks

From the repository root on Windows:

```powershell
.venv/Scripts/python.exe tests/run_tests.py
.venv/Scripts/python.exe -m ruff check .
```

On POSIX, use the project's `.venv/bin/python` in place of the Windows Python
path. Install Node to execute the frontend behavioral tests; the Python wrapper
skips them when Node is absent. To run the retained pane harness directly:

```powershell
node tests/pane_callback_ownership.cjs
```

The original defect probes have been removed. The reproduction descriptions
above preserve the historical schedules; the maintained tests listed in the
completion table assert the repaired behavior.

## Integration checks needing the user's environment

These are the remaining smoke checks after the relevant fixes. They distinguish
an already-confirmed code failure from its platform-dependent presentation.

| Check | Safe setup and action | Required result | Findings |
| --- | --- | --- | --- |
| Shell replacement | Two disposable groups; delay a relaunch response, switch to the other group, then return. Repeat a shell/agent change several times. | Incoming pane never resets or changes identity; relaunched pane connects and remains connected. | F01, F02 |
| Browser completion | Two groups with same-origin browser panes; delay a redirect/load while switching groups and closing tabs. Save and restore the strip. | Only the owner adopts the URL/title; no wrong URL survives restore. | F03 |
| Real SSH I/O | Disposable shell; print Unicode repeatedly and paste a long harmless line into a text receiver, not an executable shell command. | Exact bytes/characters arrive once; no missing final newline or dropped glyphs. | F04, F05 |
| Busy Vosk stop | Record against a deliberately delayed local test service, then stop/restart. | Bounded stop, one final result or an explicit failure, no leftover recording ownership. | F11 |
| POSIX resources | Automated disposable spawn-failure and synchronized rename fixtures on Linux/macOS. | Descriptor counts remain stable; concurrent destination creation always causes refusal without overwrite. | F12, F14 |
| Remote fallback search | SSH explorer outside Git; no-match, denied-read, large-output and stalled-command fixtures. | Truthful errors/partial indicators and bounded completion. | F08, F09, F10 |
| Search ownership/Unicode | Type a query then immediately switch groups; search `hit` in `🚀 hit`. | No cross-group request effects; exactly `hit` is marked in results and Source. | F18, F19 |
| Launcher paths | Disposable checkout/environment; exercise Quit/EOF and a warm offline launch. | Quit launches nothing; working environment starts without an install step. | F16, F17 |

Do not test configuration corruption, changed host keys, or filesystem overwrite
races against the user's working installation. Use isolated fixtures for those.

## Original staged implementation plan (historical)

The main sequence increases implementation scope and shared-state risk. Within
each area, fix the highest user impact first. Stages 1–2 are small changes that
can be reviewed independently; stages 3–6 cross transport, persistence, engine,
or platform boundaries and need their own characterization. Stage 7 is an
optional startup-workflow optimization, placed last because it changes setup
policy rather than repairing runtime correctness. This ordering does not imply
that a low-priority optimization outranks connection or data-integrity fixes.

| Stage | Findings | Implementation risk / scope | Work and exit gate |
| --- | --- | --- | --- |
| 0 — Characterize | All | Low; tests and fixtures | Turn each scheduled change's probe into desired-behavior coverage. Establish POSIX and remote fixtures for unperformed platform checks. Record baseline results; make no application changes in this stage. |
| 1 — Small isolated corrections | F04, F12, F16, F18 | Low; individual functions and a response-unit contract | Incremental decoding; exception-safe PTY ownership; parent-handled Quit/EOF; consistent Unicode offset units. Each lands with its focused tests. Run terminal/launch/search suites and Ruff. |
| 2 — Frontend ownership | F02, F03, F19 | Low–medium; three UI adapters | Capture pane/session/tab identity, guard slot paints, and clean up search timers/controllers. Use deferred callbacks across group replacement, disposal, and cached restore. Complete browser/WebView smoke checks. No new persisted fields. |
| 3 — Connection and recording lifetime | F01, F05, F11 | Medium–high; transport concurrency | Connection generations and identity-checked retirement first; then bounded, ordered complete writes; then serialized Vosk stop. Test old EOF/error and overlapping connector publication, partial writes, timeout, and restart. No global locks held over transport I/O. |
| 4 — Durable settings and trust | F06, F07, F13 | Medium–high; recovery and cross-process behavior | Validate/recover configuration; move read/merge/write under one OS lock; normalize against the locked read; refuse unreadable existing trust stores. Exercise two-process schedules and permission/corruption injection. Valid on-disk shapes stay compatible; preserve evidence and known-good backups. |
| 5 — Search semantics and enforceable bounds | F08, F09, F10, F15 | High; shared local/remote search pipeline | Fix remote completion accounting and isolate arbitrary regex work. Establish the common regex contract before changing acceleration. Apply include filters before body reads as a separate small patch. Verify a cross-engine corpus, adversarial queries, exact/over-limit output, cancellation, and measured filtered-read savings. |
| 6 — Filesystem collision guarantees | F14 | High; platform-specific mutation behavior | Replace the POSIX fallback with an atomic no-replace guarantee or explicit refusal. Run synchronized external-writer tests on Linux/macOS and preserve Windows/SFTP tests unchanged. Ship only once the collision path demonstrably preserves both objects. |
| 7 — Startup/setup separation | F17 | Low code risk, medium workflow scope | Add a warm launch path with explicit install/repair and requirements/interpreter invalidation. Preserve optional dependency choices. Benchmark cold/warm/offline behavior; verify a broken environment still gets actionable repair. |

For stages 3–6, avoid combining behavioral repair with an unrelated extraction.
If the documented extraction trigger is crossed, characterize and perform the
pure move first, with its existing assertions intact, then implement the repair.
Do not broaden search or filesystem permissions to make a test pass.

**Completion gates for the overall plan:** every F01–F19 item has desired-behavior
coverage or a documented acceptance result; the existing full suite and Ruff
pass; applicable Windows, POSIX, SSH, browser/WebView, and Vosk smoke checks are
recorded; failures preserve recoverable state and release resources. Update the
maintained behavior documents with the new rules themselves, rather than citing
this audit as a contract. No fixes, commits, or issue-status changes were made
during the original review; subsequent implementation and verification are
recorded at the top of this report.
