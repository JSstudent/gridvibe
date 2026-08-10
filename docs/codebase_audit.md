# GridVibe Codebase Audit — 2026-08-10

Branch `szua_gridvibe-wrk-feat` @ `e03c03b`. Working tree clean.

A general review after the session/workspace and voice work: conformance to the
Regression Guardrails in `CLAUDE.md`, defects introduced or left behind, race
conditions, and worthwhile optimizations. Every defect below was reproduced
locally; the reproduction is shown with the finding.

This started as a report rather than a patch. Findings are being worked off in
the order set out in §6; each one that lands keeps its original text and gains a
**Resolution** block, so the reasoning that justified the fix stays next to it.
The status column in §2 is the index. Everything outside a Resolution block
describes the code as it stood at `e03c03b`.

---

## 1. Baseline

| Check | Result |
| --- | --- |
| `python -m ruff check .` | **All checks passed** |
| `python tests/run_tests.py` | 1402 tests, 46.7 s — **2 failures**, 7 skipped |
| Test/source ratio | ~34k lines of tests against ~64k of source |
| Socket.IO events | 9 emitted, 9 with client listeners — **no dead events** |
| Config keys | every key in `default_config.json` is read through `RuntimeConfig` |
| `window.prompt/confirm/alert` | **none** anywhere, including browser-only paths |
| CDN-loaded frontend assets | **none** — all vendored |
| Bare `except:` / `TODO` / `FIXME` | **none** in shipped source |
| Atomic-write temp paths | all unique (`uuid4` / `mkstemp`) — guardrail 2 satisfied |

The two test failures are environmental, not product defects — see F6.

Overall the codebase is in good shape. The guardrails are not decorative: the
lock discipline, the ordered presentation transactions, the durable-state
primitives, and the read-only explorer contract all hold up under inspection.
The findings below are concentrated in two places — the process entry points
(which the recent work did not touch) and the newest feature (voice).

---

## 2. Findings

| # | Severity | Area | Summary | Status |
| --- | --- | --- | --- | --- |
| F1 | **High** | Entry points / security | `--host`/`--port` silently break realtime transport | **Fixed 2026-08-10** |
| F2 | **Medium** | Voice | Per-session voice state leaks on disconnect; PCM buffer unbounded | Open |
| F3 | **Medium** | Performance | `_cache_terminal_output` is O(n) per chunk under the global lock | Open |
| F4 | **Medium** | Local config | `config.json` sets `cors_origins: ["*"]`, disabling both origin defences | Open (local machine) |
| F5 | Low | Correctness | `_connect_session` has no browser-pane branch (guardrail 6 corollary) | Open |
| F6 | Low | Tests | Two voice tests depend on an unpatched module-level import | Open |
| F7 | Low | Docs | `CLAUDE.md` line 116 describes a test contract that no longer exists | Open |
| F8 | Low | Explorer | Pooled SSH client can be reaped underneath an in-flight request | Open |
| F9 | Info | Docs | `GET /api/explorer/<id>/image` is missing from the read-only contract text | Open |

---

### F1 — `--host`/`--port` silently break all realtime transport · **High** · *Fixed 2026-08-10*

`web/app.py:71` builds the Socket.IO server **at import time**:

```python
socketio = SocketIO(app, cors_allowed_origins=_resolve_cors_origins(), ...)
```

`_resolve_cors_origins()` (`web/app.py:38`) derives the allowed origins from
`runtime_config.app_config["server"]["port"]` — i.e. from `config.json` /
`default_config.json`, **never** from the command line. But `main.py:126` and
`web/webview_launcher.py:1440` both resolve the *bind* address through
`resolve_server_settings(...)`, where an explicit CLI flag correctly wins.

The two resolutions are disconnected, so the server binds to one port and
authorises origins for another. This directly contradicts guardrail 4
("explicit CLI flags must beat `config.json`") — the flag wins for binding and
loses for everything the binding implies.

**Reproduced** (shipped defaults, no local `config.json`):

```
allowed origins: ['http://127.0.0.1:5050', 'http://localhost:5050']

1. handshake GET  (no Origin)                 -> 200  sid=BHwpz0G8PghDrvnTAAAA
2. data POST      (Origin :8080)              -> 400
3. data POST      (Origin :5050, the config)  -> 200
```

The failure mode is worth stating precisely, because it is *not* a clean
"connection refused":

- The polling **handshake succeeds** — browsers omit `Origin` on a same-origin
  GET, and `python-engineio` allows a request with no `Origin` header.
- The **WebSocket upgrade is rejected**. Browsers always send `Origin` on a
  WebSocket handshake (RFC 6455), so the upgrade returns 400 and the transport
  is stuck on long-polling.
- **Data POSTs are rejected** in Chromium and Safari, which send `Origin` on
  same-origin POSTs. Terminal input travels on those POSTs.

So the user sees a page that loads, panes that appear to connect, and then a
terminal that accepts no input and streams no output — with only
`... is not an accepted origin` in the server log to explain it. `--host` with
a non-loopback address fails the same way for anyone browsing by IP.

**Fix.** Make the origin derivation a function of the *resolved* server
settings rather than of the config file. The cleanest shape given the
import-time construction is to let `run_server(host, port, ...)` update the
allowed origins before `socketio.run(...)`, or to construct the Socket.IO
server lazily from resolved settings. A defensive addition either way: when
`security.cors_origins` is unset, include the request host — the write guard
already does exactly this in `_allowed_write_origin_netlocs()`
(`web/app.py:84`), which is why HTTP writes keep working on a custom port
while Socket.IO does not.

**Workaround** for users today: set `security.cors_origins` explicitly in
`config.json` to match the port actually in use. *(No longer needed — see
below.)*

#### Resolution — 2026-08-10

Both halves of the suggested fix were taken.

**1. Origins now follow the resolved bind settings.** `_resolve_cors_origins`
(`web/app.py`) takes optional `host`/`port` that beat the config values, and
`apply_resolved_server_origins(host, port)` re-points the live
`socketio.server.eio.cors_allowed_origins` at them. `run_server(host, port, …)`
(`web/api.py`) calls it before `socketio.run(...)`, so both entry points —
`main.py` and `web/webview_launcher.py`, which already shared `run_server` —
get it without either of them learning about origins. The import-time
construction is unchanged and still config-derived; nothing else in the module
had to move.

**2. The request host is trusted when `security.cors_origins` is unset.** A
static list cannot answer a wildcard bind: `--host 0.0.0.0 --port 8080` browsed
by LAN IP has no origin the server can enumerate, so the derived list alone
would still have failed that case. When nothing is configured, engine.io is
handed a `SameOriginPolicy` callable instead of a list — it allows the derived
origins plus the origin the request was actually addressed to (honouring
`X-Forwarded-Proto`/`-Host`). That is deliberately the same rule
`_allowed_write_origin_netlocs` already applies to HTTP writes, which is why
writes kept working on a custom port while Socket.IO did not; the two defences
now agree by construction. An explicit `security.cors_origins` list is still
honoured verbatim — including `["*"]`, and including *not* folding in the
request host — so a reverse-proxy configuration means exactly what it says.

`run_server` logs the effective allowlist once at startup, which is the single
line that would have made this finding obvious on first run.

**Verified** against the real server (not the test client) on `--port 8080`
with a shipped-default config:

```
handshake Origin :8080 (the bind port)   -> 200
data POST Origin :8080                   -> 200
websocket Origin :8080                   -> HTTP/1.1 101 Switching Protocols
handshake Origin :5050 (the config port) -> 400
handshake Origin http://evil.example     -> 400
```

Covered by `ResolvedServerOriginsTestCase` in `tests/test_api.py`, which drives
real handshakes through the WSGI stack: the flag port is authorised, a wildcard
bind is authorised by request host, a hostile origin and `null` are still
rejected, an explicit list is applied verbatim, and an explicit `*` still allows
everything. The four transport tests fail with the `run_server` call stubbed
out, so they hold the regression rather than merely describing it. Suite:
1410 tests, same 2 environmental failures as the baseline (F6); ruff clean.

---

### F2 — Voice session state leaks on disconnect; PCM buffer is unbounded · **Medium**

Two distinct problems in the same path.

**(a) Nothing cleans up voice state when a socket drops.** The `disconnect`
handler (`web/api.py:2965`) clears joined session buffers and lifecycle window
records, but touches none of the voice registries:

```python
@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")
    _clear_client_joined_sessions(request.sid)
    lifecycle_coordinator.disconnect_client(request.sid)
```

`_whisper_audio_buffers`, `_vosk_ws_connections`, `_vosk_session_locks` and
`_active_voice_sessions` (`web/voice.py:104-117`) are only ever cleared by
`voice_stop` or by `_stop_vosk_service()`, which is registered with `atexit`
— i.e. at process exit. A recording interrupted by a tab close, a crash, a
network drop or a suspended laptop leaves its entry behind for the life of the
process, including a live Vosk WebSocket that is never closed.

**(b) The whisper buffer has no ceiling.** `_handle_whisper_audio_chunk`
(`web/voice.py:730`) is an unbounded `extend`:

```python
buffer.extend(raw)
```

Audio is raw PCM int16 at 16 kHz mono = 32 KB/s ≈ **1.9 MB/minute**, so a
push-to-talk key held (or wedged) for an hour buffers ~115 MB before a single
`_transcribe_whisper_audio` call is asked to swallow all of it at once. Combined
with (a), an abandoned recording holds that memory permanently.

**Fix.** Clear the four registries for the disconnecting client in
`handle_disconnect` — this needs a sid→session mapping, which
`_active_voice_sessions` can carry alongside the engine. Separately, cap the
whisper buffer at a defensible maximum recording length and emit a
`voice_status` error when it is hit, rather than growing without limit.

---

### F3 — `_cache_terminal_output` is O(n) per chunk under the global lock · **Medium**

`web/terminal_io.py:136`:

```python
with connection_lock:
    ...
    buffer.append(output)
    total = sum(len(chunk) for chunk in buffer)   # full scan, every chunk
```

The deque is bounded by *total characters* (50 000), not by chunk count, so the
number of chunks scales inversely with chunk size — and the whole scan happens
while holding `connection_lock`, the **process-wide** lock every terminal's
output pump shares.

**Measured** (steady state, this machine):

| Chunk size | Chunks held | Cost per chunk |
| --- | --- | --- |
| 1 char (keystroke echo, the `read(1)` fallback path) | 50 000 | **2452.7 µs** |
| 64 chars (interactive line) | 782 | **40.0 µs** |
| 4096 chars (bulk read) | 13 | 1.9 µs |

The bulk-read path is fine. The interactive path costs 40 µs of the *global*
lock per chunk, and the 1-char path — reachable through the
`stdout_handle.read(1)` fallback at `web/terminal_io.py:628` — costs 2.5 ms per
character, serialising every other pane behind it.

This is guardrail 3's "per-chunk full-buffer copies" in spirit: not a copy, but
a full-buffer walk on the hot path.

**Fix.** Keep the running total next to the deque (a small
`{"chunks": deque, "total": int}` record, or a parallel dict) and adjust it
incrementally on append and eviction. The trimming loop already computes
exactly the deltas needed; only the initial `sum()` has to go.

---

### F4 — Local `config.json` disables both origin defences · **Medium**

Not a repository defect — `config.json` is gitignored and the shipped
`default_config.json` is correct (`"cors_origins": []` → derive same-origin).
But the config on this machine reads:

```json
"security": { "cors_origins": ["*"] }
```

That single value turns off **both** layers:

- `_resolve_cors_origins()` returns `["*"]`, so Socket.IO accepts a connection
  from any origin;
- `_allowed_write_origin_netlocs()` (`web/app.py:74`) returns `None` on seeing
  `*`, which `_reject_cross_origin_writes` treats as "allow all" — the
  cross-origin write guard is bypassed entirely.

GridVibe binds to `127.0.0.1` and has no authentication by design, and its
Socket.IO handlers identify work by `session_id` from the payload rather than by
room membership (`handle_terminal_input`, `web/api.py:3100`; the `voice_*`
handlers, `web/api.py:3262-3332`). The same-origin default is therefore the
*only* thing standing between a hostile page the user happens to visit and
their live shells. With `["*"]` set, any web page can open a Socket.IO
connection to `localhost:5050` and write to terminals.

**Recommendation.** Remove the override locally unless a reverse proxy needs
it, and in that case name the proxy origin explicitly rather than using `*`.
Worth considering upstream: refuse `*` when the bind host is loopback, or log a
startup warning — the value is easy to add while debugging and easy to forget.

---

### F5 — `_connect_session` has no browser-pane branch · Low

`web/terminal_io.py:1112`:

```python
if _is_explorer_session(session):
    session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
    _broadcast_session_status(session_id)
    return

if session.mode == "wsl":
    _connect_local_session(session_id, session)
    return
```

Explorer panes are guarded; browser panes are not. And a browser pane is
*always* `mode == "wsl"` — `_normalize_startup_mode` only admits `browser` for
the `wsl` connection mode (`web/saved_sessions.py:141`) — so it falls straight
into the local-shell connector. Its `initial_command` is the active tab's URL
(`sessions/manager.py:84-88`), which `_run_startup_sequence` would then type at
the prompt.

This is precisely the corollary guardrail 6 warns about: *"a missing branch
silently degrades the pane to a plain shell that then types its
`initial_command` at the prompt."*

**It is latent, not live.** Every current caller guards externally — the launch
and restore path (`web/workspaces.py:752`), split (`web/api.py:2491`), and
mode-switch (`web/api.py:2669`) all check `_is_browser_session` before
dispatching. The one unguarded caller is `reconnect_session`
(`web/api.py:2558`), which is unreachable today because it requires status
`ERROR`/`DISCONNECTED` and browser panes are set `CONNECTED` and never stream.

**Fix.** One line — extend the existing early return to
`if _is_explorer_session(session) or _is_browser_session(session):`. The
function already imports `_is_explorer_session` from the same module that
exports `_is_browser_session`. Cheap insurance against the next caller.

---

### F6 — Two voice tests depend on an unpatched module-level import · Low

```
FAIL: ApiRoutes: voice status endpoint reports per engine availability
AssertionError: 'vosk and websockets' not found in 'Cannot start Vosk because
websocket-client is not installed. ...'

FAIL: ApiRoutes: voice status endpoint trusts a running external vosk service
AssertionError: False is not true
```

Both tests (`tests/test_api.py:3692` and `:3710`) patch
`_vosk_service_reachable` and `_vosk_service_packages_available` but **not**
`web_voice.ws_client`. Both code paths check `ws_client is not None` first
(`web/voice.py:77` and `:143`), so in any environment where `websocket-client`
is not importable the tests exercise the wrong branch and fail on an assertion
that has nothing to do with what they are testing.

`websocket-client>=1.9.0` *is* a core dependency in `requirements.txt`, and
`requirements-dev.txt` includes it, so a properly provisioned environment
passes. This `.venv` simply lacks it. **Confirmed** by stubbing the module:

```
test_voice_status_endpoint_reports_per_engine_availability ... ok
test_voice_status_endpoint_trusts_a_running_external_vosk_service ... ok
```

Two separate points, both worth acting on:

1. The tests should patch what they depend on, as their siblings already do
   (`test_voice_status_endpoint_reports_missing_whisper_dependency` patches
   `WhisperModel` and `np`). Adding `patch.object(web_voice, "ws_client", object())`
   makes them test their stated behaviour in any environment.
2. This `.venv` is stale against `requirements.txt` — `make dev-deps` (or
   `pip install -r requirements-dev.txt`) will fix the run.

---

### F7 — `CLAUDE.md` describes a test contract that no longer exists · Low

`CLAUDE.md:116` documents `tests/test_session_persistence_contract.py` as:

> `# (expectedFailure until each audit stage lands)`

The file itself now says the opposite, in its module docstring:

> *"Stages 1-6 have shipped, so no `expectedFailure` remains: every forcing
> function in this module now asserts behaviour the production code delivers.
> A new decorator here means a new deferred contract, not a known defect."*

There are **zero** `@unittest.expectedFailure` decorators left in the file. A
reader trusting `CLAUDE.md` would take failures there as expected rather than
as regressions — which is exactly the inversion the Working Rules warn about
("a stale contract is worse than a missing one"). One-line fix.

---

### F8 — Pooled SSH client can be reaped underneath an in-flight request · Low

`_acquire_ssh_sftp` (`web/explorer.py:2834`) calls
`_reap_idle_pooled_ssh_clients()` on every acquisition, which closes any client
whose `last_used` is older than `SSH_CLIENT_POOL_IDLE_TIMEOUT` (60 s). But
`last_used` is stamped at *acquire* and *release* time, not continuously — so a
request that runs longer than 60 s (a large `/download`, a slow repository
`/search`) is holding a client the reaper considers idle. A concurrent request
on the same session triggers the reap and closes the transport out from under
it.

Narrow: it needs a same-session concurrent request during a >60 s operation,
and the explorer's own polls keep refreshing `last_used` in practice. Worth
noting rather than urgent. A simple in-use refcount, or stamping `last_used` on
a keepalive during long transfers, closes it.

Adjacent observation: pooled explorer clients never get
`transport.set_keepalive(...)`, unlike terminal sessions
(`web/terminal_io.py:960`). With a 60 s idle reap that rarely matters, but it
means a pooled client behind an aggressive NAT can go dead before the reaper
notices, and the first request to reuse it pays a failed `open_sftp()` and a
reconnect.

---

### F9 — `/image` missing from the documented read-only contract · Info

The read-only explorer contract in `CLAUDE.md` enumerates the reads that stay
in scope (`/download`, `/git/state`, `/file/state`, `/entries`, `/search`) and
the six mutation families. `GET /api/explorer/<session_id>/image`
(`web/api.py:1373`) is not mentioned.

The route itself is exemplary — root-confined through `backend.resolve_file`,
25 MB capped on both `stat` and the actual read, `Content-Security-Policy:
default-src 'none'; ... sandbox` and `X-Content-Type-Options: nosniff` for the
direct-navigation case. It is unambiguously a read and belongs in the read
list. Only the documentation needs the addition.

---

## 3. Guardrail conformance

Verified clean, with the evidence:

| # | Guardrail | Status |
| --- | --- | --- |
| 1 | Security | Same-origin defaults intact **in shipped config**; room-scoped emits confirmed; host-key policy and Fernet handling unweakened. See **F4** for the local override. |
| 2 | Concurrency | **Clean.** All 12 `socketio.emit` call sites are outside both locks — `_broadcast_session_status` snapshots under `session_manager.lock` and emits after; the stream pumps emit outside `connection_lock`; `LifecycleCoordinator.request_flush` decides its emit set inside one hold and emits outside. Documented lock ordering respected. Every atomic write uses a unique same-directory temp path. |
| 3 | Performance | SSH pooling correct (no per-request handshakes); push over Socket.IO with a 15 s fallback poll gated on `socket.connected`; no CDN assets. **F3** is the exception. |
| 4 | Correctness | `_powershell_single_quote` / `shlex.quote` used per target shell; **zero** `window.prompt/confirm/alert`. CLI-flag precedence is where **F1** lives. |
| 5 | Dead code | **Clean.** 9 Socket.IO events, all with listeners in both directions. Every `default_config.json` key reaches `RuntimeConfig`; `ssh.keepalive_interval` — the most plausible orphan — is read at `web/terminal_io.py:960`. |
| 6 | Architecture/DRY | Backend split is holding (`web/` sub-modules, no `api.py` regrowth in the recent work). New frontend surfaces went to their own files (`notice-banner.js`, `voice-dictation.js`). **F5** is the pane-kind corollary; §4.1 covers the frontend regrowth. |
| 7 | Styling | New CSS is fully tokenised — `app-settings.css`, `lifecycle.css`, `notice-banner.css`, `workspaces.css` all have **zero** colour literals. Legacy debt quantified in §4.2. |
| 8 | Interaction | Irreversible actions go through `openGenericConfirmModal`; failures carry `retryable` and a retry affordance; busy states toggle classes. |
| 9 | Logging | **Clean.** ANSI stripped on the file handler only (`main.py:53`); poll GETs suppressed via `_POLL_RE`; lifecycle and runtime-state logging is shape-only (ids, revisions, counts, categories) with paths and payloads deliberately excluded. |
| 10 | New features | Voice config flows through `RuntimeConfig` + `/api/app-config`; no secrets in new state files. **F2** is a resource-lifecycle gap, not a contract breach. |

Two things stood out as genuinely well built and worth not regressing:

- **The lifecycle flush coordinator** (`web/lifecycle.py`). The
  stale-vs-departed window distinction, pre-acknowledging a window that drops
  mid-flush so it resolves immediately with the accurate category instead of
  timing out, and forgetting a deliberate leave rather than reporting it — these
  are the details that usually get missed. The emit set being decided in the
  same lock hold that builds `expected` (`web/lifecycle.py:406`) is exactly
  right, and the comment explains why.
- **The durable-state layer** (`web/state_files.py` + `web/runtime_state.py`).
  In-process tickets ordering threads, durable revisions ordering processes,
  quarantine-then-recover-from-backup instead of laundering a bad file into
  empty state, and `create_file_exclusively` claiming with a syscall that fails
  rather than clobbers.

---

## 4. Optimizations and improvements

### 4.1 `explorer-viewer.js` regrowth is no longer a risk — it has happened

`CLAUDE.md` flags this file as "the current regrowth risk". It is now the
largest file in the project:

- **8119 lines**, 315 function declarations
- **zero** section markers or banner comments to navigate by
- clearly separable domains by reference density: Diff (~300), tab strip
  (~319), Git sidebar (~180), Preview (~168), Markdown (~117)

For comparison, the split that produced `terminal-icons.js`, `explorer-fs.js`,
`browser-pane.js` et al. was triggered by a 13.5k-line `terminals.js` — which
now sits at 8089 lines, i.e. the two files have converged on the same size the
split was meant to prevent.

Suggested split, in the order that yields the most separation per unit of risk:

1. `explorer-diff.js` — Diff rendering, patch identity, staged/worktree modes.
   The most self-contained domain and the one with its own persistence identity
   rules already.
2. `explorer-git-sidebar.js` — staging, commit, publish, discard UI. It already
   has a natural seam at the `/git/*` endpoint boundary.
3. `explorer-tabs.js` — the tab strip and its ordering/activation rules.

Markdown/Preview can stay behind; it is the smallest of the four and is
entangled with the mermaid vendoring.

### 4.2 Legacy CSS token debt, quantified

Guardrail 7 says to migrate literals when touching a legacy block. Current
state:

| File | Hex literals | `rgb()`/`rgba()` | Total |
| --- | --- | --- | --- |
| `terminals.css` | 330 | 87 | **417** |
| `launcher.css` | 41 | 127 | **168** |
| `tokens.css` | 30 | 22 | 52 *(definitions — correct)* |
| `app-settings.css`, `lifecycle.css`, `notice-banner.css`, `workspaces.css` | 0 | 0 | **0** |

The incremental rule is working — everything new is at zero — but 585 literals
remain in the two legacy files, which is slow going at "when you touch it"
pace. A single focused pass over `terminals.css` would retire most of it, and
it is mechanical, low-risk work with visual regression as the only real check.

### 4.3 Test file sizes

`tests/test_api.py` is **17 111 lines** and `tests/test_multi_workspace.py` is
**6 055**. The same DRY argument that split `web/api.py` applies: `test_api.py`
now covers explorer FS, git, voice, sessions, workspaces, and lifecycle in one
file. Splitting along the module boundaries that already exist
(`test_api_explorer_git.py`, `test_api_voice.py`, …) would make failures easier
to place and the suite easier to run selectively.

### 4.4 Smaller items

- **Bound `normalize_group_presentation`'s `pane_order`** against
  `MAX_STORED_SESSION_PANES`. The manager's membership check makes an oversized
  payload harmless today, but the bound belongs with the other schema ceilings,
  and rejecting early is cheaper than building the pane map first.
- **Explorer pool keepalive** — see F8's closing note.
- **`_evict_excess_auto_slots` records no tombstone** for evicted workspaces
  (`web/runtime_state.py:1164`). Benign today, since a later capture simply
  recreates the slot, but it is the one mutation of `workspaces` that does not
  go through the revision bookkeeping the rest of the module is careful about.

---

## 5. Feature suggestions

Offered as candidates, weighted toward things that build on contracts that
already exist rather than opening new ones.

**Fits cleanly within existing contracts:**

- **Startup self-check / diagnostics endpoint.** F1 is invisible to the user
  precisely because nothing reconciles "what did we bind to" with "what did we
  authorise". A `GET /api/diagnostics` returning resolved host/port, effective
  CORS origins, host-key policy, voice engine availability, and pool state
  would have made F1 obvious on first run — and would help with the voice
  dependency confusion behind F6.
- **Terminal output search / scrollback find.** The replay buffer already
  exists per session (`session_output_buffers`); a client-side find over the
  live xterm buffer needs no new backend surface at all.
- **Explorer: open recent files across panes.** The presentation layer already
  persists ordered tabs per pane; a workspace-scoped recents list is the same
  data at a different scope, and would go through the existing ordered
  workspace-presentation transaction.
- **Per-workspace SSH agent/key selection.** Currently `look_for_keys` and
  `allow_agent` are derived from whether a password was supplied
  (`web/explorer.py:2768`). An explicit key path per saved preset is a natural
  extension of the existing preset schema.

**Deliberately flagged as contract changes, not free additions:**

- **Explorer search-and-replace.** The obvious next step after bounded
  repository search, but it would add a seventh mutation family and a
  multi-file write path. The current write contract is single-file, full
  content, revision-checked — replace across N files does not fit that shape
  without a new transaction design. Worth doing only with that design done
  first.
- **Git checkout / pull / merge.** Explicitly out of scope in `CLAUDE.md`
  today, and for good reason: all three can destroy uncommitted work in ways
  the current claim/revision machinery does not model. If it is ever wanted,
  `pull --ff-only` is the one variant with a bounded failure mode.

---

## 6. Recommended order of work

1. ~~**F1** — a documented flag that silently breaks the app is the only
   user-facing breakage here.~~ **Done 2026-08-10.**
2. **F4** — one line of local config; removes a real drive-by risk today.
3. **F2** — resource leak in the newest feature, cheapest to fix while it is
   still fresh.
4. **F6, F7** — restore a green suite and an accurate `CLAUDE.md`; both are
   minutes of work and both currently mislead.
5. **F3** — measurable, contained, and the fix is a running total.
6. **F5, F8, F9** — latent/narrow/documentation; batch them.
7. **§4.1** — schedule the `explorer-viewer.js` split before it grows further.
