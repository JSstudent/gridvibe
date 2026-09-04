# Milestone A — security boundary and data exposure

**Source audit:** `docs/codebase_review_2026-09-04.md` (revision `f5f2303aac95`)
**Verified against:** `6299ddb` (`szua_gridvibe_agents-wrk`), 2026-09-04
**Findings:** GV-001, GV-002, GV-003, GV-007, GV-025, GV-027
**Status:** verified, not started

This document expands Milestone A into work packets. Every packet states its
invariants, production changes, compatibility story, test matrix, and exit
evidence, so the audit ID stays the traceable link from finding to
verification.

The audit is a point-in-time record and is **not** a contract. Nothing in the
codebase should cite it or this document. Rules that must outlive the work are
marked **→ guardrail** and belong in the Regression Guardrails list in
`CLAUDE.md` and `AGENTS.md` when the packet lands.

---

## 1. Verification record

Every Milestone A finding was re-checked against the working tree before
planning. Four reproduce as running code; two are confirmed by reading the one
code path that can produce them.

| ID | Claim | Method | Result |
|---|---|---|---|
| GV-001 | Request `Host` is trusted as the same-origin authority | Flask test client + `SameOriginPolicy` called directly | **Reproduced** |
| GV-002 | Non-loopback bind has no authentication gate | `main.py:117-156` read end to end | **Confirmed** |
| GV-003 | Decrypted SSH passwords reach ordinary GET routes | `GET /api/session-config` against a live test client | **Reproduced** |
| GV-007 | Socket events accept malformed payloads and unowned session IDs | Handlers invoked directly with non-mapping payloads | **Reproduced** |
| GV-025 | Startup commands and directories are logged verbatim | `web/terminal_io.py:1364-1369`, `1489` read | **Confirmed** |
| GV-027 | No response-wide header policy exists | `grep` for `after_request` / CSP across `web/*.py` | **Confirmed** |

### 1.1 What the probes returned

**GV-001.** With `Host: evil.example:5050` *and* `Origin: http://evil.example:5050`,
`POST /api/sessions` returned `400` from payload validation — the write guard
never fired. The same request with an honest `Host: 127.0.0.1:5050` and the
hostile Origin returned `403`. So the guard is not broken in general; it is
defeated specifically by letting the attacker supply the authority it compares
against. `SameOriginPolicy` accepted `http://evil.example:5050` when `HTTP_HOST`
carried the same value, and accepted `https://evil.example` when only
`X-Forwarded-Host` / `X-Forwarded-Proto` carried it — with no proxy configured
and no proxy trust setting in existence.

**GV-003.** `GET /api/session-config` returned `200` with `ssh` keys
`['default_dir', 'host', 'password', 'port', 'username']`. The password is
plaintext: `_normalize_stored_payload()` decrypts on load, and
`_saved_session_response(..., include_config=True)` passes the config through
unredacted.

**GV-007.** All four handlers (`join_session`, `clear_terminal_buffer`,
`terminal_input`, `terminal_resize`) raised `AttributeError` for every
non-mapping payload tried (`None`, `[]`, `"x"`, `5`) — 16 of 16 cases. Two
thousand `clear_terminal_buffer` calls with fabricated IDs created two thousand
buffer entries, because `_clear_terminal_output_buffer()` assigns rather than
clears an existing key.

### 1.2 Baseline to preserve

`tests.test_api.CorsOriginDefaultsTestCase` and
`tests.test_api.CrossOriginWriteGuardTestCase` — **16 tests, currently green.**
They must stay green. They pass today *and* the boundary is broken, because
every one of them supplies an honest `Host`; that gap is itself a finding about
the test surface and is closed by the matrix in §7.

---

## 2. Corrections and additions to the audit

Three points where planning changed the picture. None invalidates a finding.

### 2.1 GV-001 and GV-002 are one work item, not two

The audit lists them separately, and the recommended order fixes GV-001 first.
That order cannot be executed as written.

`SameOriginPolicy`'s request-host fallback is load-bearing for exactly one
supported configuration, and the code says so at `web/app.py:93-101`: with a
wildcard bind (`--host 0.0.0.0`) browsed by LAN IP, the derived origin list
contains no entry anybody uses, so removing the fallback kills the Socket.IO
transport for every remote pane. GV-001's fix removes that fallback. GV-002's
fix is what replaces it — a non-loopback bind becomes an explicit, configured
mode that *names* its reachable hosts, so the allowlist can be derived from
configuration instead of from the request.

**Consequence:** packets A1 and A2 ship together or the wildcard-bind case
regresses from "insecure but working" to "broken". They are sequenced as one
release unit below.

### 2.2 GV-003's fix has a client-side half the audit does not mention

The audit recommends keeping credentials server-side and merging by preset ID.
That is right, but `launcher.js` currently *renders* the saved password into
the `#ssh_password` form field (`launcher.js:1068` on preset load, `:1249` on
target pick) and reads it back out on launch (`:1040`, `:1739`, `:2134`).
Redacting the response without touching the client silently converts every
password-authenticated saved preset into a blank-password launch attempt.

**Consequence:** A3 is a two-sided packet with a tri-state contract, not a
response-shape change. Detailed in §5.3.

### 2.3 GV-027's strict CSP cannot ship in one step

The launcher and workspace pages carry **55 inline event-handler attributes**
(`onclick=`/`onchange=`/`oninput=`, counted across `templates/`) and two inline
`<script>` blocks. A nonce covers a `<script>` block; it does not cover an
attribute handler. A `script-src` strict enough to be worth having therefore
breaks the UI until those 55 handlers are migrated to `addEventListener`.

Separately, browser-preview panes frame arbitrary external URLs, so `frame-src`
must stay wide — a `default-src 'self'` written without that kills the feature.

**Consequence:** A5 enforces the directives that cost nothing today and ships
the strict policy as `Content-Security-Policy-Report-Only` alongside. The
handler migration is named as follow-on work, not smuggled into this milestone.

---

## 3. Invariants to preserve

Work in this milestone must not disturb:

- **Loopback stays frictionless.** The default `127.0.0.1` experience gains no
  login step, no token prompt, and no extra click. Every new refusal is
  conditional on a configuration the user had to opt into.
- **`security.cors_origins` keeps its meaning**, including the documented
  `["*"]` escape hatch and its startup warning.
- **The cross-origin write guard keeps its current verdicts** for every honest
  request. The 16 baseline tests encode those verdicts.
- **Room-scoped emits stay room-scoped.** Nothing here widens a broadcast.
- **`saved_sessions.json` on-disk format is unchanged.** Passwords stay
  Fernet-encrypted at rest with the existing key; A3 changes what leaves the
  process, not what is stored.
- **Lifecycle logging is already shape-only** (`CLAUDE.md`). A6 extends that
  existing rule to terminal connection logging rather than inventing a new one.
- **No new global JS.** Client changes in A3 go in `launcher.js`'s existing
  preset code or a new module — never `terminals.js`.

---

## 4. Sequencing

```
A1  origin/host authority ───┐
                             ├──► one release unit (§2.1)
A2  bind gate + token   ─────┘
                             └──► A4  socket schema + authorization

A3  credential redaction   (independent — ship first if splitting)
A5  response headers       (independent)
A6  log redaction          (independent)
```

A4 depends on A2 only for the handshake token check; its schema and membership
work is independent and may land first. A3, A5 and A6 have no dependencies on
each other or on A1/A2 and can proceed in parallel.

**Recommended landing order:** A3 → A6 → A5 → A1+A2 → A4. This front-loads the
packets with the highest data-exposure reduction per unit of regression risk,
and leaves the one packet that changes a supported configuration's behaviour
(A1+A2) for a point where the rest of the milestone is already green.

---

## 5. Work packets

### 5.1 A1 — one canonical origin and host authority (GV-001)

**Problem.** `_allowed_write_origin_netlocs()` inserts `request.host` into the
allowlist it is about to check the request against. `SameOriginPolicy` accepts
any origin equal to `_request_origin(environ)`, which is built from `HTTP_HOST`,
`HTTP_X_FORWARDED_HOST` and `HTTP_X_FORWARDED_PROTO`. The HTTP path compares
`netloc` only, so a configured origin's scheme is not part of the verdict.

**New module: `web/origins.py`.** The authority does not belong in `web/app.py`,
which is the app/socket factory; a separate module keeps the policy testable
without an app context and matches the architecture guardrail for new backend
code.

```
@dataclass(frozen=True)
class ServerOrigins:
    allowed_hosts: frozenset[str]     # host:port values valid in a Host header
    allowed_origins: frozenset[str]   # full scheme://host:port
    trusted_proxy: bool
    wildcard: bool                    # security.cors_origins == ["*"]

resolve_server_origins(host, port, config) -> ServerOrigins
install_server_origins(host, port) -> ServerOrigins   # publishes one generation
current_server_origins() -> ServerOrigins
```

Published by a **single reference swap** to one immutable object, and read
once per operation — the model `RuntimeConfig` already uses, for the same
reason (guardrail 2). A request must not derive its host verdict from one
generation and its origin verdict from the next.

**Changes.**

1. `web/app.py` gains `@app.before_request _reject_untrusted_host()`, ordered
   **before** `_reject_cross_origin_writes()`. It compares `request.host`
   (lowercased, IDNA-normalized, default port folded) against
   `allowed_hosts`. A miss is `403` with the same error shape the cross-origin
   guard already returns. This runs on **`GET` too** — GV-003's routes are
   `GET`, and DNS rebinding's whole point is reading a response.
2. `_allowed_write_origin_netlocs()` is deleted. `_reject_cross_origin_writes()`
   compares the full `scheme://host:port` of the Origin header against
   `allowed_origins`. Scheme becomes part of the verdict.
3. `SameOriginPolicy.__call__` drops the `_request_origin(environ)` fallback.
   With `trusted_proxy` off it consults `allowed_origins` only.
4. `_request_origin()` survives for the proxy case alone, and reads
   `X-Forwarded-*` **only** when `trusted_proxy` is set. → **guardrail:** a
   forwarded header is evidence only when a trusted proxy was declared; a
   direct client can always send one.
5. `apply_resolved_server_origins(host, port)` calls `install_server_origins()`
   so the CLI-flag-wins rule (guardrail 4) reaches the new authority by the
   same path it already reaches Socket.IO.

**New config keys**, all through `RuntimeConfig` (guardrail 5), under `security`:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `allowed_hosts` | list[str] | `[]` | Extra `host` / `host:port` values accepted in the Host header |
| `trusted_proxy` | bool | `false` | Honour `X-Forwarded-Host` / `X-Forwarded-Proto` |

Derived defaults when `allowed_hosts` is empty: `127.0.0.1:<port>`,
`localhost:<port>`, `[::1]:<port>`, plus the resolved bind host when it is not
in `_UNROUTABLE_BIND_HOSTS`. A wildcard bind contributes nothing on its own —
that is the case A2 answers.

**Compatibility.** `security.cors_origins` continues to win outright, `["*"]`
still disables both guards and still warns. Loopback users see no change:
`127.0.0.1:5050` and `localhost:5050` are both in the derived set, both
directions of the existing `.replace()` aliasing preserved.

**Risk.** A user who reaches GridVibe by a hostname not in the derived set — a
`hosts` file alias, a container name, an mDNS `.local` address — gets a `403`
where they previously got a page. This is the intended refusal, but it must be
**diagnosable**: the rejection logs the received Host and the allowed set at
WARNING, and the response body names `security.allowed_hosts` as the setting
that admits it. → **guardrail:** a refusal that a legitimate user can hit names
the setting that lifts it.

---

### 5.2 A2 — non-loopback requires explicit opt-in and a token (GV-002)

**Problem.** `--host 0.0.0.0` is accepted with no gate. The API creates shells
and mutates the filesystem, so this is remote command authority. Origin and
CORS checks are browser controls and do not apply to a non-browser client,
which chooses its own headers.

**Changes.**

1. **Bind gate.** `main.py` and `web/webview_launcher.py` gain
   `--allow-remote-access`; config gains `security.allow_remote_access`
   (bool, default `false`). `web/config.py` grows
   `resolve_security_posture(config, allow_remote_access=None)` beside
   `resolve_server_settings()`, so the explicit-flag-beats-config rule is
   applied by the same owner. A non-loopback resolved host without the opt-in
   **refuses to start**: one message naming the host, the flag and the config
   key, then `sys.exit(2)`. No server is created — a warning that leaves the
   port open is not a gate.
2. **Token.** In remote mode a token is required on both transports. Resolved
   in order: `GRIDVIBE_AUTH_TOKEN` env → `security.auth_token` → generated
   `secrets.token_urlsafe(32)`. A generated token is printed to the console
   once at startup, never to `logs/gridvibe.log`. → **guardrail:** a
   capability token is a credential; it never reaches the rotating log.
3. **HTTP.** `_require_authenticated()` runs in `before_request` after the host
   check. It accepts `Authorization: Bearer <token>` or the
   `gridvibe_auth` cookie. `GET /auth?token=…` validates with
   `hmac.compare_digest`, sets the cookie `HttpOnly; SameSite=Strict;
   Path=/` (and `Secure` when the request is HTTPS), then redirects to `/`
   without the token in the URL. Loopback mode installs no check at all, so
   the default path adds not one comparison.
4. **Socket.IO.** The `connect` handler validates the same cookie/auth payload
   and returns `False` to refuse the connection before any room is joined.
5. **Startup posture line.** One log line and one `/api/app-config` field
   report the effective posture (`loopback` / `remote-token`), so the UI can
   show it. → this is the audit's "display the effective security state"
   recommendation, reduced to its testable core.

**Interaction with A1.** In remote mode the resolved bind host joins
`allowed_hosts`, and `security.allowed_hosts` is how an operator adds the
public names they actually browse. That is the replacement for the deleted
request-host fallback (§2.1).

**Explicitly out of scope:** TLS termination, user accounts, per-user
authorization, and token rotation. Milestone A delivers *one shared capability
token*, which is what makes the boundary honest; it is not a multi-user model
and the startup line must not imply it is.

---

### 5.3 A3 — saved passwords stop leaving the process (GV-003)

**Problem.** `_normalize_stored_payload()` decrypts into the in-memory config;
`GET /api/session-config` and `GET /api/saved-sessions/<id>` return that config
whole. Any same-origin script, rebinding bypass, page-scoped extension, or
future XSS reads reusable SSH credentials.

**Server changes.**

1. `_saved_session_response()` never emits `config.ssh.password`. It emits
   `config.ssh.has_password` (bool) instead — the fact the UI actually needs.
2. `load_session_config()` applies the same redaction. → **guardrail:** the
   redaction lives in the two response builders, not at each call site; a new
   route that returns a preset config is redacted by construction.
3. New `resolve_launch_password(saved_session_id, supplied) -> str` in
   `web/saved_sessions.py`, called inside the launch transaction. **Tri-state**,
   the convention `web/session_shell.py` already establishes for a payload
   dimension that may be unstated:

   | `supplied` | Meaning | Result |
   |---|---|---|
   | absent / `None` | not stated | stored password for `saved_session_id`, else `""` |
   | `""` | stated empty | `""` — key auth, an explicit clear |
   | non-empty string | stated | the supplied value |

   An unstated dimension is left alone; only a stated `""` clears one. That is
   the same sentence `session_shell.py` lives by, and reusing it means one
   rule to learn rather than two.
4. The launch path merges the secret server-side from `saved_session_id`, which
   already travels with the payload (`web/workspaces.py:545`) — no new field.

**Client changes** (`launcher.js`, the preset/target code; no new global):

5. `#ssh_password` is never populated from a response. When
   `has_password` is true the field renders empty with placeholder
   *"Saved password in use — type to replace"* and a `data-saved-password="1"`
   marker.
6. A `dirty` flag set on first `input`. On submit: not dirty and marker set →
   omit `password` entirely (the unstated case). Dirty → send the field's
   literal value, `""` included (the stated cases).
7. The reveal button (`#show_ssh_password`) can no longer reveal a stored
   secret, because the page no longer has one. It keeps working for a password
   the user is typing now. **This is a deliberate feature removal** and belongs
   in `CHANGELOG.md`. If reveal-a-saved-password must return, it comes back as
   a separate confirmed short-lived action, never as a field prefill.

**Compatibility.** On-disk format unchanged; no migration. A preset saved
before this change launches correctly because the server resolves its own
stored secret.

---

### 5.4 A4 — socket events are validated and authorized (GV-007)

**Problem.** Handlers call `data.get(...)` on whatever arrives (all four crash
on a non-mapping payload), join rooms for sessions that do not exist, create
buffers for arbitrary IDs, and let any socket write to any live connection
without ever having joined it.

**New module: `web/socket_events.py`** — one validator every handler passes
through, so a new event cannot quietly skip it.

```
@validated_event(schema, require_session=..., require_membership=...)
```

- **Shape.** Non-mapping payload → acknowledged error, never an exception. →
  **guardrail:** a socket handler never indexes an unvalidated payload; the
  decorator is the only place a payload becomes a mapping.
- **Bounds.** `session_id` must be a string, `1..128` chars, matching the ID
  charset. `data`/audio chunks get explicit byte ceilings.
- **Existence.** `join_session` joins **no room** when no such session exists.
- **Membership.** `terminal_input`, `terminal_resize`, `clear_terminal_buffer`
  require that this socket joined that session. → **guardrail:** the room is
  the authorization unit; a socket may act only on a session it joined.
- **Caps.** `_MAX_ROOMS_PER_CLIENT` beside the existing
  `_MAX_TRACKED_SOCKET_CLIENTS`.
- **Errors.** No raw exception text is written into terminal output. The
  handler acknowledges an error category; the detail goes to the log.

**One separate fix in `web/terminal_io.py`:**
`_clear_terminal_output_buffer()` currently *assigns*
`session_output_buffers[session_id] = _OutputBuffer()`, which creates an entry
for any ID (2 000 fabricated IDs → 2 000 entries). It must clear an existing
buffer and create nothing.

**Note on the existing client cap.** When `client_joined_sessions` exceeds
`_MAX_TRACKED_SOCKET_CLIENTS`, the code evicts `next(iter(...))` — the
oldest-inserted client, which may be a live window whose replay tracking then
silently resets. Not a Milestone A finding and not fixed here; recorded so it
is not mistaken for intended behaviour by whoever touches this next.

---

### 5.5 A5 — one response-header policy (GV-027)

**Problem.** Only the inline-image route sets headers (`web/api.py:1664-1665`).
No `after_request` hook exists.

**Changes.** `@app.after_request _apply_security_headers()` in `web/app.py`.

**Enforced immediately** (nothing in the app relies on their absence):

| Header | Value |
|---|---|
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Content-Security-Policy` | `default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self' ws: wss:; frame-src *` |

Two directives are deliberately wide and must stay that way until something
changes: `frame-src *` because browser-preview panes frame arbitrary URLs, and
`script-src 'unsafe-inline'` because of the 55 inline handlers (§2.3). Written
out rather than omitted, so the policy states what it does **not** yet protect.

**Shipped alongside:** `Content-Security-Policy-Report-Only` carrying the
target policy (`script-src 'self'`, no `'unsafe-inline'`). Report-Only is how
the `connect-src`/WebSocket and vendored-asset assumptions get *verified in a
real browser* rather than asserted here.

**Two rules the hook must obey:**

- A response that already carries a CSP keeps it. The image route's
  `default-src 'none'; … sandbox` is stricter than the page policy and must not
  be widened by a blanket overwrite. → **guardrail:** the global header hook
  fills gaps; it never relaxes a header a route set deliberately.
- Static assets get `nosniff` and no page CSP.

**Follow-on, named not hidden:** migrating 55 inline handlers to
`addEventListener` so `'unsafe-inline'` can be dropped. That is the work that
makes this header worth having against injection, and it is not in Milestone A.

---

### 5.6 A6 — operational logs become shape-only (GV-025)

**Problem.** `web/terminal_io.py:1364-1369` logs
`dir={session.directory} cmd={session.initial_command!r}` at INFO for every SSH
connect; line 1489 logs the fully composed local shell command. Startup
commands routinely carry env assignments, bearer tokens and credential-bearing
URLs, and these land in a rotating file.

**Changes.**

1. Both lines become shape-only: session ID, connection family, WSL/PowerShell
   booleans, whether a directory and an initial command are *present*, and
   their lengths. Never the values. This is the existing lifecycle logging rule
   (`CLAUDE.md`) applied to terminal connection logging. → **guardrail.**
2. New `security.log_commands` (bool, default `false`), through
   `RuntimeConfig`. When enabled, the full command is logged at **DEBUG** and
   startup emits a conspicuous warning naming the retention risk — the pattern
   `security.cors_origins: ["*"]` already uses.
3. An audit pass over launch summaries and transition logging in the workspace
   and session modules for the same leak; each hit either redacted or, when it
   is already shape-only, left alone.

**Not changed.** Host and username in the SSH connect line stay: they are the
connection's identity, they are what makes a failure diagnosable, and they are
not reusable secrets. The audit groups them with commands; that is one step
further than the evidence supports, and removing them would leave SSH failures
undiagnosable from the log.

---

## 6. Guardrails to add on landing

Each packet contributes rules that must outlive this document. Add to the
Regression Guardrails list in **both** `CLAUDE.md` and `AGENTS.md`:

1. **The request never supplies the authority it is checked against.** `Host`
   is validated against a set derived from resolved server settings; it is
   never added to its own allowlist. Forwarded headers are evidence only when
   `security.trusted_proxy` is set. Origin comparison is full
   scheme+host+port. (A1)
2. **A non-loopback bind is an explicit mode with a capability token**, and a
   token never reaches `logs/gridvibe.log`. (A2)
3. **A saved secret is redacted in the response builder, not at the call
   site**, and reaches a launch by server-side merge from the preset ID. A
   payload dimension that may be unstated is tri-state: unstated leaves it
   alone, a stated empty value clears it. (A3)
4. **A socket handler never indexes an unvalidated payload**, and the room is
   the authorization unit — a socket may act only on a session it joined. (A4)
5. **The global header hook fills gaps and never relaxes a header a route set
   deliberately.** (A5)
6. **Operational logs are shape-only** — ids, family, counts, categories,
   presence and length; never commands, directories, or payloads. (A6)

---

## 7. Test matrix

New file **`tests/test_security_boundary.py`** for A1/A2/A5; A3 and A4 extend
`tests/test_api.py` beside the code they cover; A2's startup gate extends
`tests/test_main.py`.

Behavioural tests throughout — these are Python routes and handlers, so they
are exercised by calling them, never by asserting on source text.

### A1 — host and origin matrix

| # | Case | Expect |
|---|---|---|
| 1 | `Host: evil.example:5050` + matching Origin, `POST` | `403` |
| 2 | Same, `GET /api/session-config` | `403` — the rebinding read is the point |
| 3 | Honest Host, hostile Origin | `403` (baseline preserved) |
| 4 | Honest Host, no Origin (pywebview, non-CORS) | passes |
| 5 | `127.0.0.1:5050` and `localhost:5050` both directions | pass |
| 6 | IPv6 `[::1]:5050`, bracketed and with default port folded | pass |
| 7 | Scheme mismatch: configured `https://proxy.example`, request `http://` | `403` |
| 8 | `X-Forwarded-Host: evil.example`, `trusted_proxy` off | ignored → `403` |
| 9 | Same, `trusted_proxy` on with the origin configured | passes |
| 10 | `cors_origins: ["*"]` | both guards off, warning emitted |
| 11 | `SameOriginPolicy` with hostile `HTTP_HOST` | `False` |
| 12 | `SameOriginPolicy` with forwarded spoof, proxy off | `False` |
| 13 | Host rejection log names received Host and allowed set | asserted |
| 14 | Two generations installed mid-flight | one operation reads one generation |

### A2 — bind gate and token

| # | Case | Expect |
|---|---|---|
| 15 | `--host 0.0.0.0` without opt-in | `SystemExit(2)`, no server created |
| 16 | Same with `--allow-remote-access` | starts, posture `remote-token` |
| 17 | Config `allow_remote_access: true`, no flag | starts |
| 18 | Flag beats config in both directions | resolved value wins |
| 19 | Remote mode, no token | HTTP `401`, socket `connect` refused |
| 20 | Remote mode, wrong token | `401`, constant-time compare |
| 21 | Remote mode, valid bearer / valid cookie | pass |
| 22 | `GET /auth?token=…` | sets HttpOnly cookie, redirects without the token |
| 23 | Loopback mode | no token required, no check installed |
| 24 | Generated token | printed to console, absent from the log file |

### A3 — no secret leaves the process

| # | Case | Expect |
|---|---|---|
| 25 | `GET /api/session-config` with a password-bearing preset | no `password` key; `has_password: true` |
| 26 | `GET /api/saved-sessions/<id>` | same |
| 27 | `GET /api/saved-sessions` (list) | same (regression cover) |
| 28 | `GET /api/session-targets` | secret-free (baseline preserved) |
| 29 | **Sweep:** every registered `GET` route reachable without arguments, recursive scan of the JSON body for the stored password value | no hit |
| 30 | Socket emit sweep over a scripted session lifecycle | no hit |
| 31 | Launch, `password` absent, preset has one | stored secret used |
| 32 | Launch, `password: ""` | empty — explicit clear |
| 33 | Launch, `password: "typed"` | `"typed"` |
| 34 | Launch, no `saved_session_id`, no password | `""`, no lookup |
| 35 | Launch naming a deleted preset | `""`, refused cleanly, not a crash |
| 36 | Round trip: save with password → reload → launch | connects |

Cases 29 and 30 are the ones that keep this fixed. They scan for the *value*,
so a future route returning the config by a new path fails them without anyone
remembering to add a case. → this is the audit's "response-shape scan".

### A4 — socket validation and authorization

| # | Case | Expect |
|---|---|---|
| 37 | Every handler × `{None, [], "x", 5, nested dict}` | error ack, no exception |
| 38 | `session_id` non-string / empty / 10 000 chars | refused |
| 39 | `join_session` for a nonexistent session | no room joined |
| 40 | `clear_terminal_buffer` for a nonexistent session | no buffer created |
| 41 | 2 000 fabricated `clear_terminal_buffer` calls | registry size unchanged |
| 42 | Client B sends `terminal_input` for A's session, B never joined | refused |
| 43 | Same for `terminal_resize`, `clear_terminal_buffer` | refused |
| 44 | Client joins more than `_MAX_ROOMS_PER_CLIENT` | capped |
| 45 | Oversized `terminal_input` payload | refused at the ceiling |
| 46 | Handler raises internally | no raw exception text in `terminal_output` |
| 47 | Normal single-client join → input → resize → leave | unchanged behaviour |

### A5 — headers

| # | Case | Expect |
|---|---|---|
| 48 | `GET /` and the workspace page | all five headers present |
| 49 | CSP contains `frame-ancestors 'self'`, `object-src 'none'`, `base-uri 'self'` | asserted |
| 50 | `frame-src` admits `http:`/`https:` | asserted — pins the browser pane |
| 51 | Inline-image route | keeps `default-src 'none'; … sandbox`, not widened |
| 52 | Static asset | `nosniff`, no page CSP |
| 53 | Report-Only present and stricter than enforced | asserted |
| 54 | JSON API response | `nosniff` |

### A6 — logs

| # | Case | Expect |
|---|---|---|
| 55 | SSH connect with `initial_command` carrying a token | command absent from records |
| 56 | Local/WSL connect | composed command absent |
| 57 | Both | session ID and family present — still diagnosable |
| 58 | `log_commands: true` | command at DEBUG only, startup warning emitted |
| 59 | `log_commands` default | `false` |
| 60 | Directory values | absent; presence and length only |

### Regression

- `CorsOriginDefaultsTestCase` + `CrossOriginWriteGuardTestCase` — 16 tests,
  unmodified, green.
- Full runner green on Windows. GV-009's two known Windows failures
  (`test_repo_git_timeout_bounds_a_remote_that_goes_quiet`,
  `test_a_stalled_remote_returns_the_worker_thread_within_the_bound`) belong to
  Milestone C and are **not** a Milestone A gate; they must be no worse.

---

## 8. Exit gate

Milestone A is complete when every line is checked with evidence attached.

**Boundary**
- [ ] Hostile `Host` + matching `Origin` rejected for HTTP `GET` and `POST` (cases 1, 2)
- [ ] Hostile origin rejected by Socket.IO with and without forwarded headers (11, 12)
- [ ] Forwarded headers ignored unless `trusted_proxy`; honoured when set (8, 9)
- [ ] Scheme is part of the origin verdict (7)
- [ ] IPv4, IPv6, default-port and localhost/127.0.0.1 aliasing all pass (5, 6)
- [ ] `cors_origins: ["*"]` behaves as documented and still warns (10)

**Network exposure**
- [ ] Non-loopback bind without opt-in refuses to start (15)
- [ ] Remote mode refuses unauthenticated HTTP and Socket.IO (19)
- [ ] Loopback default gains no authentication step (23)
- [ ] Generated token never appears in `logs/gridvibe.log` (24)

**Secrets**
- [ ] No `GET` response contains a stored password value (29)
- [ ] No Socket.IO payload contains one (30)
- [ ] Password-bearing presets still launch, all three tri-state cases (31–33, 36)
- [ ] `CHANGELOG.md` records the reveal-a-saved-password removal

**Socket events**
- [ ] No handler raises on any malformed payload (37)
- [ ] Cross-client input/resize/clear refused (42, 43)
- [ ] No unbounded registry growth from fabricated IDs (41)
- [ ] Per-client room cap enforced (44)

**Headers and logs**
- [ ] Five headers on both pages; route-set CSP not widened (48, 51)
- [ ] Report-Only strict policy shipped, and its browser-observed violations
      recorded for the follow-on handler migration (53)
- [ ] No raw startup command or directory in the log at default settings (55, 56, 60)

**Suite**
- [ ] 16 baseline origin tests unmodified and green
- [ ] `make check` green on Windows; Milestone C's two known failures no worse
- [ ] Guardrails from §6 written into `CLAUDE.md` and `AGENTS.md`

**Evidence to attach:** the runner's summary line, the case numbers above
mapped to test names, and the console transcript of case 15 (startup refusal)
and case 29 (secret sweep).

---

## 9. Rollback

Each packet is independently revertable; the sequencing in §4 is what keeps
that true.

- **A1+A2** revert together (§2.1). Reverting either alone leaves the wildcard
  bind broken. Kill switch short of a revert: `security.cors_origins` set to an
  explicit list restores verbatim-honoured origins, and `["*"]` restores
  today's fully-open behaviour.
- **A3** reverts server-side and client-side together; the on-disk format never
  changed, so no data migration is involved in either direction.
- **A4** reverts as one module plus the handler decorators.
- **A5** is one `after_request` hook — deleting it restores today's behaviour
  exactly.
- **A6** is log-line edits plus one config key.

**Field kill switches** (no redeploy): `security.cors_origins`,
`security.trusted_proxy`, `security.allowed_hosts`, `security.log_commands`.

---

## 10. Out of scope

Named so they are not silently absorbed:

- TLS, user accounts, per-user authorization, token rotation (A2 ships one
  shared capability token, deliberately).
- Migrating 55 inline event handlers so `'unsafe-inline'` can be dropped (A5's
  named follow-on — the work that makes the CSP meaningful against injection).
- OS credential-vault storage (audit §10; A3 stops the leak, it does not change
  where the secret lives).
- Serving browser-preview content from a separate origin (audit GV-027's second
  recommendation; a real isolation boundary, and its own project).
- The `_MAX_TRACKED_SOCKET_CLIENTS` oldest-client eviction (§5.4 note).
- Everything in Milestones B–E.
