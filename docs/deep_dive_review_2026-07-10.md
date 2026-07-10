# GridVibe Deep-Dive Code Review — 2026-07-10

Full-codebase analysis pass covering `main.py`, `web/api.py`, `web/webview_launcher.py`,
`sessions/manager.py`, `services/vosk_service.py`, `utils/cleanup.py`, the root compatibility
shims, both templates (`templates/index.html`, `templates/terminals.html`),
`web/static/voice-capture-worklet.js`, build/config files, and `logs/gridvibe.log`.

Each finding is a standalone section with a proposed implementation. Severity is a rough
prioritisation aid: **High** = fix soon (correctness/security), **Medium** = worth scheduling,
**Low** = cleanup/polish.

**Scope note:** this is an analysis document only — no code was changed.

---

## Index

| # | Finding | Category | Severity |
|---|---------|----------|----------|
| 1.1 | Socket.IO CORS defaults to `*` on a terminal-input socket | Security | High |
| 1.2 | No Origin/CSRF guard on state-changing HTTP endpoints | Security | High |
| 1.3 | `_decrypt_password` falls back to returning ciphertext | Security | Medium |
| 1.4 | Host key `AutoAddPolicy` everywhere (no opt-in strictness) | Security | Low |
| 2.1 | `create_group` display-order race (lock released between read and write) | Race condition | Medium |
| 2.2 | Empty-group deletion race in `clear_disconnected_sessions` | Race condition | Medium |
| 2.3 | Connect-vs-close TOCTOU leaks live SSH/PTY connections | Race condition | Medium |
| 2.4 | `socketio.emit` while holding `connection_lock` | Race condition / Perf | Medium |
| 2.5 | Agent detection subprocess runs under a global cache lock | Race condition / Perf | Medium |
| 2.6 | Voice engine can change between `voice_start` and `voice_stop` | Race condition | Low |
| 2.7 | Whisper model instance never reloaded after settings change | Race condition / Correctness | Medium |
| 2.8 | 8-char session IDs stored without collision check | Race condition | Low |
| 2.9 | `active_launch_options` mutated without a lock | Race condition | Low |
| 3.1 | New SSH+SFTP connection per explorer request | Performance | High |
| 3.2 | Terminal output buffer: full-string copy per chunk | Performance | Medium |
| 3.3 | SSH stream loop busy-waits with 50 ms polling | Performance | Low |
| 3.4 | 3-second HTTP polling duplicates Socket.IO push | Performance | Medium |
| 3.5 | 18k lines of inline CSS/JS in templates (no caching, reparsed each load) | Performance / Maintainability | Medium |
| 3.6 | Frontend depends on CDN for xterm/socket.io | Performance / Reliability | High |
| 3.7 | Byte-at-a-time fallback read in `_stream_local_output` | Performance | Low |
| 4.1 | PowerShell `cd` uses POSIX quoting (`shlex.quote`) | Correctness | Medium |
| 4.2 | xterm theme uses removed `selection` key | Correctness | Low |
| 4.3 | Launch button label changes identity after first launch | Correctness / UI | Low |
| 4.4 | `window.prompt` in Save Session silently no-ops under WebView2 | Correctness | High |
| 4.5 | `info`/`warning` message styles referenced but never defined | Correctness / Style | Low |
| 4.6 | Dead reference to `wsl_default_dir_display` | Correctness / Dead code | Low |
| 4.7 | `config.json` silently overrides explicit CLI flags | Correctness | Medium |
| 4.8 | Vosk port configured in two unrelated keys | Correctness / Config | Medium |
| 4.9 | `cleanup.py` deletes live logs, has stale branding, dir/file pattern confusion | Correctness | Low |
| 4.10 | Misindented return block in `get_explorer_entries` | Style | Low |
| 5.1 | Unused config keys (`keepalive_interval`, `default_username`, `terminal.*`) | Dead code / Feature gap | Medium |
| 5.2 | `/api/sessions/active` endpoint has no callers | Dead code | Low |
| 5.3 | `SessionManager` callback registry is dead code | Dead code | Low |
| 5.4 | `DESTRUCTIVE_PATTERNS = []` and other cleanup leftovers | Dead code | Low |
| 5.5 | `set_native_theme(theme)` ignores its argument; single-entry theme dict | Dead code | Low |
| 5.6 | `⋮` Options button on launcher terminal cards does nothing | Dead code / UI | Low |
| 5.7 | Three near-identical server-run entry points | Dead code / DRY | Low |
| 6.1 | Local vs remote explorer/git code is duplicated (~15 function pairs + route bodies) | DRY / Architecture | High |
| 6.2 | `web/api.py` is a 6.8k-line monolith | Architecture | Medium |
| 6.3 | `create_session`/`append_session_to_group` duplicate 17-parameter signatures | DRY | Low |
| 6.4 | Shared JS duplicated between the two templates | DRY | Medium |
| 6.5 | Oversized functions: `buildGrid` (~445 lines), `_startVoice` (~308 lines) | Maintainability | Medium |
| 6.6 | Two similarly named git runners caused a real production TypeError | DRY / Logs | Medium |
| 7.1 | Two divergent design-token systems (launcher vs terminals) | Style mismatch | Medium |
| 7.2 | Mixed icon language: emoji vs inline SVG | Style mismatch | Low |
| 7.3 | Hardcoded colors that ignore the light theme | Style mismatch | Low |
| 8.1 | No confirmation when closing a session tab | Button/UX | Medium |
| 8.2 | Launch button has no spinner and loses its arrow | Button/UX | Low |
| 8.3 | Duplicate update-status areas show identical text | Button/UX | Low |
| 8.4 | No "reconnect" affordance on errored/disconnected panes | Button/UX / Feature | Medium |
| 8.5 | Save-settings tooltip doubles up (title + custom bubble) | Button/UX | Low |
| 9.1 | Log noise: normal pane closures logged as ERROR | Logs | Medium |
| 9.2 | ANSI color codes written into gridvibe.log | Logs | Low |
| 9.3 | `/api/voice-status` polling not covered by the log filter | Logs | Low |
| 10.1 | Feature: SSH keepalive (config key already exists) | New feature | — |
| 10.2 | Feature: wire up `terminal.font_size` / `font_family` config | New feature | — |
| 10.3 | Feature: terminal search + clickable links (xterm addons) | New feature | — |
| 10.4 | Feature: broadcast input to all panes | New feature | — |
| 10.5 | Feature: session reconnect / restore after backend restart | New feature | — |
| 10.6 | Feature: explorer file download (read-only compatible) | New feature | — |
| 10.7 | Feature: strict host-key verification mode | New feature | — |

---

## 1. Security

### 1.1 Socket.IO CORS defaults to `*` on a socket that accepts terminal input — **High**

**Location:** `web/api.py:1829-1839` (`_resolve_cors_origins`), `default_config.json` (`security.cors_origins: ["*"]`)

**Problem.** The Socket.IO server is created with `cors_allowed_origins=["*"]` by default. The
same socket accepts `terminal_input`, which writes keystrokes directly into live SSH/local
shells. Browsers do not block cross-origin WebSocket/Socket.IO connections on their own —
the server-side origin check is the only defence. With `*`, any web page open in the user's
browser can connect to `http://127.0.0.1:5050`, enumerate nothing (it needs session IDs), but
`session_status` broadcasts and `terminal_output` replays are emitted globally, and session IDs
are only 8 hex chars. A malicious page can realistically join sessions and inject commands.
This directly contradicts the "binds to 127.0.0.1, local use" security posture: localhost
binding does not protect against the user's own browser.

**Proposed implementation.**
1. Change the default in `default_config.json` and `_resolve_cors_origins()` from `["*"]` to
   same-origin only, derived from the configured host/port:
   ```python
   def _resolve_cors_origins():
       configured = app_config.get("security", {}).get("cors_origins")
       if configured:                      # explicit override still wins
           return configured
       port = app_config.get("server", {}).get("port", 5050)
       return [f"http://127.0.0.1:{port}", f"http://localhost:{port}"]
   ```
2. Flag the change in `CHANGELOG.md` (users with reverse proxies must set
   `security.cors_origins` explicitly).
3. Optionally also scope `terminal_output`/`session_status` emits to rooms only (they already
   use `room=session_id` for output; `session_status` in `_broadcast_session_status` is global —
   move it to the session room too).

> **✅ Implemented (2026-07-10).** `_resolve_cors_origins` in `web/api.py` now derives
> same-origin defaults (`http://127.0.0.1:<port>` + `http://localhost:<port>`, plus the
> configured host when it isn't loopback/wildcard) from `server.host`/`server.port`;
> `default_config.json` ships `security.cors_origins: []` so the derivation applies. An
> explicit non-empty `cors_origins` list (including `["*"]`) still wins for reverse-proxy
> setups. Flagged as breaking in `CHANGELOG.md`. Step 3 (room-scoping `session_status`) was
> **not** done. Covered by `CorsOriginDefaultsTestCase` in `tests/test_api.py`.

### 1.2 No Origin/CSRF guard on state-changing HTTP endpoints — **High**

**Location:** all `POST`/`DELETE` routes in `web/api.py` — notably `/api/sessions` (launch with
credentials in body), `/api/sessions` DELETE (kill everything), `/api/app-update` (git
fetch/pull), `/api/explorer/<id>/git/{stage,unstage,commit,publish}` (repo mutation),
`/api/app-config` (settings write).

**Problem.** A hostile web page can issue "simple" cross-origin `POST`s
(e.g. `fetch('http://127.0.0.1:5050/api/sessions', {method:'DELETE', mode:'no-cors'})`).
CORS prevents the attacker *reading* the response, but the side effect executes. Only
`/api/browser-shutdown` is protected (token header). Everything else is exposed:
drive-by git `push`, killing all sessions, changing settings.

**Proposed implementation.** Add a lightweight same-origin guard as a `before_request` hook:
```python
@app.before_request
def _reject_cross_origin_writes():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    origin = request.headers.get("Origin", "")
    host = request.host  # e.g. 127.0.0.1:5050
    if origin and urlparse(origin).netloc not in {host, host.replace("127.0.0.1", "localhost"), host.replace("localhost", "127.0.0.1")}:
        return jsonify({"error": "Cross-origin request rejected"}), 403
    return None
```
Requests from the app's own pages send a matching `Origin` (or none for same-origin
non-CORS requests and pywebview); cross-site `fetch`/form posts always send the attacker's
origin. Requiring a custom header (`X-GridVibe-Request: 1`) on all frontend `fetch` calls is an
equivalent alternative (custom headers force a CORS preflight, which then fails).

> **✅ Implemented (2026-07-10).** `web/api.py` gained a `_reject_cross_origin_writes`
> `before_request` hook (plus `_allowed_write_origin_netlocs` helper): non-GET/HEAD/OPTIONS
> requests carrying a cross-origin (or `null`) `Origin` header get `403`. Same-origin requests,
> requests with no `Origin` header (curl, pywebview), the `127.0.0.1`↔`localhost` alias pair,
> and any origins listed in `security.cors_origins` (with `"*"` disabling the guard) are
> allowed. Covered by `CrossOriginWriteGuardTestCase` in `tests/test_api.py`.

### 1.3 `_decrypt_password` silently returns the ciphertext on failure — **Medium**

**Location:** `web/api.py:140-147`

**Problem.** If the Fernet key changed (e.g. `.encryption_key` deleted/regenerated), decryption
fails and the function returns the *encrypted blob* as the password. GridVibe then sends that
garbage string as an SSH password — a confusing auth failure at best, and it leaks the
ciphertext to the remote server's auth log at worst.

**Proposed implementation.** Return `""` and log a warning once:
```python
def _decrypt_password(encrypted: str) -> str:
    if not encrypted:
        return ""
    try:
        return _cipher.decrypt(encrypted.encode()).decode()
    except Exception:
        logger.warning("Stored SSH password could not be decrypted (encryption key changed?); ignoring it.")
        return ""
```
The launcher then shows an empty password field instead of a broken one.

> **✅ Implemented (2026-07-10).** `_decrypt_password` in `web/api.py` now logs a warning and
> returns `""` on decryption failure instead of returning the ciphertext.

### 1.4 `AutoAddPolicy` for all SSH host keys — **Low** (posture, documented)

**Location:** `web/api.py` — `_connect_ssh_session`, `_open_ssh_sftp`, `_detect_ssh_command`

**Problem.** All three SSH entry points use `paramiko.AutoAddPolicy()`, so a MITM on first *and
every later* connection is accepted silently (keys are auto-added but never persisted to a
known_hosts file, so every connection is effectively "first"). CLAUDE.md documents this as a
deliberate default; this section only proposes making strictness available (see feature 10.7).

**Proposed implementation (minimal).** Load and persist a project-local known_hosts:
```python
client.load_host_keys(os.path.join(BASE_DIR, ".known_hosts"))
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
...
client.save_host_keys(os.path.join(BASE_DIR, ".known_hosts"))
```
This keeps first-use convenience but detects key *changes* on later connections.

> **✅ Implemented (2026-07-10).** New `_load_persistent_host_keys(client)` helper in
> `web/api.py` creates (if needed) and loads a project-local `.known_hosts`
> (`KNOWN_HOSTS_PATH`, added to `.gitignore`); it is called at all three SSH entry points
> (`_connect_ssh_session`, `_open_ssh_sftp`, `_detect_ssh_command`). No explicit
> `save_host_keys` call is needed: because `load_host_keys` records the filename, paramiko's
> `AutoAddPolicy` persists newly accepted keys automatically, and `connect` raises
> `BadHostKeyException` (a `SSHException`, surfaced through the existing error path) when a
> known host presents a different key. Load failures degrade gracefully to today's behaviour
> with a warning. Covered by `KnownHostsPersistenceTestCase` in `tests/test_api.py`.
> Feature 10.7 (configurable strict mode) remains open.

---

## 2. Race Conditions & Concurrency

### 2.1 `create_group` computes `display_order` outside the insertion lock — **Medium**

**Location:** `sessions/manager.py:122-159`

**Problem.** The method takes `self.lock` to compute `next_display_order`, releases it, builds
the `SessionGroup`, then re-acquires the lock to insert. Two concurrent launches can read the
same max order and both create groups with identical `display_order`, breaking tab ordering
(ties fall back to `created_at`, but `reorder_groups` assumes unique orders).

**Proposed implementation.** Do everything in one critical section:
```python
with self.lock:
    existing_group = self.groups.get(resolved_group_id)
    next_display_order = (
        existing_group.display_order if existing_group is not None
        else max((g.display_order for g in self.groups.values()), default=-1) + 1
    )
    group = SessionGroup(..., display_order=next_display_order, ...)
    self.groups[resolved_group_id] = group
return group
```

> **✅ Implemented (2026-07-10).** `create_group` in `sessions/manager.py` now computes
> `next_display_order`, builds the `SessionGroup`, and inserts it under a single `self.lock`
> hold, so concurrent launches can no longer observe the same max order.

### 2.2 `clear_disconnected_sessions` can delete a group that is mid-launch — **Medium**

**Location:** `sessions/manager.py:580-607`; called from `web/api.py:6004` and `:6022`

**Problem.** `clear_disconnected_sessions()` removes **every** group that currently has zero
sessions. `POST /api/sessions` first calls `create_group(...)` and only afterwards
`create_sessions(...)` — separate lock acquisitions. If a `DELETE /api/sessions/<id>` for some
*other* session lands in that window, the cleanup deletes the freshly created (still empty)
group. The launch then proceeds with sessions pointing at a `group_id` that no longer exists;
the terminals page shows no layout metadata for it.

**Proposed implementation.** Two options (either suffices):
1. **Grace period** — skip groups younger than a few seconds:
   ```python
   now = time.time()
   disconnected_groups = [
       gid for gid, group in self.groups.items()
       if gid not in active_group_counts and now - group.created_at > 5.0
   ]
   ```
2. **Atomic create** — add a `SessionManager.create_group_with_sessions(...)` that creates the
   group and its sessions under one lock hold, so an empty group is never observable.

Option 1 is a two-line fix; option 2 is the structurally correct one.

### 2.3 Connect-vs-close TOCTOU can leak a live SSH client / PTY — **Medium**

**Location:** `web/api.py:4570-4578` (`_connect_ssh_session`), `:4678-4686` (`_connect_local_session`)

**Problem.** Both connectors do:
```python
if session_manager.get_session(session_id) is None:   # check
    _shutdown_connection(connection); return
with connection_lock:
    ssh_connections[session_id] = connection           # act
```
If `close_session`/`_close_ssh_connection` runs between the check and the insert, the close
finds nothing in `ssh_connections` (no-op) and then the connector inserts a live connection for
a session that no longer exists. The paramiko client / WinPTY process stays open until process
exit; the stream thread keeps running.

**Proposed implementation.** Re-validate inside the lock, treating `connection_lock` as the
authority for the connection registry:
```python
with connection_lock:
    if session_manager.get_session(session_id) is None:
        stale = True
    else:
        ssh_connections[session_id] = connection
        session_output_buffers[session_id] = ""
        stale = False
if stale:
    _shutdown_connection(connection)
    return
```
(`session_manager.get_session` only takes the manager's RLock; nesting it inside
`connection_lock` here is safe because no code path takes the two locks in the opposite order —
worth a comment at both lock definitions.)

### 2.4 `socketio.emit` happens while holding `connection_lock` — **Medium**

**Location:** `web/api.py` — `_drain_until_prompt` (:2327-2333), `_stream_ssh_output`
(:2400-2406), `_stream_local_output` (three sites), `handle_join_session` replay (:6079-6101)

**Problem.** Every output chunk is emitted to Socket.IO clients *inside* `connection_lock`.
In `threading` async mode a slow/blocked client write can stall the emit, and while the lock is
held, **all** other terminals' output pumps, `terminal_input`, and `terminal_resize` handlers
block on the same global lock. One wedged websocket degrades every pane.

**Proposed implementation.** Only mutate the buffer under the lock; emit outside:
```python
if output:
    with connection_lock:
        _cache_terminal_output(session_id, output)
    socketio.emit('terminal_output', {'session_id': session_id, 'data': output}, room=session_id)
```
The replay-vs-live ordering concern in `handle_join_session` can be preserved by capturing the
buffer snapshot under the lock and emitting after, since `join_room` already happened under the
lock (late chunks arrive after the replay by construction of the room membership).

### 2.5 Agent binary detection runs a subprocess while holding the global cache lock — **Medium**

**Location:** `web/api.py:1592-1610` (`_detect_agent_binary_cached`)

**Problem.** On a cache miss, `_detect_agent_binary(...)` — a subprocess call with up to an 8 s
timeout (WSL probe) — executes *inside* `_agent_detection_cache_lock`. Concurrent preflight
requests for different agents/rows serialize behind it; the launcher fires one preflight per
terminal row, so an 8-terminal WSL setup can take ~1 minute of serialized probing instead of
running probes concurrently.

**Proposed implementation.** Standard "check, compute outside, re-check" pattern:
```python
with _agent_detection_cache_lock:
    cached = _agent_detection_cache.get(key)
    if cached and now - cached[0] <= TTL:
        return dict(cached[1])
detection = _detect_agent_binary(target, binary)      # slow work, no lock
with _agent_detection_cache_lock:
    _agent_detection_cache[key] = (time.monotonic(), dict(detection))
return detection
```
Duplicate concurrent probes for the *same* key are acceptable (idempotent, cached afterwards);
if not, keep a per-key `threading.Lock` in a second dict.

### 2.6 Voice engine may differ between start / audio / stop — **Low**

**Location:** `web/api.py:6728-6789` (`handle_voice_start/audio/stop` all re-read the
`voice_engine` global)

**Problem.** If the user changes the engine in App Settings while a recording is active,
`voice_stop` routes to the *new* engine: a vosk WebSocket stays open in
`_vosk_ws_connections`, or a whisper buffer stays in `_whisper_audio_buffers` until the next
start overwrites it.

**Proposed implementation.** Record the engine per active voice session at start
(`_active_voice_sessions[session_id] = engine`) and dispatch `voice_audio`/`voice_stop` based
on the recorded value, deleting the entry on stop. ~10 lines.

### 2.7 Whisper model instance survives settings changes — **Medium**

**Location:** `web/api.py:6236-6262` (`_ensure_whisper_model`), `_refresh_runtime_config`

**Problem.** `_whisper_model_instance` is created once with the model/device/compute-type
captured at first use. `POST /api/app-config` refreshes the globals but never invalidates the
instance, so switching from `base` to `large-v3` (or CPU→CUDA) silently keeps using the old
model until a full restart. The settings UI implies it takes effect.

**Proposed implementation.** Track the parameters the instance was built with and rebuild on
mismatch:
```python
with _whisper_model_lock:
    wanted = (whisper_model, whisper_device, whisper_compute_type)
    if _whisper_model_instance is None or _whisper_model_params != wanted:
        _whisper_model_instance = WhisperModel(*wanted[:1], device=wanted[1], compute_type=wanted[2])
        _whisper_model_params = wanted
    return _whisper_model_instance
```

> **✅ Implemented (2026-07-10).** `_ensure_whisper_model` in `web/api.py` now records the
> `(model, device, compute_type)` tuple it built the instance with in a new
> `_whisper_model_params` global and rebuilds the `WhisperModel` under `_whisper_model_lock`
> whenever the current config no longer matches; the old instance is released to GC.

### 2.8 8-character session IDs stored without collision check — **Low**

**Location:** `sessions/manager.py:200, 251`

**Problem.** `str(uuid.uuid4())[:8]` has a birthday-collision probability that becomes
non-negligible over thousands of sessions; a collision silently overwrites the existing
`self.sessions[session_id]` entry, orphaning its SSH connection.

**Proposed implementation.** Generate under the lock and retry on collision:
```python
with self.lock:
    while True:
        session_id = uuid.uuid4().hex[:8]
        if session_id not in self.sessions:
            break
    self.sessions[session_id] = session
```

### 2.9 `active_launch_options` mutated per-request without a lock — **Low**

**Location:** `web/api.py:945-949, 5656-5662`

**Problem.** Multi-key `dict.update` from request threads while `GET /api/sessions` reads the
same dict. CPython makes each op atomic, so the worst case is a momentarily inconsistent
layout/count pair — cosmetic, but easy to harden.

**Proposed implementation.** Replace wholesale instead of updating in place:
`active_launch_options = {**active_launch_options, ...}` assigned to a module global (atomic
reference swap), or guard with a small lock.

---

## 3. Performance & Optimisations

### 3.1 Every explorer request opens a fresh SSH + SFTP connection — **High impact**

**Location:** `web/api.py:4286-4302` (`_open_ssh_sftp`) used by all 8 remote explorer routes

**Problem.** Each directory listing, file preview, git diff, stage/unstage, commit, and publish
performs a full TCP + SSH handshake + auth + SFTP subsystem open, then tears it down. That is
typically 300–1000 ms of overhead per click, and remote git panels issue *several* of these
per refresh (`entries` → `git/repo` → `git/diff`). This is the single largest perceived-latency
cost in the remote explorer.

**Proposed implementation.** Add a small per-session connection cache:
```python
_sftp_pool: Dict[str, Tuple[float, Any, Any]] = {}   # session_id -> (last_used, client, sftp)
_sftp_pool_lock = threading.Lock()
SFTP_IDLE_TIMEOUT = 60.0

def _acquire_ssh_sftp(session):
    with _sftp_pool_lock:
        entry = _sftp_pool.get(session.session_id)
        if entry and entry[1].get_transport() and entry[1].get_transport().is_active():
            _sftp_pool[session.session_id] = (time.monotonic(), entry[1], entry[2])
            return entry[1], entry[2]
    client, sftp = _open_ssh_sftp(session)            # existing function
    with _sftp_pool_lock:
        _sftp_pool[session.session_id] = (time.monotonic(), client, sftp)
    return client, sftp
```
- Reap idle entries from a small background task (or opportunistically on each acquire).
- Evict on session close (`_close_ssh_connection` / group close).
- Per-session `threading.Lock` if SFTP channels must not be used concurrently (paramiko SFTP
  is not thread-safe per channel), or open one channel per in-flight request from the cached
  *client* (transport handshake is the expensive part; `client.open_sftp()` on a live transport
  is cheap).
- Keep the current open/close path as fallback when pooling fails.

### 3.2 Rolling output buffer copies up to 50 KB per output chunk — **Medium**

**Location:** `web/api.py:2055-2059` (`_cache_terminal_output`)

**Problem.** `session_output_buffers[sid] = (existing + output)[-50000:]` allocates and copies
the whole 50 KB tail for every 4 KB chunk, under `connection_lock`. A busy pane (e.g. `yes`,
build logs) makes this a hot allocation loop that also lengthens lock hold times (compounding
finding 2.4).

**Proposed implementation.** Store chunks in a `collections.deque` with running length:
```python
buf = session_output_buffers.setdefault(sid, deque())
buf.append(output); total = sum(map(len, buf))
while total > 50000 and len(buf) > 1:
    total -= len(buf.popleft())
```
Join with `''.join(buf)` only at replay time (`handle_join_session`), which is rare.

### 3.3 SSH output loop busy-polls at 50 ms with a lock acquisition per iteration — **Low**

**Location:** `web/api.py:2385-2412` (`_stream_ssh_output`)

**Problem.** The loop wakes 20×/second per idle SSH pane, each time taking `connection_lock`
just to fetch the connection dict. Eight idle panes = 160 lock acquisitions/sec doing nothing.

**Proposed implementation.** Fetch the connection once before the loop (it never changes for a
session lifetime; only its presence matters) and use paramiko's blocking recv with timeout:
```python
channel.settimeout(0.5)
while not channel.closed:
    try:
        output = channel.recv(4096).decode("utf-8", errors="ignore")
    except socket.timeout:
        continue
    if not output:
        break
    ...
```
Idle cost drops to ~2 wakeups/sec, and disappearance from `ssh_connections` is detected via the
channel close that `_close_ssh_connection` already performs.

### 3.4 3-second full-state polling on top of Socket.IO push — **Medium**

**Location:** `templates/terminals.html:12941` (`setInterval(refreshStatuses, 3000)`),
`refreshStatuses` fetches `/api/session-groups` + `/api/sessions?group=…` every tick;
`main.py:33` exists solely to suppress the resulting log spam.

**Problem.** Session status changes are already pushed over Socket.IO (`session_status`), and
mode/pane changes trigger `initialLoad()` from the socket handler. The 3 s poll is a
reconciliation safety net doing two HTTP requests per open window forever — and it required a
special werkzeug log filter to hide.

**Proposed implementation.**
1. Emit a `session_groups_updated` Socket.IO event from the backend whenever groups are
   created/removed/reordered (`create_sessions`, `close_all_sessions`, `reorder_session_groups`).
2. Keep the poll as fallback but at 15–30 s, and pause it entirely while the socket is
   connected (`socket.connected === true`), resuming on `disconnect`.
3. The `_SuppressPollLogs` filter can then be deleted.

### 3.5 ~18k lines of inline CSS/JS re-parsed on every page load — **Medium**

**Location:** `templates/index.html` (5.3k lines), `templates/terminals.html` (12.9k lines)

**Problem.** Everything ships inline in the HTML: no browser caching, no HTTP caching between
launcher/session windows, and both Jinja templates are monolithic and hard to navigate. The
session window is reopened often (new groups, restarts), re-downloading and re-parsing ~600 KB
each time.

**Proposed implementation.** Extract to `web/static/`:
- `static/css/launcher.css`, `static/css/terminals.css`, `static/css/tokens.css` (shared
  variables — see 7.1)
- `static/js/launcher.js`, `static/js/terminals.js`, `static/js/shared.js` (see 6.4)
- Keep only Jinja-templated constants inline (`{{ max_sessions }}`, `{{ agent_options|tojson }}`,
  data-attributes on `<body>` — the terminals page already demonstrates the data-attribute
  pattern for voice config).
Serve via Flask's static handling with cache headers; bust with `?v={{ version }}` using
`gridvibe_version.__version__`.

### 3.6 xterm.js and socket.io are loaded from a CDN — **High** (reliability)

**Location:** `templates/terminals.html:7, 2927-2929`

**Problem.** GridVibe is a local-first tool (docs emphasise offline/localhost operation), yet
the terminals page hard-depends on `cdn.jsdelivr.net`. No network (or a corporate proxy) means
*no terminals at all* — the `try/catch` around `io()` only degrades to polling, and `Terminal`
being undefined breaks the page. It is also a supply-chain exposure: a compromised CDN script
runs with access to every terminal.

**Proposed implementation.** Vendor the three files (xterm 5.3.0 css+js, xterm-addon-fit 0.8.0,
socket.io-client 4.7.2) into `web/static/vendor/` and reference them via `url_for('static', ...)`.
~200 KB total, licence-compatible (MIT). Optionally add Subresource Integrity if the CDN must
stay.

### 3.7 Fallback local-output path reads 1 byte at a time — **Low**

**Location:** `web/api.py:2493-2506` (`_stream_local_output`, `stdout_handle` branch)

**Problem.** `stdout_handle.read(1)` does a syscall per byte and emits per-character Socket.IO
messages. This path is only hit when neither WinPTY nor a POSIX PTY is available (rare), but
when hit it is pathological.

**Proposed implementation.** `os.read(stdout_handle.fileno(), 4096)` /
`stdout_handle.read1(4096)` with the same loop structure as the other branches.

---

## 4. Correctness Bugs

### 4.1 PowerShell startup directory is quoted with POSIX rules — **Medium**

**Location:** `web/api.py:2360-2363` (`_run_startup_sequence`)

**Problem.**
```python
_send_connection_input(connection, f"Set-Location -LiteralPath {shlex.quote(target_directory)}{newline}")
```
`shlex.quote` implements POSIX shell quoting. For simple paths it happens to produce
PowerShell-compatible single quotes, but: (a) a path *without* special chars is passed
unquoted, so `C:\Program Files\x` becomes two arguments; (b) a path containing a single quote
is quoted as `'…'"'"'…'`, which PowerShell parses differently. The codebase already has the
correct helper, `_powershell_single_quote` (`web/api.py:1113`), used elsewhere.

**Proposed implementation.**
```python
elif shell_kind == "powershell":
    _send_connection_input(
        connection,
        f"Set-Location -LiteralPath {_powershell_single_quote(target_directory)}{newline}",
    )
```
Note `C:\Program Files` paths are common on the exact platform (Windows) this branch targets.

> **✅ Implemented (2026-07-10).** The `powershell` branch of `_run_startup_sequence` now quotes
> the target directory with the existing `_powershell_single_quote` helper instead of
> `shlex.quote`.

### 4.2 xterm theme uses the removed `selection` key — **Low**

**Location:** `templates/terminals.html:6139-6144` (`makeTerminal`)

**Problem.** xterm.js 5.x renamed `theme.selection` to `theme.selectionBackground` (removed in
5.0). The configured cyan selection tint is silently ignored; selections render with the
default colour.

**Proposed implementation.**
```js
theme: {
    background: '#0d0d0d',
    foreground: '#e0e0e0',
    cursor: '#00d9ff',
    selectionBackground: 'rgba(0,217,255,.25)'
}
```

> **✅ Implemented (2026-07-10).** `makeTerminal()` in `templates/terminals.html` now sets
> `selectionBackground` instead of the removed `selection` theme key, restoring the cyan
> selection tint.

### 4.3 Launch button renames itself after the first launch — **Low**

**Location:** `templates/index.html:2338` (initial label `Launch Workspace →`) vs `:5233, 5238`
(reset to `'Launch Terminals'` with no arrow)

**Problem.** After any launch attempt (success or failure) the primary CTA permanently changes
from "Launch Workspace →" to "Launch Terminals". Cosmetic, but it is the most prominent button
in the app.

**Proposed implementation.** Capture and restore the original markup:
```js
const originalHtml = button.innerHTML;      // before disabling
...
button.innerHTML = originalHtml;            // in both reset paths
```
(There are two reset sites — success `setTimeout` and the `catch` — update both, or extract a
`resetLaunchButton()` helper.)

### 4.4 Save Session uses `window.prompt`, which WebView2 blocks — **High**

**Location:** `templates/index.html:4765-4799` (`saveCurrentConfig`)

**Problem.** `window.prompt(...)` returns `null` in pywebview's EdgeChromium (WebView2)
backend — WebView2 does not implement `prompt()` unless the host handles the script-dialog
event, which `webview_launcher.py` does not. `rawName === null` → the function returns
silently. In the native desktop app the "💾 Save Session" button therefore does nothing, with
no error. (The terminals window already solved this: it has a proper `saveSessionAsModal`
dialog.)

**Proposed implementation.** Replace the `prompt()` call with a small name-input modal
mirroring `saveSessionAsModal` from `terminals.html` (title "Save Session", pre-filled with
`buildDefaultSessionName()`, Save/Cancel). Since 6.4 proposes sharing template JS anyway, the
modal + `openSaveSessionAsModal()` logic can be lifted verbatim into the shared file and reused
by both pages.

> **✅ Implemented (2026-07-10).** `templates/index.html` gained a `saveSessionNameModal`
> (mirroring the terminals page's `saveSessionAsModal` pattern: promise-based
> `openSaveSessionNameModal()` / `closeSaveSessionNameModal()`, pre-filled with
> `buildDefaultSessionName()`, Enter submits, Escape/backdrop cancels), and
> `saveCurrentConfig` awaits it instead of calling `window.prompt`. Implemented directly in
> `index.html` since the shared-JS extraction (6.4) hasn't landed yet — lift it into
> `shared.js` when that refactor happens.

### 4.5 `info` and `warning` message types are used but unstyled — **Low**

**Location:** `templates/index.html` — CSS defines only `.message.success/.message.error`
(`:495-496`) and `.inline-status.success/.error`; but code calls
`showMessage(..., 'info')` (`:4050, 5084`) and `showMessage(..., 'warning')` (`:5211`).

**Problem.** Info and warning messages render in the default muted grey — a launch that cleared
startup commands after failed preflight (a warning users should notice) looks identical to the
idle hint text.

**Proposed implementation.** Add the two missing rules to both pages' message/status styles:
```css
.message.info,   .toolbar-status.info,   .inline-status.info   { color: var(--accent); }
.message.warning,.toolbar-status.warning,.inline-status.warning{ color: #f59e0b; }
```
(Consider adding `--warning: #f59e0b` to the token set — there is currently no warning colour
variable in either palette, which is why this hole appeared.)

> **✅ Implemented (2026-07-10).** `templates/index.html` now defines a `--warning` token
> (`#f59e0b` dark, `#b45309` light for contrast) and `.info` / `.warning` rules for all three
> status classes (`.message`, `.toolbar-status`, `.inline-status`), with info mapped to
> `var(--accent)`.

### 4.6 `setLocalRepoPath` updates a DOM node that no longer exists — **Low**

**Location:** `templates/index.html:3953-3969`

**Problem.** The function looks up `wsl_default_dir_display` and maintains its text/empty
state, but `WSL_FIELDS` (`:2805-2819`) renders only the `wsl_default_dir` input — the display
element was removed in an earlier refactor. Dead branch executed on every path change.

**Proposed implementation.** Delete the `display` lookup and the `if (display) {...}` block.

### 4.7 `config.json` silently overrides explicit CLI flags — **Medium**

**Location:** `main.py:104-107`, duplicated in `web/webview_launcher.py:929-932`

**Problem.**
```python
host = config.get("server", {}).get("host", args.host)
```
Config wins over CLI. A user who runs `python main.py --port 8080` while `config.json` contains
`"port": 5050` gets 5050 with no warning — inverted from the universal convention that explicit
command-line flags beat config files.

**Proposed implementation.** Detect whether flags were explicitly passed by using `None`
defaults:
```python
parser.add_argument("--port", type=int, default=None, ...)
...
port = args.port if args.port is not None else config.get("server", {}).get("port", 5050)
```
Same for `--host` and `--debug` (use `default=None` + `action=argparse.BooleanOptionalAction`
or a tri-state). Apply in both entry points (or better: extract one shared
`resolve_server_settings(args)` helper, see 5.7).

### 4.8 Vosk service port is configured in two disconnected keys — **Medium**

**Location:** `default_config.json` (`vosk_service_url` **and** `vosk_service_port`);
`web/api.py:300` reads only the URL; `services/vosk_service.py:45` reads only the port.

**Problem.** Changing `vosk_service_port` moves the service, but the API keeps dialing the old
URL (and vice versa). The two processes desync with a confusing "voice service unavailable"
failure. Additionally `vosk_service.py._load_config_defaults` reads only `config.json` — it
ignores `default_config.json`, unlike every other config consumer.

**Proposed implementation.**
1. Make `vosk_service_url` the single source of truth. In `vosk_service.py`, parse the port
   from it:
   ```python
   from urllib.parse import urlparse
   port = urlparse(voice.get("vosk_service_url", "ws://localhost:2700")).port or DEFAULT_PORT
   ```
2. Remove `vosk_service_port` from `default_config.json` (accept it as a fallback for one
   release, warn when present).
3. Have `_load_config_defaults` reuse the layered loading (import `load_config` from `web.api`
   is heavy — instead replicate the two-file merge in ~6 lines, or move `load_config` into a
   tiny `utils/config.py` both can import).

### 4.9 `cleanup.py` deletes live logs, and has leftover/odd semantics — **Low**

**Location:** `utils/cleanup.py`

**Problems.**
1. The `*.log` pattern deletes `logs/gridvibe.log` while the server may be running — on Windows
   the file is locked by `RotatingFileHandler`, producing "Error removing" spam; on POSIX it
   silently unlinks the active log.
2. The banner prints **"Terminal Flow Cleanup"** — a previous project name.
3. `find_and_remove("*.pyc")` also matches *directories* ending in `.pyc` and would `rmtree`
   them; the `is_dir` parameter only controls pruning, not whether the dir branch runs.
4. `DESTRUCTIVE_PATTERNS = []` is unused (see 5.4).

**Proposed implementation.** Rewrite the walker with explicit dir/file pattern lists (mirroring
what `make clean` / `make clear-logs` already do): `DIR_PATTERNS = {"__pycache__"}`,
`FILE_PATTERNS = {"*.pyc", "*.pyo"}`, and handle logs by *truncating* (`path.write_text("")`)
rather than unlinking. Update the banner to "GridVibe Cleanup". Alternatively, deprecate
`cleanup.py` entirely in favour of the Makefile targets, keeping the root shim printing a
pointer message.

### 4.10 Misindented `return jsonify` block in `get_explorer_entries` — **Low**

**Location:** `web/api.py:4958-4967`

**Problem.** The local-explorer success return is indented one level deeper than its siblings
(dict keys at odd offsets). Valid Python, but visually implies nesting that does not exist and
will trip up future edits.

**Proposed implementation.** Re-indent the block to match the remote branch's formatting.
`ruff format` (or `ruff check --select E1`) would catch this class of issue; consider adding
formatting to `make fix`.

---

## 5. Dead Code & Dead Config

### 5.1 Unused config keys promise features that don't exist — **Medium**

**Location:** `default_config.json` — `ssh.default_username`, `ssh.keepalive_interval`,
`terminal.default_rows`, `terminal.default_cols`, `terminal.font_family`, `terminal.font_size`

**Problem.** Grep confirms none of these keys are read anywhere in the codebase. Users editing
them see no effect. Two of them (`keepalive_interval`, `font_size`) describe genuinely useful
behaviour (see features 10.1 / 10.2).

**Proposed implementation.** For each key, either wire it up (preferred where cheap — see
10.1/10.2) or delete it from `default_config.json` and mention the removal in `CHANGELOG.md`.
`ssh.default_username` overlaps with the per-saved-session username and should be removed;
`default_rows/cols` are superseded by the fit addon and should be removed.

### 5.2 `/api/sessions/active` has no frontend callers — **Low**

**Location:** `web/api.py:5349-5373`

**Problem.** Neither template references `sessions/active`; the endpoint is exercised only by
tests. It duplicates `/api/sessions` filtering logic and is one more surface to keep consistent.

**Proposed implementation.** Delete the route and its tests, or — if it is considered public
API for external tooling — document it in the README API table so it stops looking accidental.

### 5.3 `SessionManager`'s callback registry is dead — **Low**

**Location:** `sessions/manager.py:120, 550-569` (`_session_callbacks`, `register_callback`,
`_notify_callbacks`)

**Problem.** `register_callback` is never called outside `sessions/manager.py` and its tests,
so `_notify_callbacks` iterates an always-empty list on every status update — while holding the
manager lock (the docstring even warns about the re-entrancy hazard). Pure ceremony.

**Proposed implementation.** Remove `register_callback`, `_notify_callbacks`,
`_session_callbacks`, the two call sites in `update_session_status`/`close_session`, the
cleanup in `_remove_group_sessions_locked` / `reset_sessions` / `clear_disconnected_sessions`,
and the associated tests. If status hooks are wanted later, Socket.IO broadcast in
`_broadcast_session_status` already fills that role at the web layer.

### 5.4 Assorted dead fragments — **Low**

**Locations / items.**
- `utils/cleanup.py:8` — `DESTRUCTIVE_PATTERNS = []` never used.
- `web/webview_launcher.py:33-40` — `_NATIVE_FRAME_THEMES` is a one-entry dict keyed by a
  constant; `_apply_windows_dark_frame_attributes` could inline the three colours.
- `sessions/manager.py:576-578` — `get_active_session_count` unused outside tests.
- `templates/index.html` `.section-title svg { display: none; }` — every step-title SVG is
  rendered and then hidden by CSS; delete the SVGs or the rule.

**Proposed implementation.** Delete each; none has behavioural impact. (Batchable into one
"dead code sweep" commit validated by `make check`.)

### 5.5 `set_native_theme(theme)` ignores its argument — **Low**

**Location:** `web/webview_launcher.py:558-562`; called from both templates' `syncNativeTheme`

**Problem.** The JS bridge dutifully computes and passes the resolved theme on every theme
change, but the Python side unconditionally sets `self._native_theme = _NATIVE_FRAME_THEME`
("dark"). Either the parameter (and the JS plumbing that computes it) is dead weight, or the
intent was a light native frame that never got finished — the launcher shows a dark title bar
around a light-themed page.

**Proposed implementation.** Decide the intent:
- **Keep frames always-dark (current behaviour):** change the signature to
  `set_native_theme(self, _theme=None)` with a comment, and simplify `syncNativeTheme` in both
  templates to pass nothing.
- **Support light frames:** add a `"light"` entry to `_NATIVE_FRAME_THEMES` (caption `#f8fafc`,
  text `#111827`, border `#e5e7eb`), honour the parameter, and set DWM attribute 20 to 0 for
  light mode.

### 5.6 The `⋮` Options button on launcher terminal cards does nothing — **Low**

**Location:** `templates/index.html:4519` (`.t-menu-btn`, `title="Options"`) — no event handler
anywhere.

**Problem.** Every terminal card renders a kebab-menu button that has never been wired up.
Users click it and nothing happens.

**Proposed implementation.** Either remove it, or implement the obvious menu (this is also a
button improvement): a small popover with **Duplicate terminal** (copy this row's draft into
the next slot), **Reset to defaults**, and **Move left/right** (reorder drafts). All three
operate purely on `collectTerminalDrafts()` + `buildTerminalRows()`, so no backend work is
needed. Removal is a one-line change if the menu isn't wanted.

### 5.7 Three near-identical server-run entry points — **Low**

**Location:** `main.py:117-125`, `web/api.py:6794-6805` (`run_server`), `web/webview_launcher.py:896-905`
(`_run_server`)

**Problem.** Three code paths call `socketio.run(app, ...)` with slightly different flags
(`allow_unsafe_werkzeug` present in two, `use_reloader=False` in one). Behaviour drifts —
e.g. `web/api.py.run_server` lacks `allow_unsafe_werkzeug=True`, so `python api.py` (the
documented shim path) can refuse to start under newer Werkzeug while `python main.py` works.

**Proposed implementation.** Single `run_server(host, port, debug, *, use_reloader=False)` in
`web/api.py` with all flags; `main.py` and the launcher call it. Also the config-vs-CLI
resolution (finding 4.7) should live in this one place.

---

## 6. Code Quality / DRY / Architecture

### 6.1 Local vs remote explorer/git logic is systematically duplicated — **High value**

**Location:** `web/api.py` ~lines 2900–4420 plus all explorer routes (~2,000 lines total).
Function pairs include: `_resolve_explorer_candidate_path` / `_resolve_remote_explorer_candidate_path`,
`_git_status_for_entry` / `_remote_git_status_for_entry` (byte-identical logic),
`_append_deleted_git_entries` / `_append_deleted_remote_git_entries`,
`_bounded_git_diff` / `_bounded_remote_git_diff`, `_get_git_repo_summary` /
`_get_remote_git_repo_summary`, `_git_stage_path` / `_remote_git_stage_path`, … and every
explorer route contains an `if _is_remote_explorer_session(session): …duplicate body… else:
…duplicate body…` split.

**Problem.** Each behavioural change (e.g. the recently added stage/unstage/commit/publish)
must be written twice and reviewed twice; the two copies have already begun to drift
(`_get_git_diff` returns `byte_count`+`line_count`, `_get_remote_git_diff` returns `raw_bytes`
— frontend has to tolerate both).

**Proposed implementation.** Introduce an explorer backend abstraction:
```python
class ExplorerBackend(Protocol):
    def resolve_dir(self, requested) -> tuple[str, str]: ...
    def resolve_file(self, requested) -> tuple[str, str]: ...
    def list_entries(self, path) -> list[dict]: ...
    def read_file(self, path, limit) -> bytes: ...
    def run_git(self, args, cwd, timeout, write=False) -> CompletedProcess: ...
    def rel_path(self, root, path) -> str: ...
    def path_join / path_dirname / path_inside(...) -> ...: ...

class LocalExplorerBackend: ...      # os / subprocess implementations
class SftpExplorerBackend:           # paramiko implementations
    def __enter__/__exit__: ...      # owns client lifetime (pairs with pooling, 3.1)
```
All `_git_*` helpers then exist **once**, parameterised by `backend.run_git` and the path
helpers. Each route body shrinks to:
```python
with explorer_backend_for(session) as backend:
    root, path = backend.resolve_file(request.args.get("path"))
    return jsonify(_git_diff_payload(backend, root, path, mode, commit))
```
This is the highest-leverage refactor in the codebase (roughly −1,000 lines) and the natural
place to slot in the SFTP connection pool (3.1). Migrate one route at a time with the existing
tests (`tests/test_api.py` is 5.8k lines and covers these routes) as the safety net.

### 6.2 `web/api.py` is a 6,805-line monolith — **Medium**

**Location:** `web/api.py`

**Problem.** Config, crypto, saved sessions, agent preflight, self-update, SSH/PTY session
plumbing, two explorer implementations, git tooling, HTTP routes, Socket.IO handlers, and two
voice engines all share one module and one global namespace (60+ module globals). Navigation
and review cost is high; import side effects (Fernet key creation, config load, Flask app
creation) all fire on `import web.api`.

**Proposed implementation.** Split along the seams that already exist as comment banners:
```
web/
├── app.py            # Flask app + SocketIO creation, blueprint registration
├── config.py         # load/save/merge config, runtime globals → a Config object
├── secrets.py        # Fernet key + password encrypt/decrypt
├── saved_sessions.py # saved-session persistence + normalization
├── agents.py         # registry, detection, preflight
├── selfupdate.py     # perform_self_update + route
├── terminal_io.py    # connections dict, streams, startup sequence, resize
├── explorer/         # backend abstraction from 6.1 + routes
├── voice.py          # vosk + whisper handlers
└── api.py            # thin re-export shim for backward compatibility
```
Keep `web.api` re-exporting `app`, `socketio`, `session_manager`, `load_config` so `main.py`,
the launcher, the root shims, and the tests keep working during the transition. Do it
incrementally (one module per PR), running `make check` between steps.

### 6.3 `create_session` and `append_session_to_group` duplicate a 17-parameter signature — **Low**

**Location:** `sessions/manager.py:161-282`

**Problem.** Both methods take the identical parameter list, build the identical
`TerminalSession(...)`, and differ only in the group-existence check + counter increment.
Adding a session field currently means touching four places (dataclass, both methods,
`create_sessions`).

**Proposed implementation.**
```python
def _build_session(self, group_id: str, **fields) -> TerminalSession:
    return TerminalSession(session_id=self._new_session_id(), group_id=group_id,
                           status=SessionStatus.PENDING, **fields)

def create_session(self, group_id, **fields):
    session = self._build_session(group_id, **fields)
    with self.lock:
        self.sessions[session.session_id] = session
    return session

def append_session_to_group(self, group_id, **fields):
    session = self._build_session(group_id, **fields)
    with self.lock:
        group = self.groups.get(group_id)
        if group is None:
            return None
        self.sessions[session.session_id] = session
        group.terminal_count += 1
    return session
```
Callers already pass keyword arguments, so the change is source-compatible.

### 6.4 Shared JS is copy-pasted between the two templates — **Medium**

**Location:** duplicated between `templates/index.html` and `templates/terminals.html`:
theme management (`normalizeThemePreference` → `initTheme`, ~120 lines), `escHtml`,
`getConnectionModeLabel`, `isAbsoluteDirectory`/`normalizeComparableDirectory`/
`joinDirectories`/`resolveTerminalDirectory`/`buildLaunchDirectory`, `getDirectoryName`,
saved-session card builders + modal plumbing, BroadcastChannel/storage-event sync constants
and listeners.

**Problem.** The copies have already drifted (e.g. the terminals page's saved-session card has
no `selectable` mode; its `buildSavedSessionTags` differs). Directory-resolution rules are
security-relevant (they gate what gets sent as `directory`) and should not exist twice.

**Proposed implementation.** Create `web/static/js/shared.js` exposing a `GridVibeShared`
namespace with the theme module, path helpers, `escHtml`, and the saved-session card renderer
(taking an options object for the selectable/delete variants). Both pages load it before their
page script. This pairs naturally with 3.5 (extracting page JS to static files).

### 6.5 Monster functions in terminals.html — **Medium**

**Location:** `templates/terminals.html` — `buildGrid` (:6651-7095, ~445 lines),
`_startVoice` (:8632-8939, ~308 lines), `initialLoad` (~150 lines) plus ~40 explorer render
helpers forming an implicit module.

**Problem.** `buildGrid` constructs card HTML, wires drag&drop, split controls, explorer
controls, browser controls, voice panels, and clipboard in one pass; `_startVoice` handles
permission flow, device selection, AudioContext/worklet setup, diagnostics, and error paths in
one function. Both are effectively untestable and are where regressions cluster.

**Proposed implementation.** Decompose along the seams the code already hints at:
- `buildGrid` → `buildPaneCard(session, index)` (returns element), `wirePaneControls(card, index, session)`
  (delegates to the existing `wireSplitCardControls`/`wireExplorerOnlyControls`/…), and a thin
  `buildGrid` that loops.
- `_startVoice` → `acquireMicStream(prefs)`, `createVoicePipeline(stream, onChunk)`,
  `renderVoiceDiagnostics(...)` (exists), with `_startVoice` orchestrating.
No behaviour change; do it as part of the JS extraction (3.5) so the new files start reviewable.

### 6.6 Two similarly-named git runners already caused a production TypeError — **Medium**

**Location:** `web/api.py` — `_run_self_update_git_command(args)` (cwd fixed to repo) vs
`_run_git_command(args, *, cwd, ...)` (explorer, keyword-only cwd). Evidence:
`logs/gridvibe.log` (2026-07-04, 4×) shows
`TypeError: _run_git_command() missing 1 required keyword-only argument: 'cwd'` raised from
`perform_self_update` — i.e. the self-updater called the wrong helper for several days before
being fixed.

**Problem.** The names invite exactly this mix-up, and there are now *four* runners
(`_run_git_command`, `_run_git_write_command`, `_run_remote_git_command`,
`_run_remote_git_write_command`) plus the self-update one.

**Proposed implementation.** Consolidate into one signature family (folds into 6.1):
```python
def run_git(args, *, cwd, timeout=2.0, write=False) -> CompletedProcess   # local
# env = GIT_OPTIONAL_LOCKS=0 for reads, GIT_TERMINAL_PROMPT=0 for writes
```
and rename the self-update one to `_run_repo_git(args)` so a positional-only call can't
accidentally bind to the explorer runner. Add a regression test that hits
`POST /api/app-update` end-to-end against a temp git repo (the July log shows this route had
no test coverage for the happy path at the time).

---

## 7. Style Mismatches (UI)

### 7.1 Launcher and terminals pages use two unrelated design systems — **Medium**

**Location:** `templates/index.html:8-30` vs `templates/terminals.html:9-74`

**Problem.** The launcher is a slate/indigo glassmorphism theme (`--bg:#070b18`,
`--accent:#4cc9f0`, 12–16 px radii, `backdrop-filter: blur`), while the terminals workspace is
a flat neutral-black theme (`--t-bg:#0d0d0d`, `--t-accent:#00d9ff`, tighter radii, no blur).
Even the accents differ (`#4cc9f0` vs `#00d9ff`), as do success greens (`#22c55e` vs
`#18b66a`) and the light-theme accent (`#087f9b` in both — the one shared value, by
coincidence). Moving between the two windows feels like switching apps.

**Proposed implementation.** Create a shared `tokens.css` (see 3.5) defining one palette:
```css
:root { --gv-bg-app:…; --gv-accent:#00d9ff; --gv-success:…; --gv-danger:…;
        --gv-warning:#f59e0b; --gv-radius-s/m/l:…; }
```
Then map both pages' existing variable names onto the shared tokens
(`--accent: var(--gv-accent)`, `--t-accent: var(--gv-accent)`) so the visual unification lands
without rewriting thousands of selector rules. Decide deliberately which identity wins
(recommendation: keep the terminals page's darker neutral background for the workspace, adopt
one accent + one success/danger/warning set everywhere). The intentional exception —
`--t-terminal-bg` staying dark in light theme — should get a comment.

### 7.2 Mixed icon language: emoji vs SVG vs text glyphs — **Low**

**Location:** `templates/index.html:2287-2289, 2343` (💾 📂 🗑 🌙); `templates/terminals.html:2797`
(🌙), `:6194-6199` (↻ and 🧹 as button text), `:2829` (fullscreen `&#9974;`), while neighbouring
buttons use consistent inline SVG (refresh-all, surface-mode, settings, topbar toggle).

**Problem.** Emoji render differently per OS/font (Windows Segoe UI Emoji vs browser
fallbacks), can't inherit `currentColor`, ignore the theme, and sit at inconsistent optical
sizes next to the SVG icons.

**Proposed implementation.** Replace the emoji/text glyphs with the same stroke-style SVGs used
elsewhere (Feather-style, `stroke="currentColor"`, `stroke-width 1.8-2`): save → floppy/arrow-down-tray,
import → folder-open, delete → trash, theme → sun/moon SVG pair, clear → an eraser/ban icon,
fullscreen → expand arrows. All already match the established `btn-icon` sizing. Keep the
`title`/`aria-label` attributes that are already present.

### 7.3 Hardcoded colors that ignore the theme system — **Low**

**Location.**
- `templates/terminals.html:2837-2842` — the settings-window button's SVG hardcodes
  `#06263a/#5eefff/#63f6ff/...`, which clash on the light theme.
- `templates/index.html:385-406` — `.browser-close-btn` uses literal `rgba(181,35,49,.2)`,
  `#ff8a94`, `#ff6b75` instead of `var(--danger)` and has no `[data-theme="light"]` variant.
- `templates/index.html:1996-1999` — tooltip arrow hardcodes `rgba(16,21,39,.98)` while the
  bubble uses `var(--bg-deep)`; a future token change desyncs the arrow from its bubble.
- `templates/terminals.html:6139-6144` — xterm theme colours are literals rather than being fed
  from the CSS custom properties (they can be read via `getComputedStyle` at `makeTerminal`
  time).

**Proposed implementation.** Swap literals for the corresponding tokens; for the settings SVG
use `stroke="currentColor"` + a `.settings-window-btn { color: var(--t-accent); }` rule; derive
the xterm theme from computed CSS variables so 7.1's unification automatically restyles the
terminal cursor/selection.

---

## 8. Button & Interaction Improvements

### 8.1 Closing a session tab has no confirmation and no undo — **Medium**

**Location:** `templates/terminals.html` — tab close button wired in `renderSessionTabs`
(:4578) → `closeSessionGroup` (:9676); backend `DELETE /api/sessions?group=…` terminates every
terminal in the group immediately.

**Problem.** One misclick on the small `×` of a tab kills up to 8 live terminals (running
agents, builds, SSH jobs) irrecoverably — live sessions are memory-only by design. Compare:
the browser-mode Close button *does* confirm.

**Proposed implementation.** Minimal: `if (!confirm(\`Close "\${group.name}" and its N
terminals?\`)) return;` — but `confirm()` has the same WebView2 problem as 4.4, so implement it
with the existing modal shell (a small "Close session?" dialog with Close/Cancel), or gate it
to only prompt when the group has ≥1 `connected` terminal. A "hold to close" affordance
(mousedown 600 ms with progress ring) is a good pointer-only alternative that needs no dialog.

### 8.2 Launch button gives weak in-flight feedback — **Low**

**Location:** `templates/index.html:5182-5240`

**Problem.** During launch the CTA only swaps text to "Launching..."; there is no spinner, and
the label bug (4.3) leaves the button mutated afterwards. The `icon-btn.loading` spin animation
already exists (`:820-822`, `tf-spin`) but is unused here.

**Proposed implementation.** Give `.action-btn` a loading state:
```html
<button id="launchBtn" class="action-btn" ...>
  <span class="action-btn-label">Launch Workspace</span>
  <span class="arrow">→</span>
  <svg class="action-btn-spinner" ...>…</svg>
</button>
```
```css
.action-btn.loading .arrow { display:none }
.action-btn.loading .action-btn-spinner { display:inline-block; animation: tf-spin .9s linear infinite; }
```
JS toggles `classList.add('loading')` instead of rewriting `textContent`, which also fixes 4.3
structurally.

### 8.3 Update status is rendered twice with identical text — **Low**

**Location:** `templates/index.html:3199-3212` (`setUpdateStatus` writes both `#updateStatus`
in the Session Source card header and `#quickUpdateStatus` next to the theme controls).

**Problem.** Every update/settings message appears in two places at once — visually noisy and
the two locations imply different scopes they don't have.

**Proposed implementation.** Keep only `#quickUpdateStatus` (it sits beside the buttons that
trigger updates/settings) and drop `#updateStatus` + the `.toolbar-status` markup; or keep the
toolbar one and drop the quick one. One-location messaging with a fade-out after ~6 s
(`setTimeout(() => status.textContent = '', 6000)`) is the cleanest.

### 8.4 Errored / disconnected panes offer no retry — **Medium** (also a feature)

**Location:** `templates/terminals.html:9416-9429` (`showPlaceholderError`); no
"reconnect/retry" string exists anywhere in the file.

**Problem.** When SSH auth fails or a shell exits, the pane shows a static error/disconnected
placeholder. The only recovery is closing and relaunching the whole group, losing the other
panes' state.

**Proposed implementation.**
1. Backend: add `POST /api/sessions/<session_id>/reconnect` — validates the session exists and
   is in `error`/`disconnected`, resets status to `PENDING`, clears the output buffer, and
   `socketio.start_background_task(_connect_session, session_id)`. (~20 lines; all pieces
   exist.)
2. Frontend: render a `Retry connection` button inside `showPlaceholderError` (and the
   disconnected state) that calls the endpoint and restores the "Connecting…" placeholder.
   The existing `session_status` socket flow then reattaches the terminal automatically.

### 8.5 Save-settings button shows two tooltips at once — **Low**

**Location:** `templates/index.html:2523-2524`

**Problem.** The button has both a native `title` attribute and the custom
`.button-tooltip-bubble`; hovering long enough shows the styled bubble *and* the OS tooltip
with the same sentence.

**Proposed implementation.** Remove the `title` attribute (the bubble is
`role="tooltip"` + `aria-describedby`, so accessibility is preserved).

---

## 9. Log Review (`logs/gridvibe.log`)

### 9.1 Normal Windows pane closures are logged as ERROR — **Medium**

**Evidence.** ~25 occurrences of
`ERROR web.api Error streaming local output for session …: [WinError 10053] An established
connection was aborted by the software in your host machine` — each corresponds to a local
pane being closed normally (WinPTY read aborts when the process is torn down).

**Problem.** Routine teardown fills the log with ERRORs, training users (and future log
analysis) to ignore the level. The code already special-cases explorer sessions this way
(`logger.debug(...)` when `_is_explorer_session`).

**Proposed implementation.** In the `except` blocks of `_stream_local_output` /
`_stream_ssh_output`, check whether the connection was already removed (i.e. an intentional
close) and downgrade:
```python
with connection_lock:
    intentional = session_id not in ssh_connections
if intentional or session is None or session.status == SessionStatus.DISCONNECTED:
    logger.debug("Stream ended for closed session %s: %s", session_id, e)
    return
logger.error(...)
```

> **✅ Implemented (2026-07-10).** Both `_stream_ssh_output` and `_stream_local_output` now
> check (under `connection_lock`) whether the connection was already removed from
> `ssh_connections`, or the session is gone / already `DISCONNECTED`, and log at DEBUG and
> skip the ERROR-status transition in that case. The existing explorer-session special case
> is preserved.

### 9.2 ANSI colour escape codes are written into the log file — **Low**

**Evidence.** Entries like `"[35m[1mPOST /api/app-update HTTP/1.1[0m" 500` — werkzeug's
coloured status codes are captured raw by the `RotatingFileHandler`.

**Proposed implementation.** In `setup_logging`, add a strip filter on the file handler only:
```python
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
class _StripAnsi(logging.Filter):
    def filter(self, record):
        record.msg = _ANSI_RE.sub("", str(record.msg)); return True
file_handler.addFilter(_StripAnsi())
```
(Console keeps colours; the file becomes grep-friendly.)

### 9.3 `/api/voice-status` polling escapes the poll-suppression filter — **Low**

**Evidence.** Dozens of `GET /api/voice-status` 200 lines; the terminals page refreshes it on
every window focus/pageshow.

**Proposed implementation.** Extend the regex in `main.py:33` to include it:
```python
_POLL_RE = re.compile(r'"GET /api/(sessions|session-groups|voice-status)(\?[^ ]*)? HTTP/[\d.]+" 2\d\d')
```
(If 3.4 lands, revisit whether the filter is needed at all.)

**Also noted:** the four `TypeError: _run_git_command() missing … 'cwd'` tracebacks from
2026-07-04 are already fixed in the current source (`perform_self_update` now uses
`_run_self_update_git_command`), but they motivate finding 6.6's rename + regression test.

---

## 10. New Feature Proposals

### 10.1 SSH keepalive (the config key already exists)

**Motivation.** Long-lived SSH panes behind NAT/firewalls drop after idle timeouts; users see
frozen terminals. `ssh.keepalive_interval: 60` already ships in `default_config.json` but is
never applied (5.1).

**Proposed implementation.** One line after connect in `_connect_ssh_session` (and optionally
`_open_ssh_sftp` for the pool from 3.1):
```python
keepalive = int(ssh_config.get("keepalive_interval", 60))
if keepalive > 0:
    client.get_transport().set_keepalive(keepalive)
```

> **✅ Implemented (2026-07-10).** `_connect_ssh_session` in `web/api.py` now applies
> `transport.set_keepalive(...)` right after a successful connect, reading
> `ssh.keepalive_interval` (default 60; `0` or negative disables; `None` transport guarded).
> Not applied to `_open_ssh_sftp` — those connections are short-lived until the SFTP pool
> (3.1) lands.

### 10.2 Wire `terminal.font_size` / `font_family` into xterm

**Motivation.** The config keys exist and users on high-DPI monitors regularly want a bigger
terminal font; today `makeTerminal()` hardcodes 13 px Consolas.

**Proposed implementation.**
1. Pass through the template: `render_template('terminals.html', ..., terminal_font_size=...,
   terminal_font_family=...)` from the `terminal` config section, exposed as data-attributes on
   `<body>` (same pattern as the voice settings).
2. `makeTerminal()` reads them:
   ```js
   fontSize: Number(document.body.dataset.terminalFontSize) || 13,
   fontFamily: document.body.dataset.terminalFontFamily || 'Consolas, ...',
   ```
3. Optional stretch: Ctrl+scroll / Ctrl+±  per-pane zoom (`term.options.fontSize = n; fit()`),
   mirroring the explorer's existing editor zoom controls (`stepExplorerEditorFontSize`).

### 10.3 Terminal search and clickable links (xterm addons)

**Motivation.** Panes routinely hold build output and agent logs; today there is no way to
search scrollback or click URLs. Both are official xterm addons matching the already-vendored
version, and the explorer pane already establishes the search-UI pattern
(`renderExplorerDirectorySearchControls`).

**Proposed implementation.**
1. Vendor `@xterm/addon-search@0.13` and `xterm-addon-web-links@0.9` (see 3.6).
2. In `makeTerminal()`: `term.loadAddon(new SearchAddon.SearchAddon())`,
   `term.loadAddon(new WebLinksAddon.WebLinksAddon((e, uri) => window.open(uri)))`.
3. Add a `Ctrl+Shift+F` (per focused pane) overlay input reusing the explorer search styling;
   Enter/Shift+Enter → `searchAddon.findNext/Previous(query)`.
The existing global-shortcut router (`isEditableShortcutTarget`, `findExplorerSearchTargetIndex`)
is the right place to hook the keybinding.

### 10.4 Broadcast input to all panes ("synchronized typing")

**Motivation.** The core multi-terminal workflow (same command on N hosts / N worktrees) is
what GridVibe's grid invites; every established multiplexer (tmux `synchronize-panes`,
iTerm2 broadcast) has this. It fits the existing architecture with almost no backend work —
`terminal_input` already targets one session; the client just sends N events.

**Proposed implementation (frontend-only).**
1. Topbar toggle button "Broadcast" (with a strong visual state — e.g. accent border around all
   panes while active, to prevent accidents).
2. In the `term.onData` handler, when broadcast is on and the pane is a plain terminal (skip
   explorer/browser panes), loop:
   ```js
   sessionIds.forEach((sid, i) => {
       if (i === index || !isPlainTerminal(i)) return;
       socket.emit('terminal_input', { session_id: sid, data });
   });
   ```
3. Auto-disable on group switch and after 10 min idle as a safety.

### 10.5 Session restore after backend restart

**Motivation.** Today a GridVibe restart (including the self-update restart flow!) drops all
groups; the user rebuilds their workspace by hand. Live shells can't survive, but the
*workspace shape* can.

**Proposed implementation.**
1. On every group create/close/reorder, persist a compact snapshot
   (`groups`, per-session config minus passwords — reuse the saved-session config shape that
   `buildActiveWorkspaceSessionConfig` already produces) to `runtime_state.json` (gitignored).
2. On startup, if the file exists and is < N hours old, the launcher shows a banner:
   "Previous workspace found — Restore 2 sessions?" → replays each snapshot through the
   existing `POST /api/sessions` launch path (passwords come from saved sessions or key auth).
3. Explicitly out of scope: restoring shell history/processes.
This dovetails with the existing `saved-session-<id>` stable group IDs, which were clearly
designed with relaunch-idempotency in mind.

### 10.6 Explorer file download (read-only compatible)

**Motivation.** The explorer is deliberately read-only for mutations, but *downloading* a file
is a read. Users inspecting a remote log or artifact currently can't save it.

**Proposed implementation.**
1. `GET /api/explorer/<session_id>/download?path=…` — resolves via the same
   `_resolve_explorer_file_path` / remote equivalent (root confinement preserved), streams with
   `Content-Disposition: attachment`, with a size cap (e.g. 100 MB) and no preview-format
   restriction (binaries allowed — it's a download, not a render).
2. Frontend: a download icon in the file-view toolbar next to the existing zoom controls, and
   in each row's hover actions.
3. Document in CLAUDE.md/README that the read-only contract covers *mutations*, and download is
   read-scope.

### 10.7 Strict host-key verification mode

**Motivation.** Completes 1.4 for security-conscious users without changing defaults.

**Proposed implementation.** Config key `ssh.host_key_policy: "auto-add" | "known-hosts" |
"strict"`:
- `auto-add` — today's behaviour (default).
- `known-hosts` — persist to `.known_hosts` as in 1.4; warn on change but proceed.
- `strict` — `paramiko.RejectPolicy()` after loading `.known_hosts` + the user's
  `~/.ssh/known_hosts`; connection errors surface through the existing error placeholder
  (which, with 8.4, gains a Retry button).
Settings UI: a select in App Settings under a new "SSH" section. Flag in README's security
posture paragraph.

---

## Suggested sequencing

1. **Quick wins (small, high value):** 1.3, 2.1, 2.7, 4.1, 4.2, 4.4, 4.5, 9.1, 10.1 — each is
   under ~30 lines. ✅ **All nine implemented 2026-07-10** (see the per-finding notes above).
2. **Security defaults:** 1.1 + 1.2 together (one PR, changelog entry). ✅ **Implemented
   2026-07-10**, together with 1.4's known_hosts persistence (see the per-finding notes above).
3. **Perf pass:** 3.1 (SFTP pool) then 3.2/3.4; 3.6 (vendor assets) can ship independently.
4. **Structural:** 6.1 (explorer backend) → 6.2 (module split) → 3.5/6.4 (template extraction),
   each incremental with `make check` green between steps.
5. **UX/features:** 8.4 + 10.x as individually scoped follow-ups.
