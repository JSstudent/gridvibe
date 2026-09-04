# Milestone A — security boundary and data exposure

**Source audit:** `docs/codebase_review_2026-09-04.md` (revision `f5f2303aac95`)
**Verified against:** `6299ddb` (`szua_gridvibe_agents-wrk`), 2026-09-04
**Reviewed against:** `fec2214`, 2026-09-04 — ten review findings, all upheld (§1.3, §2)
**Findings:** GV-001, GV-002, GV-003, GV-007, GV-025, GV-027
**Status:** verified, reviewed, not started

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

The origin/CORS cover is **three** classes in `tests/test_api.py`, not two —
**25 tests, currently green**:

| Class | Tests | Bears on |
|---|---|---|
| `CorsOriginDefaultsTestCase` | 6 | Derived origin list |
| `ResolvedServerOriginsTestCase` | 10 | Resolved bind settings, wildcard bind, proxy |
| `CrossOriginWriteGuardTestCase` | 10 | The write guard's verdicts |

The first draft of this plan named only the first and third (16 tests) and
promised them **unmodified**. Neither half of that survives review: the middle
class is the one that most directly encodes what A1 changes, and the promise of
literal immutability is unkeepable. See §2.10 — what is preserved is the
*behavioural verdict*, not the test source.

They pass today *and* the boundary is broken, because every one of them
supplies an honest `Host` (or lets the test client supply its implicit one).
That gap is itself a finding about the test surface and is closed by the matrix
in §7.

### 1.3 Second-pass verification (review)

A review of the first draft raised ten findings. Each was re-checked against
`fec2214`. **All ten are upheld**; two needed a correction to their supporting
detail, recorded here rather than quietly fixed, because both corrections change
what the fix has to do.

| # | Review finding | Method | Result |
|---|---|---|---|
| R1 | A3's `resolve_launch_password(preset_id, supplied)` regresses five SSH paths | Five call sites read; `upsert_saved_session` read | **Upheld** — §2.4 |
| R2 | A Flask `before_request` never sees the Engine.IO handshake | Probe hook installed, `/socket.io/` requested | **Upheld, empirically** — §2.2 |
| R3 | `trusted_proxy: bool` is not a trust boundary | Design read | **Upheld** — §2.5 |
| R4 | `/auth?token=…` puts the capability in access logs | `main.py` log filter read | **Upheld** — §2.6 |
| R5 | `--config` never reaches `runtime_config` — split-brain security state | `main.py:24`, `web/config.py:478` read | **Upheld** — §2.3 |
| R6 | Host allowlist semantics are contradictory in five places | Draft read against `web/app.py` | **Upheld** — §2.7 |
| R7 | A4 covers four of eleven socket events; the client cap cannot stay out of scope | All `@socketio.on` handlers read | **Upheld and widened** — §2.8 |
| R8 | `img-src 'self' data: blob:` breaks Markdown previews | Renderer executed on both syntaxes | **Upheld, example corrected** — §2.9 |
| R9 | "16 tests unmodified" is unrealistic | Three test classes read | **Upheld and widened** — §2.10 |
| R10 | Remote-token mode must be described as trusted-network-only | Design read | **Upheld** — §2.11 |

**R2 was settled by running it, not by reading it.** `web.app.app.wsgi_app` is
`flask_socketio._SocketIOMiddleware`. With a probe `before_request` installed,
`GET /socket.io/?EIO=4&transport=polling` carrying
`Host: evil.example:5050` and a matching Origin returned `400` from engine.io's
own origin check and the probe recorded **no** invocation; the next request to
an ordinary route recorded one. The hook is bypassed for exactly the transport
that most needs the check.

**R8's example is wrong and its finding is right.** The review illustrated the
break with raw `<img src="https://…">` HTML, which the renderer **escapes** —
that tag does not survive today. What does survive is ordinary Markdown image
syntax:

```
_render_markdown_preview('![x](https://example.com/a.png)')
  -> <p><img alt="x" src="https://example.com/a.png"></p>
_render_markdown_preview('<img src="https://example.com/b.png">')
  -> <p>&lt;img src="https://example.com/b.png"&gt;</p>
```

`img` is in `MARKDOWN_ALLOWED_TAGS`, `src` in `MARKDOWN_ALLOWED_ATTRIBUTES`,
and the sanitizer runs with `bleach.sanitizer.ALLOWED_PROTOCOLS`
(`http`, `https`, `mailto`). So the external image reaches the page by the
Markdown path, and the proposed `img-src` would break it. The correction
matters because it names the surface the regression test must exercise.

**R9 was widened by the check.** The review said several tests rely on the test
client's implicit portless `Host`. They do — and the count was also wrong in the
draft's favour: a third class of 10 tests exists that the draft never named,
two of whose tests (`test_wildcard_bind_authorises_the_host_the_request_arrived_on`,
`test_same_origin_policy_honours_a_reverse_proxy`) assert **precisely** the two
behaviours A1 removes. Those two are not preserved-but-rewritten; they are
inverted, and §2.10 says so.

**R7 was widened by the check.** The review named `leave_session` and the three
voice events. Reading every `@socketio.on` handler found eleven, and one more
unguarded payload the review did not list: `lifecycle_flush_ack` hands `data`
straight to the coordinator. `join_workspace` / `leave_workspace` already guard
their shape with `isinstance(data, dict)` and need membership and caps only.

---

## 2. Corrections and additions

Points where planning, and then review, changed the picture. None invalidates a
finding; several change what the fix has to be.

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

### 2.2 The Host check cannot live in a Flask hook *(blocking)*

`app.wsgi_app` is `flask_socketio._SocketIOMiddleware`. It answers `/socket.io`
itself and never enters Flask's routing, so `@app.before_request` does not run
for the Engine.IO handshake — proven, not inferred (§1.3). A Host check
installed there leaves the one transport that carries terminal input protected
by nothing but the origin policy A1 is simultaneously rewriting.

**Consequence:** the canonical-authority check is a **WSGI middleware wrapping
both**, installed outside `_SocketIOMiddleware`, so one implementation covers
Flask routes and the handshake alike. The Socket.IO `connect` handler
additionally re-checks, because a WebSocket upgrade that skipped the polling
handshake must not inherit an unchecked authority. Two layers, one predicate —
never two predicates. → **guardrail:** an authority check that must cover the
Socket.IO transport is WSGI-level; a Flask `before_request` does not see it.

### 2.3 The security posture is resolved once, at the entry point *(blocking)*

`main.py:24` imports `web.api` — and through it `web.app`, the Flask app and
`runtime_config` — **before** `--config` is parsed at line 132.
`load_config(args.config)` then feeds `resolve_server_settings()` only;
`runtime_config.refresh()` calls `load_config()` with no argument and reads the
default `CONFIG_PATH`. So with `--config other.json` the entry point can gate
startup on one file while `web.app` reads Host and token settings from another.

The general fix for that import order is GV-017 and is reasonably deferred. The
**security boundary cannot be split-brain in the meantime**: a gate and the
checks it authorises must come from one generation of one file.

**Consequence:** A1/A2 resolve one immutable security state **in the entry
point**, from the config file that was actually named, and *pass* it in
(`install_server_origins(...)`, `install_security_posture(...)`). Nothing in the
request path re-reads a config file to answer an authority question.
→ **guardrail:** the security posture is resolved once by the entry point and
published by reference; no request-path code re-derives it from disk.

### 2.4 GV-003's fix is a credential-reference contract, not a preset-ID lookup

The audit recommends keeping credentials server-side and merging by preset ID.
The direction is right and the mechanism is not:
`resolve_launch_password(saved_session_id, supplied)` regresses five paths, each
verified.

1. **The target picker deliberately drops the preset ID while borrowing its
   password.** `applyConnectionTarget()` calls `setActiveSavedSession(null)` —
   with a comment saying why: this must stay a *scratch* launch — and then
   fetches that preset's password into the field (`launcher.js:1216`, `:1239`).
   There is no `saved_session_id` on the launch to merge from, so a preset-ID
   lookup loses password authentication here.
2. **Both launch builders collapse `""` to `null`** (`launcher.js:3600`,
   `terminals.js:1833`: `password: config.ssh.password || null`). A tri-state
   contract cannot be expressed through a builder that erases the distinction
   between "stated empty" and "unstated" before the request is sent.
3. **Re-saving an imported preset would wipe its stored password.**
   `upsert_saved_session()` assigns `entry["config"] = normalized_config`
   wholesale. Import populates the form from the response; with the password
   redacted the field is blank, and `saveCurrentConfig()` posts
   `collectFormConfig()` — the whole form — so an untouched blank field
   overwrites the secret. Save As (a new name → a new preset) loses it the same
   way.
4. **A preset ID alone can send the old secret to a newly edited target.** The
   codebase already refuses to do that elsewhere: `_preset_ssh_credential()`
   (`web/workspaces.py:1050`) binds a password to host, username *and* port, and
   its docstring states the rule — "a password is only meaningful together with
   the host, username, and port it authenticates against". A3 must use the same
   rule rather than inventing a weaker one beside it.
5. **Agent preflight needs the credential too.** `_build_agent_preflight_request()`
   (`web/agents.py:855`) and `_resolve_preflight_environment()` (`:498`) both
   take `password` from the client's form payload. Redact the response without
   changing preflight and every password-only host reports a failed check.

Three further exposures the draft's response-shape change would have missed:

6. **The workspace page would emit a false warning.** `terminals.js:1946-1948`
   raises "No password is saved for this session" by inspecting
   `payload.sessions.some(session => session.password)` — a redacted payload
   makes that warning fire on every password-authenticated launch.
7. **Redaction in "the two response builders" is not enough.**
   `DELETE /api/saved-sessions` returns `"config": last_entry["config"]` inline,
   outside `_saved_session_response()` (`web/api.py:2771`) — and the launcher
   *consumes* it (`applySessionConfig(data.config)`), so this is a live prefill
   path, not only a leak. `POST /api/session-config` returns
   `load_session_config()` (`:2577`). The sweep must cover **every response
   method, not just GET**.
8. **A DOM `data-*` marker cannot hold the state.** `renderModeFields()`
   (`launcher.js:956-964`) replaces `container.innerHTML`, destroying the
   `#ssh_password` element and any attribute on it, then restores *values* via
   `applyModeInputs(previous)`. The dirty/reference state must live in JS state.

**Consequence:** A3 is a two-sided packet built on a **credential reference**
bound to a target, applied at five call sites. Detailed in §5.3.

### 2.5 `trusted_proxy` as a bool is not a trust boundary

A boolean says forwarded headers may be believed; it does not say *by whom*.
With it on, any client reaching the backend directly still supplies
`X-Forwarded-Host` and `X-Forwarded-Proto` and is believed — the same defect
GV-001 describes, re-admitted by a setting whose name promises otherwise.

**Consequence:** the setting is `security.trusted_proxies` — a list of
addresses/CIDRs verified against `request.remote_addr`, with an explicit hop
count — or it does not ship in Milestone A and proxy deployments configure
`security.allowed_hosts` and `security.cors_origins` explicitly. **Recommended:
ship the list.** A bool is the shape that invites the mistake.
→ **guardrail:** a forwarded header is evidence only when the *peer* is a
declared trusted proxy; a setting that trusts the header without checking who
sent it is not a trust boundary.

### 2.6 The token bootstrap must not travel in a URL

`GET /auth?token=…` puts the capability in the Werkzeug access log (`main.py`'s
filter suppresses only 2xx polling GETs), in browser history, in any proxy log,
and in the browser-launch process command line. That directly contradicts A2's
own guardrail that a token never reaches the rotating log.

**Consequence:** the bootstrap is `GET /auth#token=…` — a fragment never sent to
the server — served by a minimal page that POSTs the fragment value, or a
password-style POST form. Neither can be logged.

**Native mode needs explicit handling, and does not get it for free.** Three
call sites bypass the browser's cookie jar entirely:

- `_wait_for_server()` makes an unauthenticated health request
  (`web/webview_launcher.py:1643`).
- The download bridge (`:1094`) and the upload bridge (`:1223-1232`) issue bare
  `urlopen()` calls with no cookies.
- Both WebViews need an authenticated cookie bootstrap before their first load.

This bites only when native mode runs with a remote bind, but it bites
completely: the window opens on a server that refuses it. A2 either
authenticates all three or states that native mode is loopback-only — and it
must state which.

### 2.7 Host allowlist semantics need one consistent rule

The draft left five contradictions, all upheld:

1. `allowed_hosts` was described as "**extra**" values while the derived
   defaults were stated to apply "**when `allowed_hosts` is empty**". Those are
   different settings. It is additive: derived defaults **always** apply and
   `allowed_hosts` adds to them.
2. **A configured `cors_origins` entry did not become an accepted Host.** A
   working reverse-proxy deployment today sets
   `cors_origins: ["https://gridvibe.example"]` and nothing else; under the
   draft its `Host: gridvibe.example` is unlisted and every request 403s. The
   netloc of every configured origin joins `allowed_hosts`.
3. **A wildcard bind with the remote opt-in but no named authority would start
   and be unreachable.** §5.1 said a wildcard bind contributes nothing to the
   derived set, §5.2 said the resolved bind host joins it — and `0.0.0.0` is in
   `_UNROUTABLE_BIND_HOSTS`, so it contributes nothing. Startup succeeds and
   every LAN browse 403s. A wildcard bind must **require** at least one explicit
   reachable authority (`allowed_hosts` or a concrete configured origin) and
   refuse to start without one, exactly as the missing opt-in does.
4. **`cors_origins: ["*"]` must not disable Host validation.** It is a CORS
   escape hatch; DNS rebinding is a different attack and the wildcard says
   nothing about which authorities are legitimate. `["*"]` keeps its existing
   meaning for the origin checks and stops there.
5. **The generation must be captured once per request.** Two hooks reading
   `current_server_origins()` separately are two reads — the exact defect
   guardrail 2 exists for. The middleware captures once into `flask.g` (and into
   the environ for the Socket.IO path) and everything downstream reads that.

### 2.8 A4 must cover every socket event, and the client cap is in scope

The audit's GV-007 includes the voice events; the draft's A4 matrix covered
four handlers. There are **eleven**:

| Handler | Shape today | Needs |
|---|---|---|
| `connect` / `disconnect` | n/a | Token check (A2) |
| `join_workspace`, `leave_workspace` | `isinstance(data, dict)` guarded | Membership, caps |
| `lifecycle_flush_ack` | Raw `data` to the coordinator | Shape |
| `join_session` | `data.get(...)` | Shape, bounds, existence |
| `leave_session` | `data.get(...)` | Shape |
| `clear_terminal_buffer`, `terminal_input`, `terminal_resize` | `data.get(...)` | Shape, bounds, membership |
| `voice_start`, `voice_audio`, `voice_stop` | `data.get(...)` | Shape, membership, audio ceiling, recording caps |

`terminal_resize` additionally needs integer/type bounds, and `voice_audio`
needs a byte ceiling plus per-client and global recording caps — an unbounded
audio path is a memory ceiling with no owner.

**And the client cap cannot stay out of scope.** The draft recorded
`_MAX_TRACKED_SOCKET_CLIENTS` eviction as a known oddity and deferred it. That
was defensible while `client_joined_sessions` was only replay bookkeeping. A4
makes it the **authorization source**, and eviction — `next(iter(...))`, the
oldest-inserted client (`web/api.py:3259-3261`) — then silently revokes a
still-connected client's authority over its own sessions. Either use a dedicated
non-evicting live-membership registry keyed on connection lifetime, or fix the
cap's lifecycle as part of A4. **Recommended: the dedicated registry** —
membership is bounded by live connections already, so a cap on it is answering a
question the connection lifecycle answers better.

### 2.9 GV-027's strict CSP cannot ship in one step

The launcher and workspace pages carry **55 inline event-handler attributes**
(`onclick=`/`onchange=`/`oninput=`, counted across `templates/`) and two inline
`<script>` blocks. A nonce covers a `<script>` block; it does not cover an
attribute handler. A `script-src` strict enough to be worth having therefore
breaks the UI until those 55 handlers are migrated to `addEventListener`.

Separately, two directives must stay wide for features that exist today:

- **`frame-src`** — browser-preview panes frame arbitrary external URLs, so
  `default-src 'self'` written without it kills the feature.
- **`img-src`** — the Markdown preview renders external images by design
  (§1.3). `'self' data: blob:` would blank them. `http:` and `https:` stay in
  `img-src`, or blocking remote Markdown images becomes an explicit product
  change with its own UI treatment and tests. **Recommended: keep them.** A
  silently blank image in a rendered README reads as a broken preview, and
  nothing in this milestone is meant to change what the explorer renders.

**Consequence:** A5 enforces the directives that cost nothing today and ships
the strict policy as `Content-Security-Policy-Report-Only` alongside. The
handler migration is named as follow-on work, not smuggled into this milestone.

### 2.10 The origin baseline is 25 tests, and it cannot stay unmodified

The draft promised 16 tests unmodified. Both halves fail (§1.2, §1.3).

- **The count is 25**, across three classes. `ResolvedServerOriginsTestCase` —
  the class the draft never named — is the one most directly about A1.
- **Two of its tests assert what A1 removes.**
  `test_wildcard_bind_authorises_the_host_the_request_arrived_on` and
  `test_same_origin_policy_honours_a_reverse_proxy` encode the request-host
  fallback and the untrusted forwarded-header path. They are **inverted** by
  this milestone, deliberately, and rewritten to assert the refusal plus the
  configured replacement.
- **The rest need an honest, explicit authority.** They rely on the test
  client's implicit `Host: localhost` (no port) and send origins without the
  configured `:5050`. A correct full-origin policy rejects those pairings —
  `http://localhost` is not `http://localhost:5050`. Keeping them literally
  unmodified would require accepting portless origin equivalence, which is
  exactly the sloppiness A1 exists to remove.

**Consequence:** what is preserved is the **behavioural verdict**, not the test
source. Every test in the three classes supplies an explicit honest Host or base
URL; the verdict each asserts is unchanged except for the two named inversions,
which the exit gate names individually. → **guardrail:** a test that passes
because the client supplied a lax default authority is not evidence about the
authority check.

### 2.11 Remote-token mode is trusted-network-only

Without TLS the token and its cookie are observable on the wire. A2 is a large
improvement over an open port and it is not a public-exposure story.

**Consequence:** the posture string and its `/api/app-config` field say
**"authenticated, unencrypted — trusted network or TLS proxy required"**, not
just `remote-token`. The startup line must not imply it is safe to expose to the
internet.

---

## 3. Invariants to preserve

Work in this milestone must not disturb:

- **Loopback stays frictionless.** The default `127.0.0.1` experience gains no
  login step, no token prompt, and no extra click. Every new refusal is
  conditional on a configuration the user had to opt into.
- **`security.cors_origins` keeps its meaning**, including the documented
  `["*"]` escape hatch and its startup warning. It governs origin checks only —
  it never widens the Host allowlist (§2.7).
- **The cross-origin write guard keeps its current verdicts** for every honest
  request. The 25 baseline tests encode those verdicts; two are inverted on
  purpose and named in the exit gate (§2.10).
- **Room-scoped emits stay room-scoped.** Nothing here widens a broadcast.
- **`saved_sessions.json` on-disk format is unchanged.** Passwords stay
  Fernet-encrypted at rest with the existing key; A3 changes what leaves the
  process, not what is stored.
- **A stored password is never sent to a target it was not saved for.** The rule
  `_preset_ssh_credential()` already lives by (`web/workspaces.py:1050`); A3
  extends it to the launch path rather than introducing a looser one.
- **Markdown previews keep rendering external images.** A5's `img-src` admits
  `http:`/`https:` (§2.9).
- **Lifecycle logging is already shape-only** (`CLAUDE.md`). A6 extends that
  existing rule to terminal connection logging rather than inventing a new one.
- **Native mode keeps working**, including the download and upload bridges and
  the startup health probe (§2.6).
- **No new global JS.** Client changes in A3 go in `launcher.js`'s existing
  preset code or a new module — never `terminals.js`.

---

## 4. Sequencing

```
A0  entry-point security state ──┐   (§2.3 — prerequisite for A1/A2)
                                 │
A1  origin/host authority ───────┤
                                 ├──► one release unit (§2.1)
A2  bind gate + token   ─────────┘
                                 └──► A4  socket schema + authorization

A3  credential reference   (independent — ship first if splitting)
A5  response headers       (independent)
A6  log redaction          (independent)
```

**A0 is new**, carved out by §2.3: one resolved immutable security state,
resolved in the entry point from the config file actually named, published by
reference. It is small, it is a prerequisite for both A1 and A2, and it is not
GV-017's general import-order cleanup — that stays deferred.

A4 depends on A2 only for the handshake token check; its schema and membership
work is independent and may land first. A3, A5 and A6 have no dependencies on
each other or on A0/A1/A2 and can proceed in parallel.

**Recommended landing order:** A3 → A6 → A5 → A0+A1+A2 → A4. This front-loads
the packets with the highest data-exposure reduction per unit of regression
risk, and leaves the one packet that changes a supported configuration's
behaviour (A1+A2) for a point where the rest of the milestone is already green.

---

## 5. Work packets

### 5.0 A0 — one resolved security state, published by the entry point (§2.3)

**Problem.** The entry point parses `--config` after the application module has
already loaded a different file. A gate resolved from one and enforced from the
other is not a boundary.

**Changes.**

1. `web/config.py` grows `resolve_security_posture(config, **overrides)` beside
   `resolve_server_settings()`, so the explicit-flag-beats-config rule
   (guardrail 4) has one owner.
2. `main.py` and `web/webview_launcher.py` resolve host, port **and** posture
   from the config file they actually loaded, then call
   `install_server_origins(host, port, posture)` /
   `install_security_posture(posture)` before the server is created.
3. Both are published by a **single reference swap** to one immutable object and
   read once per operation — the model `RuntimeConfig` already uses, for the
   same reason (guardrail 2).

**Explicitly not in scope.** GV-017's general import-order cleanup. A0 fixes the
security state's provenance only; every other setting keeps reaching code the
way it does today.

---

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
    trusted_proxies: tuple[str, ...]  # addresses/CIDRs, never a bool (§2.5)
    proxy_hops: int
    wildcard: bool                    # security.cors_origins == ["*"]

resolve_server_origins(host, port, config) -> ServerOrigins
install_server_origins(host, port, posture) -> ServerOrigins   # one generation
current_server_origins() -> ServerOrigins
```

Published by a **single reference swap** to one immutable object, and captured
**once per request** — not once per hook (§2.7 item 5). The middleware stores
the captured generation in `flask.g` and in the WSGI environ; every downstream
check reads that object, never `current_server_origins()` again.

**Changes.**

1. **The host check is a WSGI middleware, not a Flask hook** (§2.2). It wraps
   `app.wsgi_app` *outside* `_SocketIOMiddleware`, so one implementation covers
   Flask routes and the Engine.IO handshake. It compares the request's `Host`
   (lowercased, IDNA-normalized, default port folded) against `allowed_hosts`; a
   miss is `403` with the same error shape the cross-origin guard returns. This
   runs on **`GET` too** — GV-003's routes are `GET`, and DNS rebinding's whole
   point is reading a response.
2. **The Socket.IO `connect` handler re-checks the same predicate**, because a
   WebSocket upgrade that skipped the polling handshake must not inherit an
   unchecked authority. One predicate, two call sites — never two predicates.
3. `_allowed_write_origin_netlocs()` is deleted. `_reject_cross_origin_writes()`
   compares the full `scheme://host:port` of the Origin header against
   `allowed_origins`. Scheme becomes part of the verdict.
4. `SameOriginPolicy.__call__` drops the `_request_origin(environ)` fallback.
   With no trusted proxy matched it consults `allowed_origins` only.
5. `_request_origin()` survives for the proxy case alone, and reads
   `X-Forwarded-*` **only** when `request.remote_addr` matches
   `trusted_proxies` (§2.5). → **guardrail:** a forwarded header is evidence
   only when the peer is a declared trusted proxy.
6. `apply_resolved_server_origins(host, port)` calls `install_server_origins()`
   so the CLI-flag-wins rule (guardrail 4) reaches the new authority by the same
   path it already reaches Socket.IO, from the state A0 resolved.

**New config keys**, all through `RuntimeConfig` (guardrail 5), under `security`:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `allowed_hosts` | list[str] | `[]` | Additional `host` / `host:port` values accepted in the Host header |
| `trusted_proxies` | list[str] | `[]` | Peer addresses/CIDRs whose `X-Forwarded-*` may be believed |
| `proxy_hops` | int | `1` | How many forwarded hops to traverse |

**How `allowed_hosts` is composed** (§2.7 items 1–3). The derived set **always**
applies and `allowed_hosts` is additive to it — it is not an override:

- `127.0.0.1:<port>`, `localhost:<port>`, `[::1]:<port>` — always.
- The resolved bind host when it is not in `_UNROUTABLE_BIND_HOSTS`.
- **The netloc of every configured `security.cors_origins` entry**, so a
  reverse-proxy deployment that names its origin today keeps working tomorrow.
- Everything in `security.allowed_hosts`.

A wildcard bind contributes nothing on its own, so **remote mode with a wildcard
bind and no explicit reachable authority refuses to start** (A2), rather than
starting into a server that 403s every LAN request.

**`cors_origins: ["*"]` does not disable Host validation** (§2.7 item 4). It
keeps its documented meaning for the origin checks — both guards off, startup
warning unchanged — and the Host allowlist still applies. The two answer
different questions.

**Compatibility.** `security.cors_origins` continues to win outright for origin
verdicts. Loopback users see no change: `127.0.0.1:5050` and `localhost:5050`
are both derived, both directions of the existing `.replace()` aliasing
preserved.

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
and mutates the filesystem, so this is remote command authority. Origin and CORS
checks are browser controls and do not apply to a non-browser client, which
chooses its own headers.

**Changes.**

1. **Bind gate.** `main.py` and `web/webview_launcher.py` gain
   `--allow-remote-access`; config gains `security.allow_remote_access` (bool,
   default `false`), resolved by A0's `resolve_security_posture()`. A
   non-loopback resolved host without the opt-in **refuses to start**: one
   message naming the host, the flag and the config key, then `sys.exit(2)`. No
   server is created — a warning that leaves the port open is not a gate.
2. **A wildcard bind must also name a reachable authority** (§2.7 item 3).
   `--host 0.0.0.0 --allow-remote-access` with an empty `allowed_hosts` and no
   concrete configured origin refuses to start by the same route, naming
   `security.allowed_hosts`. Starting into a server that 403s every remote
   request is the failure this prevents.
3. **Token.** In remote mode a token is required on both transports. Resolved in
   order: `GRIDVIBE_AUTH_TOKEN` env → `security.auth_token` → generated
   `secrets.token_urlsafe(32)`. A generated token is printed to the console
   once at startup, never to `logs/gridvibe.log`. → **guardrail:** a capability
   token is a credential; it never reaches the rotating log.
4. **HTTP.** `_require_authenticated()` runs after the host check. It accepts
   `Authorization: Bearer <token>` or the `gridvibe_auth` cookie, compared with
   `hmac.compare_digest`. Loopback mode installs no check at all, so the default
   path adds not one comparison.
5. **The bootstrap carries the token in a fragment, never a query string**
   (§2.6). `GET /auth#token=…` serves a minimal page that POSTs the fragment
   value; the POST handler sets the cookie `HttpOnly; SameSite=Strict; Path=/`
   (and `Secure` when the request is HTTPS) and redirects to `/`. A fragment is
   never sent to the server, so it cannot reach an access log, a proxy log, or
   the browser-launch command line.
6. **Socket.IO.** The `connect` handler validates the same cookie/auth payload
   and returns `False` to refuse the connection before any room is joined.
7. **Native mode is handled explicitly** (§2.6). `_wait_for_server()`'s health
   probe, the download bridge and the upload bridge each carry the token, and
   both WebViews get an authenticated cookie bootstrap before their first load.
   **If any of those is not delivered, A2 declares native mode loopback-only and
   refuses a native remote bind at startup** — an unstated third option is how a
   native window comes to open on a server that refuses it.
8. **Startup posture line.** One log line and one `/api/app-config` field report
   the effective posture: `loopback`, or `remote-token — authenticated,
   unencrypted; trusted network or TLS proxy required` (§2.11). → this is the
   audit's "display the effective security state" recommendation, reduced to its
   testable core.

**Interaction with A1.** In remote mode the resolved bind host joins
`allowed_hosts`, and `security.allowed_hosts` is how an operator adds the public
names they actually browse. That is the replacement for the deleted request-host
fallback (§2.1), and item 2 is what stops it being silently empty.

**Explicitly out of scope:** TLS termination, user accounts, per-user
authorization, and token rotation. Milestone A delivers *one shared capability
token*, which is what makes the boundary honest; it is not a multi-user model
and the startup line must not imply it is.

---

### 5.3 A3 — saved passwords stop leaving the process (GV-003)

**Problem.** `_normalize_stored_payload()` decrypts into the in-memory config;
`GET /api/session-config`, `GET /api/saved-sessions/<id>`, the `DELETE`
response and `POST /api/session-config` return that config whole. Any
same-origin script, rebinding bypass, page-scoped extension, or future XSS reads
reusable SSH credentials.

**The contract** (§2.4). A password is meaningless without the target it
authenticates against, so the server resolves it from a **credential reference
plus the target actually being launched** — never from a preset ID alone:

```
resolve_ssh_credential(
    credential_reference,       # preset whose secret the form borrowed
    host, username, port,       # the target this launch actually names
    supplied=MISSING,
) -> str
```

| `supplied` | Meaning | Result |
|---|---|---|
| `MISSING` | not stated | the referenced preset's stored password **only if `host`/`username`/`port` still match it**; otherwise `""` |
| `""` | stated empty | `""` — key auth, an explicit clear |
| non-empty string | stated | the supplied value |

Two rules make it safe. **An unstated dimension is left alone; only a stated
`""` clears one** — the sentence `web/session_shell.py` already lives by, so
there is one rule to learn rather than two. And **the reference is honoured only
against the target it was saved for**, which is `_preset_ssh_credential()`'s
existing rule (`web/workspaces.py:1050`) rather than a looser new one.

**`credential_reference` is separate from the persisted `saved_session_id`**,
because the target picker deliberately drops the latter to keep the launch a
scratch session (§2.4 item 1). Collapsing them is what breaks that path.

**Server changes.**

1. `_saved_session_response()` never emits `config.ssh.password`. It emits
   `config.ssh.has_password` (bool) instead — the fact the UI actually needs,
   and the field `/api/session-targets` already publishes
   (`web/saved_sessions.py:1048`).
2. `load_session_config()` applies the same redaction — this covers both
   `GET` and `POST /api/session-config`.
3. **The `DELETE /api/saved-sessions` inline config is redacted too**
   (`web/api.py:2771`). It is outside `_saved_session_response()` and the
   launcher feeds it to `applySessionConfig()`, so it is a live prefill path.
   → **guardrail:** redaction lives in the response builders, and a route that
   assembles a preset config inline is a route that must use one.
4. `resolve_ssh_credential()` in `web/saved_sessions.py`, called inside the
   launch transaction and at **five** call sites: launch (both pages), agent
   preflight, preset overwrite, preset clone / Save As, and the target-picker
   scratch launch.
5. **Agent preflight takes a credential reference, not a password.**
   `_build_agent_preflight_request()` and `_resolve_preflight_environment()`
   (`web/agents.py:855`, `:498`) resolve through the same function, so a
   password-only host still preflights.
6. **Preset save never writes an empty password over a stored one it did not
   show.** `upsert_saved_session()` assigns the config wholesale, so the launch
   payload's tri-state must reach it: unstated preserves the stored secret,
   stated `""` clears it. Save As resolves against the source reference for the
   same reason.

**Client changes** (`launcher.js`, the preset/target code; no new global):

7. `#ssh_password` is never populated from a response. When `has_password` is
   true the field renders empty with placeholder *"Saved password in use — type
   to replace"*.
8. **The state lives in JS, not in a DOM attribute** (§2.4 item 8):
   `{ credentialReference, dirty }`, because `renderModeFields()` replaces the
   field's markup on every mode switch and would drop a `data-*` marker.
   `dirty` is set on first `input`.
9. **The launch builders stop collapsing `""` to `null`** (`launcher.js:3600`,
   `terminals.js:1833`). Not dirty and a reference is held → **omit `password`
   entirely** and send `credential_reference` (the unstated case). Dirty → send
   the field's literal value, `""` included (the stated cases).
10. **The target picker sends the reference it borrowed from** while still
    clearing `saved_session_id`, so a scratch launch keeps password
    authentication without becoming a preset launch.
11. **The workspace page's warning reads the resolved outcome, not the payload**
    (`terminals.js:1946-1948`). It must not say "No password is saved" because
    the payload it can see was redacted; the launch response reports whether a
    credential was resolved.
12. The reveal button (`#show_ssh_password`) can no longer reveal a stored
    secret, because the page no longer has one. It keeps working for a password
    the user is typing now. **This is a deliberate feature removal** and belongs
    in `CHANGELOG.md`. If reveal-a-saved-password must return, it comes back as
    a separate confirmed short-lived action, never as a field prefill.

**Compatibility.** On-disk format unchanged; no migration. A preset saved before
this change launches correctly because the server resolves its own stored secret
against the target the launch names.

---

### 5.4 A4 — socket events are validated and authorized (GV-007)

**Problem.** Handlers call `data.get(...)` on whatever arrives (all four
originally probed crash on a non-mapping payload), join rooms for sessions that
do not exist, create buffers for arbitrary IDs, and let any socket write to any
live connection without ever having joined it.

**New module: `web/socket_events.py`** — one validator every handler passes
through, so a new event cannot quietly skip it.

```
@validated_event(schema, require_session=..., require_membership=...)
```

- **Shape.** Non-mapping payload → acknowledged error, never an exception. →
  **guardrail:** a socket handler never indexes an unvalidated payload; the
  decorator is the only place a payload becomes a mapping.
- **Bounds.** `session_id` must be a string, `1..128` chars, matching the ID
  charset. `terminal_resize` takes integer bounds. `data`/audio chunks get
  explicit byte ceilings.
- **Existence.** `join_session` joins **no room** when no such session exists.
- **Membership.** `terminal_input`, `terminal_resize`, `clear_terminal_buffer`
  and the voice events require that this socket joined that session. →
  **guardrail:** the room is the authorization unit; a socket may act only on a
  session it joined.
- **Caps.** `_MAX_ROOMS_PER_CLIENT`, plus per-client and global voice-recording
  caps.
- **Errors.** No raw exception text is written into terminal output. The handler
  acknowledges an error category; the detail goes to the log.

**Coverage is every handler, not four** (§2.8). All eleven `@socketio.on`
handlers pass through the decorator: `connect`, `disconnect`, `join_workspace`,
`leave_workspace`, `lifecycle_flush_ack`, `join_session`, `leave_session`,
`clear_terminal_buffer`, `terminal_input`, `terminal_resize`, `voice_start`,
`voice_audio`, `voice_stop`. `join_workspace`/`leave_workspace` already guard
their shape and need membership and caps only; `lifecycle_flush_ack` hands raw
`data` to the coordinator and needs shape.

**Membership gets its own registry** (§2.8). `client_joined_sessions` is
evicted at `_MAX_TRACKED_SOCKET_CLIENTS` by `next(iter(...))` — the
oldest-*inserted* client (`web/api.py:3259-3261`). Making it the authorization
source without changing that means eviction silently revokes a still-connected
client's authority over its own sessions. A4 introduces a **non-evicting live
membership registry keyed on connection lifetime**; the replay-tracking cap can
keep its own eviction, because losing replay bookkeeping is a cosmetic reset and
losing authority is not. → **guardrail:** an authorization registry is bounded by
connection lifetime, never by an eviction policy that can drop a live client.

**One separate fix in `web/terminal_io.py`:**
`_clear_terminal_output_buffer()` currently *assigns*
`session_output_buffers[session_id] = _OutputBuffer()`, which creates an entry
for any ID (2 000 fabricated IDs → 2 000 entries). It must clear an existing
buffer and create nothing.

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
| `Content-Security-Policy` | `default-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: http: https:; font-src 'self'; connect-src 'self' ws: wss:; frame-src *` |

Three directives are deliberately wide and must stay that way until something
changes, written out rather than omitted so the policy states what it does
**not** yet protect:

- `frame-src *` — browser-preview panes frame arbitrary URLs.
- `script-src 'unsafe-inline'` — the 55 inline handlers (§2.9).
- **`img-src … http: https:`** — the Markdown preview renders external images
  today (§1.3, §2.9). Dropping them is a product change, not a header tightening,
  and is not in this milestone.

**Shipped alongside:** `Content-Security-Policy-Report-Only` carrying the target
policy (`script-src 'self'`, no `'unsafe-inline'`). Report-Only is how the
`connect-src`/WebSocket and vendored-asset assumptions get *verified in a real
browser* rather than asserted here. The Report-Only policy keeps the wide
`img-src`: it is a scripting-injection target, and narrowing images there would
report violations for a feature nobody intends to remove.

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
connect; line 1489 logs the fully composed local shell command. Startup commands
routinely carry env assignments, bearer tokens and credential-bearing URLs, and
these land in a rotating file.

**Changes.**

1. Both lines become shape-only: session ID, connection family, WSL/PowerShell
   booleans, whether a directory and an initial command are *present*, and their
   lengths. Never the values. This is the existing lifecycle logging rule
   (`CLAUDE.md`) applied to terminal connection logging. → **guardrail.**
2. New `security.log_commands` (bool, default `false`), through `RuntimeConfig`.
   When enabled, the full command is logged at **DEBUG** and startup emits a
   conspicuous warning naming the retention risk — the pattern
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

1. **The security posture is resolved once by the entry point** from the config
   file actually named, and published by reference; no request-path code
   re-derives it from disk. (A0)
2. **The request never supplies the authority it is checked against.** `Host` is
   validated against a set derived from resolved server settings; it is never
   added to its own allowlist. Origin comparison is full scheme+host+port.
   `cors_origins: ["*"]` governs origin checks only and never widens the Host
   allowlist. (A1)
3. **An authority check that must cover the Socket.IO transport is WSGI-level.**
   `app.wsgi_app` is `_SocketIOMiddleware`, so a Flask `before_request` never
   sees the Engine.IO handshake. One predicate, called from the middleware and
   from `connect` — never two predicates. (A1)
4. **A forwarded header is evidence only when the peer is a declared trusted
   proxy**, matched against `request.remote_addr`. A boolean that trusts the
   header without checking who sent it is not a trust boundary. (A1)
5. **A refusal a legitimate user can hit names the setting that lifts it**, in
   the response body and in the WARNING log line. (A1)
6. **A non-loopback bind is an explicit mode with a capability token**, a token
   never reaches `logs/gridvibe.log`, and a capability is never carried in a URL
   query string — a fragment or a POST body, so it cannot be logged. (A2)
7. **A saved secret is redacted in the response builders, not at the call
   site** — and a route that assembles a preset config inline uses one. **A
   stored credential is resolved against the target being launched**, never from
   a preset ID alone. A payload dimension that may be unstated is tri-state:
   unstated leaves it alone, a stated empty value clears it, and no builder
   between the form and the server may collapse the two. (A3)
8. **A socket handler never indexes an unvalidated payload**, and the room is
   the authorization unit — a socket may act only on a session it joined. **An
   authorization registry is bounded by connection lifetime**, never by an
   eviction policy that can drop a live client. (A4)
9. **The global header hook fills gaps and never relaxes a header a route set
   deliberately**, and a CSP directive is not narrowed past a feature that ships
   today — `frame-src` for browser panes, `img-src` for Markdown previews. (A5)
10. **Operational logs are shape-only** — ids, family, counts, categories,
    presence and length; never commands, directories, or payloads. (A6)
11. **A test that passes because the client supplied a lax default authority is
    not evidence about the authority check.** Every origin/host test states its
    Host explicitly. (§2.10)

---

## 7. Test matrix

New file **`tests/test_security_boundary.py`** for A0/A1/A2/A5; A3 and A4 extend
`tests/test_api.py` beside the code they cover; A0's and A2's startup gates
extend `tests/test_main.py`.

Behavioural tests throughout — these are Python routes and handlers, so they are
exercised by calling them, never by asserting on source text.

### A0 — resolved security state

| # | Case | Expect |
|---|---|---|
| 1 | `--config other.json` naming a non-loopback host | the gate reads *that* file |
| 2 | Entry point resolves posture; request path never re-reads config | asserted |
| 3 | Two generations installed mid-flight | one operation reads one generation |

### A1 — host and origin matrix

| # | Case | Expect |
|---|---|---|
| 4 | `Host: evil.example:5050` + matching Origin, `POST` | `403` |
| 5 | Same, `GET /api/session-config` | `403` — the rebinding read is the point |
| 6 | **Hostile Host on the Socket.IO handshake, matching Origin** | refused |
| 7 | **Hostile Host on the Socket.IO handshake, no Origin** | refused |
| 8 | **Hostile Host on a WebSocket upgrade that skipped polling** | `connect` refuses |
| 9 | Honest Host, hostile Origin | `403` (baseline preserved) |
| 10 | Honest Host, no Origin (pywebview, non-CORS) | passes |
| 11 | `127.0.0.1:5050` and `localhost:5050` both directions | pass |
| 12 | IPv6 `[::1]:5050`, bracketed and with default port folded | pass |
| 13 | Scheme mismatch: configured `https://proxy.example`, request `http://` | `403` |
| 14 | `X-Forwarded-Host: evil.example` from an untrusted peer | ignored → `403` |
| 15 | Same from a peer in `trusted_proxies`, origin configured | passes |
| 16 | **`trusted_proxies` set, request from a peer outside it** | forwarded headers ignored |
| 17 | **A configured `cors_origins` netloc is an accepted Host** | passes |
| 18 | **`cors_origins: ["*"]`: origin checks off, Host validation still on** | hostile Host `403` |
| 19 | `cors_origins: ["*"]` startup warning | emitted |
| 20 | `SameOriginPolicy` with hostile `HTTP_HOST` | `False` |
| 21 | `SameOriginPolicy` with forwarded spoof, no trusted peer | `False` |
| 22 | Host rejection log names received Host and allowed set | asserted |
| 23 | Rejection body names `security.allowed_hosts` | asserted |
| 24 | **One generation captured per request, read by both checks** | asserted |

### A2 — bind gate and token

| # | Case | Expect |
|---|---|---|
| 25 | `--host 0.0.0.0` without opt-in | `SystemExit(2)`, no server created |
| 26 | **`--host 0.0.0.0 --allow-remote-access`, no named authority** | `SystemExit(2)` naming `security.allowed_hosts` |
| 27 | Same with a named `allowed_hosts` | starts, posture `remote-token` |
| 28 | Config `allow_remote_access: true`, no flag | starts |
| 29 | Flag beats config in both directions | resolved value wins |
| 30 | Remote mode, no token | HTTP `401`, socket `connect` refused |
| 31 | Remote mode, wrong token | `401`, constant-time compare |
| 32 | Remote mode, valid bearer / valid cookie | pass |
| 33 | **`GET /auth#token=…`** | token never in the request; POST sets HttpOnly cookie |
| 34 | **No request path logs a token** | scan of captured log records |
| 35 | Loopback mode | no token required, no check installed |
| 36 | Generated token | printed to console, absent from the log file |
| 37 | **Native: `_wait_for_server()` health probe in remote mode** | authenticated, or startup refuses |
| 38 | **Native: download and upload bridges in remote mode** | authenticated, or startup refuses |
| 39 | **Posture string names "unencrypted — trusted network"** | asserted in log and `/api/app-config` |

### A3 — no secret leaves the process

| # | Case | Expect |
|---|---|---|
| 40 | `GET /api/session-config` with a password-bearing preset | no `password` key; `has_password: true` |
| 41 | `GET /api/saved-sessions/<id>` | same |
| 42 | `GET /api/saved-sessions` (list) | same (regression cover) |
| 43 | **`POST /api/session-config` response** | same |
| 44 | **`DELETE /api/saved-sessions` inline config** | same |
| 45 | `GET /api/session-targets` | secret-free (baseline preserved) |
| 46 | **Sweep:** every registered route reachable without arguments, **every method**, recursive scan of the JSON body for the stored password value | no hit |
| 47 | Socket emit sweep over a scripted session lifecycle | no hit |
| 48 | Launch, `password` absent, reference held, target matches | stored secret used |
| 49 | **Launch, `password` absent, reference held, host edited** | `""` — never the old secret |
| 50 | **Same for an edited username, and an edited port** | `""` each |
| 51 | Launch, `password: ""` | empty — explicit clear |
| 52 | Launch, `password: "typed"` | `"typed"` |
| 53 | Launch, no reference, no password | `""`, no lookup |
| 54 | Launch naming a deleted preset | `""`, refused cleanly, not a crash |
| 55 | **Target-picker scratch launch** — reference held, `saved_session_id` null | stored secret used, no preset activated |
| 56 | **Agent preflight, password-only host, redacted form** | preflight succeeds |
| 57 | **Import a preset, re-save untouched** | stored password preserved |
| 58 | **Import a preset, Save As under a new name** | password carried to the clone |
| 59 | **Save with the field explicitly cleared** | stored password cleared |
| 60 | **Launch builders do not collapse `""` to `null`** | unstated omits the key; `""` is sent |
| 61 | **Workspace page warning** | accurate after redaction — no false "No password is saved" |
| 62 | Round trip: save with password → reload → launch | connects |

Cases 46 and 47 are the ones that keep this fixed. They scan for the *value*, so
a future route returning the config by a new path fails them without anyone
remembering to add a case. → this is the audit's "response-shape scan", widened
past `GET` by §2.4 item 7.

### A4 — socket validation and authorization

| # | Case | Expect |
|---|---|---|
| 63 | **Every one of the eleven handlers** × `{None, [], "x", 5, nested dict}` | error ack, no exception |
| 64 | `session_id` non-string / empty / 10 000 chars | refused |
| 65 | `join_session` for a nonexistent session | no room joined |
| 66 | `clear_terminal_buffer` for a nonexistent session | no buffer created |
| 67 | 2 000 fabricated `clear_terminal_buffer` calls | registry size unchanged |
| 68 | Client B sends `terminal_input` for A's session, B never joined | refused |
| 69 | Same for `terminal_resize`, `clear_terminal_buffer` | refused |
| 70 | **Same for `voice_start`, `voice_audio`, `voice_stop`** | refused |
| 71 | **`leave_session` with a malformed payload** | error ack, no exception |
| 72 | **`lifecycle_flush_ack` with a non-mapping payload** | error ack, coordinator untouched |
| 73 | **`terminal_resize` with non-integer / out-of-range dimensions** | refused |
| 74 | **Oversized `voice_audio` chunk** | refused at the ceiling |
| 75 | **Per-client and global voice recording caps** | enforced |
| 76 | Client joins more than `_MAX_ROOMS_PER_CLIENT` | capped |
| 77 | Oversized `terminal_input` payload | refused at the ceiling |
| 78 | **A live client past `_MAX_TRACKED_SOCKET_CLIENTS`** | keeps authority over its own sessions |
| 79 | Handler raises internally | no raw exception text in `terminal_output` |
| 80 | Normal single-client join → input → resize → leave | unchanged behaviour |

### A5 — headers

| # | Case | Expect |
|---|---|---|
| 81 | `GET /` and the workspace page | all five headers present |
| 82 | CSP contains `frame-ancestors 'self'`, `object-src 'none'`, `base-uri 'self'` | asserted |
| 83 | `frame-src` admits `http:`/`https:` | asserted — pins the browser pane |
| 84 | **`img-src` admits `http:`/`https:`** | asserted — pins the Markdown preview |
| 85 | **A Markdown file with `![x](https://…)` renders an external `<img>`** | unchanged by A5 |
| 86 | Inline-image route | keeps `default-src 'none'; … sandbox`, not widened |
| 87 | Static asset | `nosniff`, no page CSP |
| 88 | Report-Only present and stricter than enforced on `script-src` | asserted |
| 89 | JSON API response | `nosniff` |

### A6 — logs

| # | Case | Expect |
|---|---|---|
| 90 | SSH connect with `initial_command` carrying a token | command absent from records |
| 91 | Local/WSL connect | composed command absent |
| 92 | Both | session ID and family present — still diagnosable |
| 93 | `log_commands: true` | command at DEBUG only, startup warning emitted |
| 94 | `log_commands` default | `false` |
| 95 | Directory values | absent; presence and length only |

### Regression

- The **25** origin/CORS tests across `CorsOriginDefaultsTestCase`,
  `ResolvedServerOriginsTestCase` and `CrossOriginWriteGuardTestCase` are green,
  each supplying an explicit honest Host or base URL (§2.10). Every verdict is
  unchanged except the two deliberate inversions:
  `test_wildcard_bind_authorises_the_host_the_request_arrived_on` and
  `test_same_origin_policy_honours_a_reverse_proxy`, which now assert the
  refusal plus its configured replacement.
- Eight SSH save/restore and credential-matching tests green and unmodified —
  A3 must not disturb the restore architecture, which already binds a credential
  to its target.
- Full runner green on Windows. GV-009's two known Windows failures
  (`test_repo_git_timeout_bounds_a_remote_that_goes_quiet`,
  `test_a_stalled_remote_returns_the_worker_thread_within_the_bound`) belong to
  Milestone C and are **not** a Milestone A gate; they must be no worse.

---

## 8. Exit gate

Milestone A is complete when every line is checked with evidence attached.

**Boundary**
- [ ] Hostile `Host` + matching `Origin` rejected for HTTP `GET` and `POST` (4, 5)
- [ ] Hostile `Host` rejected on the Socket.IO handshake with and without an Origin, and on a bare upgrade (6, 7, 8)
- [ ] Hostile origin rejected by `SameOriginPolicy` with and without forwarded headers (20, 21)
- [ ] Forwarded headers ignored unless the peer is a trusted proxy; honoured when it is (14, 15, 16)
- [ ] Scheme is part of the origin verdict (13)
- [ ] IPv4, IPv6, default-port and localhost/127.0.0.1 aliasing all pass (11, 12)
- [ ] A configured `cors_origins` netloc is an accepted Host (17)
- [ ] `cors_origins: ["*"]` disables the origin checks, still warns, and does **not** disable Host validation (18, 19)
- [ ] One security generation captured per request (3, 24)
- [ ] The gate and the request path read the same `--config` file (1, 2)

**Network exposure**
- [ ] Non-loopback bind without opt-in refuses to start (25)
- [ ] Wildcard bind with the opt-in but no named authority refuses to start (26)
- [ ] Remote mode refuses unauthenticated HTTP and Socket.IO (30)
- [ ] Loopback default gains no authentication step (35)
- [ ] The token is never carried in a URL query string (33)
- [ ] Generated token never appears in `logs/gridvibe.log` (34, 36)
- [ ] Native mode is authenticated in remote mode, or refuses a remote bind (37, 38)
- [ ] The posture string says "unencrypted — trusted network or TLS proxy required" (39)

**Secrets**
- [ ] No response of any method contains a stored password value (46)
- [ ] No Socket.IO payload contains one (47)
- [ ] A stored credential never reaches an edited target (49, 50)
- [ ] Target-picker scratch launch keeps password authentication (55)
- [ ] Password-only agent preflight still succeeds (56)
- [ ] Import → re-save and Save As preserve the stored password (57, 58)
- [ ] An explicit clear still clears (51, 59)
- [ ] Password-bearing presets still launch, all three tri-state cases (48, 51, 52, 62)
- [ ] The workspace page's warning is accurate after redaction (61)
- [ ] `CHANGELOG.md` records the reveal-a-saved-password removal

**Socket events**
- [ ] No handler — all eleven — raises on any malformed payload (63, 71, 72)
- [ ] Cross-client input/resize/clear and voice refused (68, 69, 70)
- [ ] No unbounded registry growth from fabricated IDs (67)
- [ ] Audio and resize bounds and recording caps enforced (73, 74, 75)
- [ ] Per-client room cap enforced (76)
- [ ] A live client past the tracking cap keeps its authority (78)

**Headers and logs**
- [ ] Five headers on both pages; route-set CSP not widened (81, 86)
- [ ] `frame-src` and `img-src` still admit what ships today, and an external Markdown image still renders (83, 84, 85)
- [ ] Report-Only strict policy shipped, and its browser-observed violations
      recorded for the follow-on handler migration (88)
- [ ] No raw startup command or directory in the log at default settings (90, 91, 95)

**Suite**
- [ ] 25 origin tests green, each stating its Host explicitly; the two
      inversions reviewed individually and named in the commit
- [ ] Eight SSH save/restore credential tests green and unmodified
- [ ] `make check` green on Windows; Milestone C's two known failures no worse
- [ ] Guardrails from §6 written into `CLAUDE.md` and `AGENTS.md`

**Evidence to attach:** the runner's summary line, the case numbers above mapped
to test names, and the console transcript of case 25/26 (startup refusal) and
case 46 (secret sweep).

---

## 9. Rollback

Each packet is independently revertable; the sequencing in §4 is what keeps that
true.

- **A0** reverts to the import-time config read. It is a prerequisite for A1+A2,
  so reverting it means reverting those too.
- **A1+A2** revert together (§2.1). Reverting either alone leaves the wildcard
  bind broken. Kill switch short of a revert: `security.cors_origins` set to an
  explicit list restores verbatim-honoured origins, and `["*"]` restores today's
  origin behaviour — but no longer disables Host validation, so
  `security.allowed_hosts` is the lever for a Host refusal.
- **A3** reverts server-side and client-side together; the on-disk format never
  changed, so no data migration is involved in either direction.
- **A4** reverts as one module plus the handler decorators and the membership
  registry.
- **A5** is one `after_request` hook — deleting it restores today's behaviour
  exactly.
- **A6** is log-line edits plus one config key.

**Field kill switches** (no redeploy): `security.cors_origins`,
`security.allowed_hosts`, `security.trusted_proxies`, `security.log_commands`.

---

## 10. Out of scope

Named so they are not silently absorbed:

- TLS, user accounts, per-user authorization, token rotation (A2 ships one
  shared capability token, deliberately, for a trusted network).
- GV-017's general import-order cleanup. A0 fixes the *security state's*
  provenance only.
- Migrating 55 inline event handlers so `'unsafe-inline'` can be dropped (A5's
  named follow-on — the work that makes the CSP meaningful against injection).
- Blocking external images in Markdown previews. `img-src` keeps `http:`/`https:`;
  narrowing it is a product change with its own UI treatment (§2.9).
- OS credential-vault storage (audit §10; A3 stops the leak, it does not change
  where the secret lives).
- Serving browser-preview content from a separate origin (audit GV-027's second
  recommendation; a real isolation boundary, and its own project).
- Everything in Milestones B–E.

**No longer out of scope:** the `_MAX_TRACKED_SOCKET_CLIENTS` eviction, which
A4's authorization model pulls in (§2.8).
