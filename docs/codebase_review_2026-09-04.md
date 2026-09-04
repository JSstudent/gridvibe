# GridVibe codebase review

**Review date:** 2026-09-04  
**Revision:** `f5f2303aac95` (`szua_gridvibe_agents-wrk`)  
**Scope:** application and test code only. Existing documentation was deliberately not used as evidence.  
**Outcome:** three urgent security findings, multiple reproducible/concrete concurrency and timeout defects, and a generally strong persistence and explorer-safety foundation.

## 1. Executive summary

GridVibe is unusually deliberate about state normalization, bounded explorer operations, stale frontend responses, and durable workspace transactions. The automated test surface is also substantial: the full runner discovered 2,536 tests, and many browser policies are exercised as behavior in Node rather than merely checked as text.

The current revision should nevertheless not be treated as safe for untrusted network exposure. The server trusts the incoming `Host` header when deciding whether an HTTP or Socket.IO origin is same-origin. A hostile hostname that resolves to the GridVibe address can therefore pass both guards. Because the app has no authentication, exposes terminal input, and returns decrypted saved SSH passwords from two GET routes, that boundary failure has a high impact. Independently, binding to a non-loopback address makes every API and terminal directly available to peers on that network.

The most immediate correctness defect is reproducible on Windows: the shared process-tree timeout does not reliably terminate Git transport helpers. The full suite has one failure and one cleanup error, and both targeted tests reproduce. A stalled Git operation can outlive the advertised deadline and retain repository handles.

The next group of risks is concurrency-related. Terminal stream finalizers identify a connection only by session ID, so an old stream can close a replacement connection. Reconnect, shell/mode transition, split capacity, lifecycle save, Git mutations, voice ownership, and native window opening each contain a check-then-act interval that is not protected by one operation-level claim.

Recommended release order:

1. Repair the Host/origin/authentication boundary and stop returning reusable passwords to general page JavaScript.
2. Fix Windows child-tree ownership and make per-pane connection generations explicit.
3. Serialize pane transitions, Git mutations, workspace window creation, and capacity claims.
4. Bound Paramiko exit-status and SFTP operations, then harden configuration and backup durability.
5. Split the largest frontend/backend modules and add safe per-repository caching only after correctness is stable.

## 2. Review method and limits

The review covered the startup entry points, Flask/Socket.IO boundary, session manager, local/WSL/SSH terminal lifecycle, workspaces, presentation and lifecycle persistence, explorer reads and mutations, Git integration, browser pane, voice input, native WebView launcher, self-update, utilities, and representative frontend modules. Tests were read alongside production code to distinguish intended contracts from accidental behavior.

Checks performed:

- `ruff check` over `web`, `sessions`, `services`, `utils`, and the root Python entry points: **passed**.
- `.venv\Scripts\python.exe tests\run_tests.py`: **2,536 tests; 1 failure, 1 error, 9 skipped; about 280 seconds**.
- The two failing Git timeout tests were rerun directly: **both reproduced** in 8.3 seconds.
- `node --check` on `terminals.js`, `explorer-viewer.js`, and `launcher.js`: **passed**.
- A Flask test-client request using `Host: evil.example:5050` and `Origin: http://evil.example:5050` reached the `/api/sessions` route and returned its payload-validation `400`; the origin guard did not return `403`.
- Targeted static searches for unbounded subprocess/SSH calls, connection registries, read-modify-write sequences, credential flow, path confinement, frontend globals, and unused symbols.

This was not a penetration test and did not use live SSH/SFTP servers, a microphone, WSL distributions, or a real pywebview GUI. Findings marked “code-path” should receive deterministic concurrency/fault-injection tests before and after a fix. No coverage percentage was collected. Existing prose documentation was excluded as requested.

Priority means:

- **P0:** address before exposing or shipping the affected capability.
- **P1:** high-impact correctness or resource-ownership problem; fix next.
- **P2:** hardening issue that can cause loss, hangs, or surprising behavior in narrower conditions.
- **P3:** maintainability/performance debt or a low-impact edge case.

## 3. Findings index

| ID | Priority | Area | Finding | Evidence |
|---|---:|---|---|---|
| GV-001 | P0 | HTTP / Socket.IO | Incoming `Host` and forwarded headers are trusted as the same-origin authority | Reproduced + code-path |
| GV-002 | P0 | Network exposure | Non-loopback mode has no authentication or capability boundary | Code-path |
| GV-003 | P0 | Credentials | Decrypted saved SSH passwords are returned to general browser GET routes | Code-path |
| GV-004 | P1 | Terminal I/O | An old stream finalizer can close and disconnect a replacement connection | Code-path |
| GV-005 | P1 | Sessions | Reconnect and mode/shell transitions are not one serialized operation | Code-path |
| GV-006 | P1 | Sessions | Split capacity enforcement has a check-then-append race | Code-path |
| GV-007 | P1 | Socket.IO | Event payloads, room counts, and session access are insufficiently validated | Code-path |
| GV-008 | P1 | Voice | Recording ownership and failed-start cleanup are incomplete | Code-path |
| GV-009 | P1 | Process bounds | Windows Git helper processes escape timeout ownership | Reproduced |
| GV-010 | P1 | Agent detection | SSH CLI detection can block forever in `recv_exit_status()` | Code-path |
| GV-011 | P1 | Remote Git | The final remote exit-status read is outside the command deadline | Code-path |
| GV-012 | P1 | SFTP | Explorer SFTP operations have no operation deadline | Code-path |
| GV-013 | P1 | Git mutations | Mutations are not serialized per repository; scoped commit validation is racy | Code-path |
| GV-014 | P1 | Lifecycle | Save can accept an empty flush after the last window disconnects | Code-path |
| GV-015 | P1 | Native window | Concurrent open requests can create duplicate, partly untracked windows | Code-path |
| GV-016 | P1 | Self-update | Updating files underneath a running server creates a mixed-version process | Code-path |
| GV-017 | P1 | Configuration | `--config` affects bind settings but not the global runtime configuration | Code-path |
| GV-018 | P2 | SSH trust store | Concurrent first connections can lose/corrupt known-host updates | Code-path |
| GV-019 | P2 | Configuration | Valid JSON with wrong section types can crash startup; booleans are coerced incorrectly | Reproduced + code-path |
| GV-020 | P2 | Native restart | The normal Windows restart branch discards original CLI arguments | Code-path |
| GV-021 | P2 | Persistence | Backups are non-atomic and replaced state is not directory-fsynced | Code-path |
| GV-022 | P2 | Search | Regex and special/growing files can escape search time/memory bounds | Code-path |
| GV-023 | P2 | Explorer writes | Revision and root checks are vulnerable to external check/use races | Design limitation |
| GV-024 | P2 | Child processes | Agent probes and voice dependency installation do not share tree-bounded execution | Code-path |
| GV-025 | P2 | Logging | Commands and paths can leak tokens and sensitive workspace details | Code-path |
| GV-026 | P2 | App settings | Two independent saves are presented as one atomic operation | Code-path |
| GV-027 | P2 | Web hardening | Main pages lack a response-wide security-header policy | Code-path |
| GV-028 | P3 | Filesystem names | Character-count limits do not model byte limits or suffixed collision names | Code-path |
| GV-029 | P3 | Update utility | Requirement-file rewrites are direct and can leave a truncated file | Code-path |
| GV-030 | P3 | Architecture | Very large classic-script/global modules raise collision and change risk | Measured |
| GV-031 | P3 | Performance | Git state/graph work is repeatedly recomputed per pane and per refresh | Code-path |

## 4. Detailed findings

### GV-001 — untrusted Host/origin equivalence (P0)

**Affected code:** `web/app.py:78-112`, `web/app.py:171-212`.

`_allowed_write_origin_netlocs()` always inserts `request.host` into the HTTP write allowlist. `SameOriginPolicy` likewise accepts an origin equal to `_request_origin(environ)`, and `_request_origin()` builds that value from unvalidated `Host`, `X-Forwarded-Host`, and `X-Forwarded-Proto` headers. The HTTP path compares only `netloc`, so an explicitly configured origin's scheme is not part of the verdict.

This is the classic local-service/DNS-rebinding failure mode: if `evil.example` resolves to loopback or the listening LAN address, a page at `http://evil.example:5050` sends that same value as both Host and Origin. GridVibe treats the attacker-supplied pair as proof of same origin. The test-client probe reached route validation instead of being rejected by the guard.

Impact includes creating/closing sessions, sending terminal input over Socket.IO, explorer and Git mutations, lifecycle actions, and reading the credential endpoints described in GV-003. Trusting forwarded headers also lets a direct client synthesize a proxy identity even when no trusted reverse proxy is present.

**Recommendation:** derive one canonical set of allowed scheme/host/port tuples from resolved server configuration. Validate `Host` before routing, never add the request Host to its own allowlist, and ignore forwarded headers unless an explicit trusted-proxy mode is enabled. Add a random per-process/session capability or CSRF token to state-changing HTTP and Socket.IO handshakes/events. Test hostile Host, DNS-rebinding-shaped requests, scheme mismatch, IPv6 forms, default ports, and configured reverse-proxy origins.

### GV-002 — unauthenticated non-loopback exposure (P0)

**Affected code:** `main.py`, `web/webview_launcher.py`, `web/app.py`, all `/api/*` routes and Socket.IO handlers.

`--host 0.0.0.0` and other non-loopback binds are accepted, but the application has no user authentication or session authorization. Origin/CORS checks are browser controls, not authentication: a process on the LAN can call the API or Socket.IO server directly and choose any Host/Origin headers. The API controls shells and filesystem/Git mutations, so this is remote command authority, not merely UI exposure.

**Recommendation:** refuse non-loopback binds unless an explicit “unsafe unauthenticated network mode” switch is supplied, or implement an authenticated mode with TLS/reverse-proxy guidance and a bearer/session token for both transports. Display the effective security state prominently at startup and in the UI. Do not use CORS as the access-control layer.

### GV-003 — decrypted passwords returned to page JavaScript (P0)

**Affected code:** `web/saved_sessions.py:605-613`, `web/saved_sessions.py:672-722`, `web/saved_sessions.py:811-826`, `web/api.py:2547-2550`, `web/api.py:2776-2785`.

The store correctly encrypts passwords at rest, but `_normalize_stored_payload()` decrypts them into the in-memory config. `GET /api/session-config` returns the last preset's complete config, and `GET /api/saved-sessions/<id>` returns another complete config. `_saved_session_response(..., include_config=True)` does not redact `ssh.password`.

Consequently any successful same-origin script, DNS-rebinding bypass, browser extension with page access, or future XSS can retrieve reusable SSH credentials. The narrower `/api/session-targets` response is secret-free, but it advertises `has_password` and the preset ID needed for the follow-up fetch.

**Recommendation:** keep credentials server-side. Let launch requests refer to a saved credential/preset ID and merge the secret only inside the server transaction. If password reveal is a required feature, expose it through a separate short-lived, user-confirmed action rather than routine configuration GETs. Prefer the OS credential vault for new storage. Add response-shape tests asserting that no ordinary GET or Socket.IO payload contains `password`, encrypted or plaintext.

### GV-004 — stale terminal stream can close a replacement (P1)

**Affected code:** `web/terminal_io.py:211-219`, `web/terminal_io.py:869-961`, `web/terminal_io.py:964+`, connection insertion near `web/terminal_io.py:1423-1439` and `1530+`.

Each stream captures a connection but its `finally` block calls `_finalize_stream(session_id)`. The finalizer updates the session status and `_close_ssh_connection(session_id)`, which pops whatever connection is currently stored under that ID. If a reconnect or shell/mode transition installs generation B before generation A's stream finishes unwinding, A can close B and mark the pane disconnected. Error cleanup similarly checks only whether the ID is present, not whether the stored object is the connection that failed. Concurrent connectors can overwrite the registry entry without disposing of the loser.

**Recommendation:** allocate a monotonically increasing generation/token per pane. Store `(generation, connection)`, pass both into the stream, and use compare-and-swap cleanup: pop/update status only if the registry still contains that exact generation/object. A connector must claim the generation before dialing and discard itself if superseded. Test delayed old-stream finalization after a replacement is connected.

### GV-005 — pane transitions are not serialized end to end (P1)

**Affected code:** `web/api.py:2947-2964`, `web/session_shell.py:250-262`, `web/session_modes.py:187-189`, `316-318`, `401-404`.

Reconnect checks status, closes the connection, changes status, and launches a background connector as separate operations. Two concurrent reconnect requests can both observe an eligible status and start two connectors. Mode and shell changes correctly validate before mutation, but their metadata update, close, status update, broadcast, and restart are also separate calls. A reconnect or second transition can interleave between any of them.

**Recommendation:** add a per-pane transition claim in `SessionManager`. One method should validate expected state/revision, allocate the new connection generation, mutate metadata/status, and return a transition plan while holding the claim. Side effects may run outside the manager lock, but they must carry the generation and become no-ops when stale. Return `409` for a transition already in progress.

### GV-006 — split capacity check/append race (P1)

**Affected code:** `web/api.py:2838-2921`, `sessions/manager.py:762-776`.

The route reads the group's sessions and checks `max_sessions`, then later calls `append_session_to_group()`. The append is individually locked but does not enforce the cap. Two concurrent requests at `max_sessions - 1` can both pass and both append.

**Recommendation:** implement `append_session_if_below_limit(group_id, limit, fields)` under the manager lock and return an explicit capacity conflict. Generate the title/count from the committed position, not the earlier snapshot. Add a barrier-based two-request test.

### GV-007 — Socket.IO validation and authorization gaps (P1)

**Affected code:** `web/api.py:3235-3383`, plus voice handlers at `3493-3563`; registries in `web/terminal_io.py`.

Handlers assume `data` is a mapping, accept unbounded/arbitrary session IDs, and generally do not require that the current socket joined or owns the pane. `join_session` joins a room even when no session exists, and caps only the number of tracked clients, not rooms per client. `clear_terminal_buffer` creates a buffer for any supplied ID. Terminal input and resize can target any live connection without room membership. Malformed/non-mapping payloads can raise handler exceptions, and many long distinct IDs can grow per-client sets, rooms, or buffers.

The absence of authentication makes this worse, but the validation remains necessary even in local mode because stale/multiple windows and buggy clients can trigger it.

**Recommendation:** use one schema validator for every socket event; bound ID and data sizes; require a live session; cap joined rooms and recordings per client; require membership/ownership for input, resize, clear, audio, and stop; and acknowledge errors without embedding raw exception text in terminal output. Add fuzz-shaped payload tests (`null`, list, number, nested/unhashable IDs, huge IDs/audio) and cross-client access tests.

### GV-008 — voice ownership and start cleanup are incomplete (P1)

**Affected code:** `web/api.py:3493-3563`, `web/voice.py:629-704`, `web/voice.py:733+`, `web/voice.py:925-955`.

`voice_start` registers ownership before the backend actually starts. If backend setup fails, the owner/engine row can remain. Starting the same pane from another socket intentionally transfers registry ownership, but audio and stop do not verify the caller against that owner, so the old socket can continue feeding or terminate the new recording. There is no generation to keep late chunks from a previous recording out of a replacement.

In `_stop_vosk_voice_session`, failure to acquire the per-session lock within five seconds still falls through to `ws.send()` and `ws.recv()`, concurrently with the operation whose lock timed out.

**Recommendation:** start into a private generation, publish ownership only after successful setup, and roll back all resources on failure. Check `(client_id, generation)` on every audio/stop event. If the Vosk lock times out, close/cancel the socket or return a retryable error; do not operate on it unlocked. Bound simultaneous recordings per client and globally.

### GV-009 — Windows Git helper processes escape timeout ownership (P1)

**Affected code:** `web/process_bounds.py:27-70`, `web/explorer.py:1283+`, `web/selfupdate.py:32-86`.

On Windows, `CREATE_NEW_PROCESS_GROUP` plus `taskkill /T` does not provide durable ownership equivalent to a POSIX process group. In the current environment a stalled transport survives long enough to keep repository handles open. The full test suite and targeted rerun both fail:

- `ApiRoutesTestCase.test_repo_git_timeout_bounds_a_remote_that_goes_quiet` errors while deleting its temporary repository with `WinError 32/5`.
- `ExplorerGitProcessTreeTestCase.test_a_stalled_remote_returns_the_worker_thread_within_the_bound` returns no `subprocess.TimeoutExpired` as promised.

This can leave production Git helpers, pipes, sockets, and repository handles alive after the UI reports a timeout.

**Recommendation:** on Windows, create a Job Object with kill-on-close and assign the child before it can spawn helpers; retain the job handle for the operation. Alternatively use a small supervised helper process with verified descendant ownership. Ensure pipe readers are cancelable and every timeout path joins/reaps within a second deadline. The existing integration tests are good release gates and must be green on Windows.

### GV-010 — SSH agent detection can wait forever for exit status (P1)

**Affected code:** `web/agents.py:663-713`.

`exec_command(timeout=8)` configures channel/socket behavior but is followed immediately by `stdout.channel.recv_exit_status()`, before stdout/stderr are drained. Paramiko's exit-status wait has no deadline and can block if the server never sends a status or if output fills the channel window.

**Recommendation:** drain both streams concurrently with byte ceilings and a monotonic deadline, poll `exit_status_ready()`, and close the channel on timeout. Reuse the same remote-command abstraction as Git rather than maintaining a weaker one.

### GV-011 — remote Git exit status is outside the deadline (P1)

**Affected code:** `web/explorer.py:1680-1755`.

Remote stdout and stderr drains share a deadline, but after both return `_remote_exit_status()` unconditionally calls `recv_exit_status()`. A server that closes output or stops sending data without delivering an exit status can park the request after the advertised deadline.

**Recommendation:** pass the remaining deadline into the exit-status helper, poll `exit_status_ready()`, and close the channel/raise `TimeoutExpired` when no status arrives. Add a fake Paramiko channel test that reaches EOF but never sets the status event.

### GV-012 — SFTP calls have no operation deadline (P1)

**Affected code:** `web/explorer.py:3725-3741` and the SFTP backend/pool below it.

TCP connect has a configurable timeout, but auth/banner timeouts are not explicitly supplied here and SFTP channel operations (`stat`, `listdir`, read/write, rename, remove, search) have no request-level deadline. A silent server can occupy Flask threads indefinitely. A stuck pooled transport can also delay watchers and subsequent explorer actions.

**Recommendation:** set connect/auth/banner deadlines, apply a timeout to each SFTP channel, and wrap operations in a request deadline/cancellation boundary. Evict the pooled transport after timeout or protocol error. Cap concurrent remote operations per session and test a server/channel that stops responding mid-read and mid-write.

### GV-013 — repository mutations are not one transaction (P1)

**Affected code:** Git routes in `web/api.py:1829-1990`; helpers around `web/explorer.py:3460-3650`, especially `_git_commit()` at `3545-3602`.

Multiple panes may stage, unstage, discard, revert, commit, or publish against the same repository concurrently. Git's own index lock prevents some corruption, but it does not preserve GridVibe's higher-level decision. `_git_commit()` checks all staged paths, checks scoped staged paths in a second command, then runs a repository-wide `git commit`; another pane or terminal can stage an out-of-scope path between the check and commit, causing the commit to include work the user was explicitly told would be excluded. Other mutations make decisions from state that can be stale by execution time.

**Recommendation:** serialize GridVibe Git writes by canonical repository identity and attach an optimistic repository revision to requests. Re-read/revalidate under the claim immediately before mutation. Because an external terminal can still run Git, scoped commit should use a pathspec/index-isolation strategy that enforces the selected set rather than relying only on an earlier check. Refresh all panes viewing the repository after each mutation.

### GV-014 — workspace save can accept an empty flush (P1)

**Affected code:** `web/lifecycle.py:832-882`, `LifecycleCoordinator.request_flush()`.

`prepare_workspace_save()` first calls `connected_window_count()` and later calls `request_flush()`. If the last window departs between those calls, the flush can have an empty expected set and report success, allowing capture of stale server state despite the function's stated rule that no reachable window must be refused.

**Recommendation:** have `request_flush()` atomically select and return the expected/connected window count and require it to be nonzero in the same result. Remove the separate precheck. Add a deterministic test that unregisters the last window at the selection boundary.

### GV-015 — native window creation has no opening claim (P1)

**Affected code:** `web/webview_launcher.py:1423-1532`, launcher open-button flow.

`_open_workspace_window()` checks `_workspace_windows`, calls `webview.create_window()`, then attaches/registers the result without a lock or “opening” marker. Two bridge calls can both see no window and create two. The later registration overwrites the dictionary entry, leaving one OS window not represented by the workspace map. The launcher button does not provide a server-side concurrency guarantee even if the UI is normally single-clicked.

**Recommendation:** protect an `opening_by_workspace` map with a lock/future. The first caller creates; later callers await/focus the same result. Disable the UI action while its promise is in flight as a usability guard, not as the correctness mechanism.

### GV-016 — self-update creates live mixed-version code (P1)

**Affected code:** `web/selfupdate.py:94-235`, `perform_app_update()` at `238-249`.

The updater fetches and fast-forwards the checkout while the current Python process continues serving. Python modules remain the old in-memory version, while templates and static assets are read from newly replaced files. That can produce incompatible client/server schemas before the user restarts. Concurrent update requests are not serialized. The dispatcher also explicitly lacks a packaged/frozen update branch.

**Recommendation:** serialize updates, enter a draining/read-only state, stage and validate the update separately, then perform a mandatory coordinated restart. Include a client/server build ID handshake so old tabs reload after restart. For packaged builds, implement signed release download plus rollback or remove/disable the unfinished control.

### GV-017 — custom config path is split-brain (P1)

**Affected code:** import order in `main.py:24`, argument parsing at `129-135`, `web/config.py` global `runtime_config`, and equivalent WebView startup.

`web.api` and its global runtime configuration are imported before `--config` is parsed. The selected file is later loaded to resolve server host/port/debug, but the application's `runtime_config` and App Settings routes continue using the project `CONFIG_PATH`. With the default relative `config.json`, starting from another working directory can also separate startup settings from runtime settings.

**Recommendation:** parse/resolve the canonical config path before importing/building the app, or use an application factory that receives the path exactly once. Make all reads, writes, snapshots, and server settings use that owner. Add subprocess startup tests from another CWD and with a non-default `--config`.

### GV-018 — known-host persistence races (P2)

**Affected code:** `web/hostkeys.py:21-85`.

First creation and Paramiko `save_host_keys()` use ordinary file operations without the cross-process lock/atomic-replace utilities used by the JSON stores. Concurrent first connections can each load an old set and save the whole file, losing the other's key or exposing partial contents. In `auto-add`/`known-hosts`, a load failure logs and then proceeds with `AutoAddPolicy`, degrading to trust-on-every-use for that connection.

**Recommendation:** create a dedicated locked known-host store that merges the new key with the latest disk state and atomically replaces the file. In strict mode fail closed on any trust-store read error; in permissive modes make the degraded behavior an explicit user-visible warning and avoid silently claiming that changed keys remain protected.

### GV-019 — malformed-but-valid config crashes and boolean coercion (P2)

**Affected code:** `web/config.py:127-131`, `_build_runtime_state()` at `302-439`, `web/api.py:481-610`.

Several sections (`ssh`, `terminal`, `appearance`, `workspace`, and `voice_input`) are used with `.get()` without first proving they are mappings. Valid JSON such as `{"ssh": []}` raises `AttributeError` during runtime-state construction; this was reproduced for multiple sections. A valid top-level non-object is silently converted to `{}` rather than quarantined/recovered, making an unsupported file look like an empty override. `voice_enabled` is typed as `Any` and preserves values such as the string `"false"`, which is truthy; the API further uses `bool(value)`, so `"false"` becomes `True`.

**Recommendation:** validate a versioned config schema at the load boundary, normalize every section before access, and require actual booleans. Treat an unsupported top-level/section shape as corrupt for backup recovery purposes. Return field-level `400` errors for API writes instead of silently converting malformed values.

### GV-020 — Windows restart loses CLI arguments (P2)

**Affected code:** `web/webview_launcher.py:1661-1682`.

When the repository launcher and venv Python exist, `_build_restart_command()` returns only `[venv_python, launcher_path]`. It omits `sys.argv[1:]`, unlike the frozen and fallback branches. A restart therefore drops custom `--config`, `--host`, `--port`, `--mode`, and `--debug` choices.

**Recommendation:** preserve normalized original arguments in every branch, excluding only one-shot internal flags if any. Add Windows-specific construction tests with paths containing spaces and every supported option.

### GV-021 — backup/directory durability gap (P2)

**Affected code:** `web/state_files.py:156-164`, `180-214`; all three durable stores.

The primary JSON write is a unique temporary file, fsynced, then atomically replaced—a strong design. However, the last-good backup is refreshed by `shutil.copyfile()` directly onto `<file>.bak`. A crash or full disk can truncate/corrupt the only recovery copy just before the primary replace. Backup failure is deliberately ignored. On POSIX, the parent directory is not fsynced after `os.replace()`, so the rename is not fully crash-durable across sudden power loss.

**Recommendation:** write and fsync a unique backup temp, atomically replace `.bak`, and fsync the directory after both renames where supported. Decide explicitly whether inability to preserve the old recovery point should abort the new commit; at minimum expose it in diagnostics rather than debug-only logging.

### GV-022 — search limits do not cover one regex/file operation (P2)

**Affected code:** `web/explorer_search.py:289-359`.

The fallback engine checks its deadline between files and checks `getsize()` before `read()`, but Python `re.search()` can spend arbitrarily long on a catastrophic user regex against one large line. A file can grow between size check and unbounded `read()`. No regular-file `lstat` check is made, so FIFOs/devices or unusual reparse targets can block. The deadline cannot interrupt any one of those operations.

**Recommendation:** use a regex engine with an enforceable timeout or an isolated bounded worker/subprocess; reject known-dangerous constructs only as a secondary guard. Open only verified regular files without following links where supported, then read at most `max_file_bytes + 1` in deadline-aware chunks. Revalidate confinement on the opened handle/final path.

### GV-023 — explorer checks cannot defeat external check/use races (P2)

**Affected code:** `_explorer_path_claims()` and save flow in `web/explorer.py:739+`, `4277-4341`; mutation policy in `web/explorer_fs.py`.

The process-local claims, revisions, no-overwrite operations, and root checks are strong against concurrent GridVibe requests. They do not protect against another same-user process changing a file or swapping a parent symlink/reparse point between validation and open/replace. In save, content is hashed and compared before `backend.replace_file()`, so an external write in that interval can be lost. String-based realpath confinement has the analogous parent-swap window.

This is partly an OS portability limitation, not a simple logic bug, but the UI currently presents revisions and root confinement as stronger than they are under an actively changing local filesystem.

**Recommendation:** use handle-relative APIs (`openat`/dirfd plus `O_NOFOLLOW` on POSIX; final-path verification on Windows handles) where feasible. Re-stat identity immediately before commit and report the remaining external-writer limitation. For strict CAS saves, use platform locking or a helper that commits only while holding an identity-checked handle.

### GV-024 — other subprocess timeout paths do not own descendants (P2)

**Affected code:** `web/agents.py` subprocess probes around lines `230`, `549`, `581`, `631`, `1043`; voice installation at `web/voice.py:266+`.

Several local/WSL/PowerShell agent probes use `subprocess.run(timeout=...)` without the shared process-tree supervisor. A shell or WSL command may leave descendants holding handles after the direct child times out. Voice dependency installation also uses a 30-minute `subprocess.run(capture_output=True)` without group/tree teardown and can retain unbounded pip output in memory.

**Recommendation:** make one bounded subprocess runner responsible for process ownership, output ceilings, prompt suppression, deadlines, and reaping across Git, agent detection, and installation. Stream install progress to a bounded log/buffer rather than retaining 30 minutes of output.

### GV-025 — logs contain sensitive commands and paths (P2)

**Affected code:** `web/terminal_io.py:1364-1369`, `1458-1492`, plus launch summaries and transition logging in workspace/session modules.

SSH connection logging includes the working directory and `session.initial_command`; local logging emits the fully composed shell command. Startup commands often contain environment assignments, bearer tokens, URLs, or command-line credentials. Paths and host/user identifiers also persist in rotating logs and may appear in exception text.

**Recommendation:** make operational logs shape-only: session/workspace IDs, connection family, duration, status, and bounded error category. Never log raw commands or credential-bearing URLs. Redact exception strings at trust boundaries, and provide an explicit opt-in debug mode with a conspicuous retention warning if command logging is ever necessary.

### GV-026 — App Settings can partially commit (P2)

**Affected code:** `web/static/js/app-settings.js:687-735`.

The UI runs `/api/app-config` and `/api/voice-prefs` concurrently with `Promise.all()` and reports them as one save. They are separate persistence operations. If one succeeds and the other fails, the dialog says “Settings save failed,” may not apply/broadcast the successful server change, and leaves the user unsure which values persisted.

**Recommendation:** combine the values in one server transaction/revision or use `Promise.allSettled()` and explicitly reconcile partial results. Disable closing/re-saving while the transaction is in flight, then reload the authoritative state after any partial failure.

### GV-027 — main-page security headers are absent (P2)

**Affected code:** Flask app response setup; `web/api.py:1664-1665` applies CSP/nosniff only to one inline-image response; `web/static/js/browser-pane.js` iframe policy.

The launcher and workspace pages do not receive a common CSP, `X-Content-Type-Options`, referrer policy, or explicit frame policy. The application currently uses inline/classic scripts, so introducing CSP needs staged nonce/hash work, but the impact of a same-origin injection is unusually high because page JavaScript can drive terminals and currently read saved credentials. Browser panes deliberately combine `allow-same-origin` and `allow-scripts`; same-origin content is therefore trusted application content, not an isolation boundary.

**Recommendation:** add centralized response headers, migrate inline handlers/scripts toward nonce-bearing or external modules, and define `frame-ancestors`/embedding policy intentionally. Serve browser-preview content from a separate origin/process if untrusted same-origin content is ever supported.

### GV-028 — filename bounds miss encoded and suffixed length (P3)

**Affected code:** `web/explorer_fs.py:34`, `_copy_name_for_attempt()` at `166+`, `_upload_name_for_attempt()` at `531+`, and related name validation.

The 255 limit is Python characters, while common filesystems impose component limits in encoded bytes. A multibyte name may pass validation and fail later. Copy/upload collision suffixes are appended after validation and can push a maximum-length base beyond the component limit.

**Recommendation:** centralize a filesystem-aware component validator, reserve suffix space before generating numbered candidates, and surface OS errors as precise `400/409` responses. Test multibyte names and 255-unit collision cases on supported platforms.

### GV-029 — requirement bump rewrites are not atomic (P3)

**Affected code:** `utils/bump_requirements.py`, final write in `main()`.

The utility preserves formatting well and raises floors only, but writes each requirements file directly. Interruption, disk exhaustion, or an encoding/write error can leave a truncated dependency manifest, and multiple files are not staged as a coherent plan.

**Recommendation:** write/fsync a sibling temp, reparse it, and `os.replace()` the original. If multiple files change, finish the complete plan before replacing any file and report exactly which replacements succeeded.

### GV-030 — oversized global modules increase collision risk (P3)

**Affected code:** notably `web/static/js/terminals.js` (~8,500 lines), `explorer-viewer.js` (~7,500), `web/explorer.py` (~4,300), `launcher.js` (~3,900), and `web/api.py` (~3,600).

The browser uses many order-dependent classic scripts that publish functions/state into the same global namespace. This makes name collisions, hidden coupling, and load-order regressions more likely. On the backend, broad modules combine transport, policy, caching, and formatting, increasing the lock and failure-boundary reasoning required for a small change.

**Recommendation:** continue the existing extraction pattern, but move toward ES modules (or one namespaced bundle) with explicit imports. Split backend modules by transaction owner, not merely file length. Add import-cycle and public-export tests so compatibility re-exports can be retired deliberately.

### GV-031 — repeated Git work is the clearest optimization target (P3)

**Affected code:** repository summary/state/graph helpers in `web/explorer.py`; `web/static/js/explorer-git-watch.js`.

Directory/tree activity can repeatedly discover repository roots and run status work. Repository summaries execute state plus graph/history commands, and multiple panes watching the same repository repeat substantially identical calls. Remote panes multiply this into SSH channel round trips. The watcher intentionally sequences panes to avoid bursts, but one slow remote operation can therefore delay all following panes.

**Recommendation:** after GV-009 through GV-013 are fixed, add a short-lived cache keyed by canonical repository identity, requested scope, HEAD/index/worktree revision, and graph pagination. Share the semantic snapshot across panes; invalidate on GridVibe mutations and filesystem watcher signals. Use small bounded concurrency for independent repositories so one remote cannot stall all watchers. Measure command count and latency before choosing cache TTLs.

## 5. Functionality-by-functionality assessment

| Functionality | Assessment |
|---|---|
| Startup and server settings | Normal loopback defaults are sensible; custom config ownership and non-loopback security need redesign (GV-002, GV-017, GV-019). |
| HTTP/Socket.IO boundary | Cross-origin intent is good, but Host self-trust defeats it; socket event schemas and authorization are too loose (GV-001, GV-007). |
| Saved launch presets | Store transactions, encryption-at-rest, locking, quarantine, and response list redaction are strong; secret retrieval is too broad (GV-003). |
| Workspace/session manager | Internal dictionary/list mutations are commonly locked; multi-step user operations need operation-level claims (GV-005, GV-006). |
| Local/WSL/SSH terminals | Startup integration and replay bounds are thoughtful; connection identity/generation and descendant ownership are missing (GV-004, GV-024). |
| Workspace presentation/runtime state | Revisions, normalization, and ordered per-workspace commits are strong; backup crash durability and one lifecycle disconnect edge remain (GV-014, GV-021). |
| Explorer reads/previews | Size caps, Markdown sanitization, revision-aware UI state, and remote abstraction are good; regex/special-file and SFTP deadlines need work (GV-012, GV-022). |
| Explorer mutations/uploads | No-overwrite naming, streaming limits, path claims, and revision checks are strong; external-process TOCTOU and name units remain (GV-023, GV-028). |
| Git sidebar/actions | Command argv construction, output ceilings, prompt suppression, and UI identity models are strong; Windows teardown, remote status, repository transactions, and repeated calls are weak points (GV-009, GV-011, GV-013, GV-031). |
| Browser pane | Tab persistence and navigation controls are structured; the iframe is not a same-origin security boundary and page headers need hardening (GV-027). |
| Voice input | Backend choice and disconnect cleanup exist; ownership/generation, failed starts, lock timeout, and installer supervision need correction (GV-008, GV-024). |
| Native desktop | Fallback behavior and restart helper are substantial; duplicate-open and argument-loss edges remain (GV-015, GV-020). |
| Self-update | Clean-tree and fast-forward checks are appropriate; live checkout replacement, concurrency, child ownership, and packaged updates are incomplete (GV-009, GV-016). |
| Cleanup/dependency utilities | Cleanup is dry-run by default and prunes broad dependency/VCS directories; dependency rewriting needs atomic output (GV-029). |
| Frontend state/UI | There are many good stale-request, revision, cache-bound, and DOM-free model tests; partial settings saves and global/load-order coupling remain (GV-026, GV-030). |

## 6. Positive engineering observations

These are worth preserving during fixes:

- The three JSON stores use in-process and cross-process ordering, unique temporary files, primary atomic replace, explicit persistence errors, backup recovery, and quarantine. GV-021 is a crash-durability refinement, not a rejection of that architecture.
- Runtime configuration is published as immutable snapshots, which avoids mixed-generation multi-field reads after refresh.
- Presentation and runtime-state code validates bounded schemas and uses revisions/tickets rather than blind last-writer-wins updates.
- Explorer upload/download paths are streamed under explicit limits. Mutations use path claims, exclusive creation/no-overwrite behavior, root checks, and meaningful revision conflicts.
- Markdown rendering uses sanitization, vendored assets, and strict Mermaid handling rather than trusting repository HTML.
- Local Git uses argument arrays, disables interactive prompts, bounds stdout/stderr, and separates read/write behavior. Remote Git shell quoting is centralized.
- Terminal output is room-scoped, replay buffers are rolling/bounded, and frontend modules contain many request-sequence, `AbortController`, identity, and revision checks.
- Tests cover a large amount of behavior, including DOM-coupled modules in Node stubs. The two failing process tests are valuable because they expose a production ownership issue rather than merely a formatting mismatch.
- Ruff found no Python lint failures. A quick reference scan found no unambiguous dead production function suitable for deletion.

## 7. Dead code and public-surface review

No code should be removed solely from this pass. The root shims and many `web.api` re-exports look redundant in isolation but serve compatibility/test patch points. JavaScript functions that appear unreferenced within their defining file are frequently called from templates, inline handlers, sibling classic scripts, or Node test harnesses.

The clearest incomplete branch is `perform_app_update()`: its own docstring says a later part will add frozen/package behavior, while current code supports only Git checkouts and source archives. Treat that as an unfinished feature, not dead code.

For a reliable dead-code campaign:

1. Generate a Python import/call graph with a configured tool such as Vulture plus an allowlist for Flask decorators, Socket.IO decorators, template calls, compatibility exports, and test patch points.
2. Instrument classic-script global reads in an end-to-end browser run before converting/removing exports.
3. Publish and deprecate the root shim/re-export surface over at least one release.
4. Prefer behavioral tests over new source-text assertions; source-shape tests make safe extraction appear as regression.

## 8. Missing tests to add

Highest-value additions:

- Host/origin matrix for HTTP and Socket.IO: hostile Host, forwarded headers without trusted proxy mode, HTTP/HTTPS mismatch, IPv6, default ports, explicit origin lists, and wildcard warning behavior.
- Response-shape scan asserting no ordinary HTTP/Socket.IO response contains a saved password.
- Barrier-driven concurrent reconnect/transition tests proving exactly one connection generation survives.
- Old-stream-finalizer test proving generation A cannot close or change the status of generation B.
- Concurrent split test at `max_sessions - 1`.
- Cross-client Socket.IO tests for terminal input, buffer clear, voice audio, and voice stop; malformed payload fuzz cases and per-client caps.
- Voice start failure and stale-audio-generation tests; Vosk lock-timeout test that proves no unlocked socket access.
- Windows Job Object tests for direct Git and `git-remote-*` descendants, including no surviving process/pipe/repository handle.
- Paramiko fake-channel tests for full output windows, missing exit status, stalled SFTP stat/read/write, and pool eviction after timeout.
- Two-pane Git mutation barriers, including an out-of-scope stage inserted between validation and commit.
- Last-window departure exactly between lifecycle selection and flush.
- Simultaneous native open calls for one workspace.
- Startup subprocess tests for custom config paths/CWD and restart argument preservation.
- Config schema cases for every section, top-level list/scalar, wrong booleans, corrupt primary, and usable/unusable backup.
- Power-loss/fault injection around primary temp fsync, backup replace, primary replace, and parent-directory fsync.
- Search cases for catastrophic regex, a single maximum-size line, growing files, FIFO/reparse targets, and cancellation.
- App Settings one-success/one-failure reconciliation.

## 9. Optimization opportunities

Optimization should follow the P0/P1 correctness work; caching a racy or unbounded operation makes it harder to reason about.

1. **Repository snapshot cache:** share status/branch/graph results across panes looking at the same repository and invalidate by semantic revision.
2. **Bounded independent-repository concurrency:** keep one repository serialized but allow a small number of unrelated watcher checks in parallel.
3. **Frontend module loading:** ES modules/bundling would reduce global parsing and collision risk and allow route/page-specific code loading.
4. **Incremental explorer search:** a cancellable worker/index can report progress and avoid rescanning stable files, while retaining strict root and size limits.
5. **Central remote-operation supervisor:** pooling, health checks, deadlines, cancellation, and circuit breaking can be shared by SSH terminal probes, Git, SFTP, and agent detection.
6. **Structured, redacted diagnostics:** correlation IDs and duration/result categories would make slow remote operations and persistence recovery observable without logging commands or paths.

## 10. Natural feature opportunities

The current architecture makes these additions comparatively natural:

- **Authenticated LAN/remote mode:** explicit token/session authentication, TLS/reverse-proxy configuration, and a UI indicator separate from the safe loopback default.
- **OS credential-vault integration:** presets retain credential references, while reusable SSH passwords never enter general JSON/API payloads.
- **Operations/diagnostics page:** effective config path, bind/security posture, build ID, active connection generations, SSH pool health, running bounded jobs, last state recovery, and update/restart status.
- **Cancelable job surface:** Git publish/fetch, remote search, SFTP transfer, voice installation, and self-update can expose progress and cancellation through one bounded job model.
- **Crash-safe staged updater:** download/fetch, validate, switch atomically, restart, version-handshake, and rollback; signed package updates for frozen builds.
- **Repository activity/transaction UI:** show when another pane owns a Git mutation, what repository revision the action targets, and when external changes invalidate the view.
- **Safe preset import/export:** omit credentials by default and optionally bind imported entries to vault-backed secrets.
- **Search progress/indexing:** opt-in repository indexing with safe literal/regex modes, cancellation, and explicit scope/resource budgets.

## 11. Proposed remediation milestones

### Milestone A — security boundary

Fix GV-001 through GV-003 and GV-007. Add centralized response headers from GV-027. Refuse unauthenticated non-loopback startup until authenticated mode exists. This milestone should be independently releasable.

### Milestone B — ownership and concurrency

Implement per-pane connection generations (GV-004/GV-005), atomic capacity claims (GV-006), voice generations (GV-008), repository mutation claims (GV-013), atomic lifecycle selection (GV-014), and native opening claims (GV-015).

### Milestone C — bounded external work

Replace Windows process handling (GV-009), unify subprocess supervision (GV-024), and put one deadline/cancellation model around SSH detection, remote Git status, and SFTP (GV-010 through GV-012).

### Milestone D — durability and configuration

Unify config ownership/schema (GV-017/GV-019), preserve restart arguments (GV-020), atomically maintain backups and directory sync (GV-021), then address external filesystem/search limits (GV-022/GV-023/GV-028/GV-029).

### Milestone E — maintainability and performance

Split global modules, resolve settings partial commits, add the repository snapshot cache, and introduce structured diagnostics (GV-026/GV-030/GV-031). Re-run the complete Python and Node suite on Windows and at least one POSIX platform before release.

## 12. Release gate from this review

At minimum, do not call the revision clean until:

- the full test runner is green on Windows, including both real stalled-remote tests;
- hostile Host/origin pairs are rejected for HTTP and Socket.IO;
- ordinary configuration/preset GETs are proven secret-free;
- concurrent reconnects cannot create two live connectors or let an old stream close a new one;
- remote Git/SFTP/agent detection always returns within its stated deadline; and
- custom config selection is consistent across startup, runtime reads, and writes.

