# MCP Surface Review — 2026-09-17

A read of every module that carries the GridVibe MCP feature, looking for
defects, unreachable code, races and claims the implementation does not keep.
Historical evidence, not a contract: anything here that becomes a rule belongs
in [`docs/engineering_contracts.md`](engineering_contracts.md) or
[`gridvibe_mcp/README.md`](../gridvibe_mcp/README.md), and this file is not
cited from either.

Nothing below was changed *when this was written*. Each finding names the file,
what was true, why it matters, and what the fix would be.

**Since then, the two High findings and all six Medium ones have been fixed** —
[1](#1) and [2](#2), then [3](#3) through [8](#8), all on 2026-09-17, code and
tests only. Each carries a **Fixed** note saying what changed and what pins it.
The nine Low findings and the Info one stand exactly as written, and the review
remains historical evidence rather than a contract: the rules the eight fixes
established live in the modules' own docstrings.

Nothing outside code and tests was touched for the Medium round either — no
README, no `CHANGELOG.md`, no `docs/engineering_contracts.md`, and not
`gridvibe_mcp/README.md`, which [finding 20](#20) already describes as four
tools out of date. Where a fix was a wording change ([3](#3)), the wording went
into the module's own docstring.

## Scope

| Half | Modules |
| --- | --- |
| Sidecar (out-of-process) | `gridvibe_mcp/__main__.py`, `client.py`, `identity.py`, `server.py`, `splits.py`, `windows.py` |
| Server (in-process) | `web/mcp_http.py`, `web/mcp_launch.py`, `web/ssh_tunnel.py`, `web/pane_gates.py`, `web/session_clear.py`, the gated halves of `web/session_shell.py` and `web/session_modes.py`, `web/window_intents.py`, the MCP parts of `web/agents.py`, `web/terminal_io.py`, `web/workspaces.py` |
| Routes | `POST /mcp/<token>`, `/api/sessions/<id>/{agent-relaunch,agent-mode-switch,clear,split-intent}`, `/api/panes/layout`, `/api/windows/*` |
| Tooling | `utils/mcp_status.py`, `Makefile` MCP targets, `requirements-mcp.txt` |

Tests read: `tests/test_mcp_tools.py`, `test_mcp_client.py`, `test_mcp_identity.py`,
`test_mcp_launch.py`, `test_mcp_remote.py`, `test_mcp_status.py`,
`test_pane_gates.py`, `test_session_clear.py`, `test_window_intents.py`,
`test_window_intent_client.py`.

## Summary

| # | Severity | Finding |
| --- | --- | --- |
| [1](#1) | High — **fixed** | The SSH reverse tunnel forwards GridVibe's whole HTTP API, not just `/mcp/<token>` |
| [2](#2) | High — **fixed** | A pane token is never revoked when its pane closes |
| [3](#3) | Medium — **fixed** | The lineage and self gates rest on a variable the constrained agent controls |
| [4](#4) | Medium — **fixed** | `make mcp-status` probes a route GridVibe has never had |
| [5](#5) | Medium — **fixed** | Over HTTP, `split_pane`/`open_window` hold a request thread for 25s and re-enter the server ~50 times |
| [6](#6) | Medium — **fixed** | The sidecar can give up before the intent store does, and then claims nothing was touched |
| [7](#7) | Medium — **fixed** | A close racing an SSH connect leaks a remote listener, a config file and an SFTP channel |
| [8](#8) | Medium — **fixed** | An SSH pane whose agent has no MCP mechanism still opens a tunnel and mints a token |
| [9](#9) | Low | `GridVibeClient.split()` is dead, and is the call `splits.py` exists to avoid |
| [10](#10) | Low | `PaneTokenRegistry.token_for()` is dead |
| [11](#11) | Low | `IDENTITY_VARIABLES` claims a single source of truth it is not |
| [12](#12) | Low | The gated relaunch is the one write path that skips the agent-depth cap |
| [13](#13) | Low | The `layout` enum advertises values GridVibe silently rewrites below four panes |
| [14](#14) | Low | A cross-workspace `list_panes` publishes the caller's own group's layout block |
| [15](#15) | Low | `create_workspace` is unbounded, and what it creates is never pruned |
| [16](#16) | Low | Every tool-launched terminal pane is a *stated* PowerShell pane |
| [17](#17) | Low | One transient poll failure reports a split that may have happened as a failure |
| [18](#18) | Low | The streamable-HTTP endpoint implements the POST half only |
| [19](#19) | Low | `whoami` says an agent may not launch panes without saying it may still split one |
| [20](#20) | Info | Documentation drift: the sidecar README, `CLAUDE.md`, and the contracts |

---

<a id="1"></a>
## 1. The SSH reverse tunnel forwards GridVibe's whole HTTP API — High

**Where:** `web/ssh_tunnel.py` (`_serve_forwarded_channel`, `open_reverse_tunnel`)

`_serve_forwarded_channel` opens a raw socket to `local_host:local_port` — the
address `web/mcp_launch.server_base_url()` resolves to, which is GridVibe's own
Flask port — and pumps bytes. It is a plain TCP forward, so the remote host does
not receive an MCP endpoint; it receives **all of GridVibe's loopback HTTP API**,
and the per-pane token guards exactly one route on it.

Everything else on that API is unauthenticated, because until now it was only
ever reachable from the machine GridVibe runs on. Reachable from the remote host
while a tunnelled pane is open, with no token:

- `GET /api/saved-sessions/<id>` — answers with `config`, and `load_saved_sessions()`
  decrypts `ssh.password` on the way out (`web/saved_sessions.py:732-736`). The
  sidecar's own `SAVED_LAYOUT_FIELDS` comment names this route as the reason that
  allowlist exists; the raw route is next to it on the same port.
- `GET /api/sessions` — every pane's `to_dict()`, across every workspace.
- `DELETE /api/sessions/<id>`, `DELETE /api/sessions`, `DELETE /api/workspaces/<id>` —
  the whole destroy tier the tool surface deliberately does not contain.
- `POST /api/app-config`, `POST /api/select-folder`, `POST /api/sessions/<id>/shell`
  and `/mode` — the **ungated** twins of the gated tool routes, which check nobody
  because "the person pressing it is looking at the pane".
- `POST /api/windows/intents/<id>/claim` and `/result` — a remote process can
  claim another pane's split intent and report an outcome that never happened.

The cross-origin write guard does not help: a non-browser client sends no
`Origin`, and `_reject_cross_origin_writes()` returns `None` for a request that
carries none.

Three module docstrings state a narrower bound than the code has:

- `web/ssh_tunnel.py`: "any process on that remote host that can reach the
  forwarded port can speak to GridVibe's MCP endpoint … the URL carries a
  per-pane token that is refused the moment the pane closes."
- `web/mcp_http.py`: "The bounds are: the token names one pane and is revoked
  when it closes …"
- `gridvibe_mcp/README.md`: "any process on that remote host that can reach the
  forwarded port can spend that pane's token, and the create tier acts as that
  pane."

Each reads as though the token were the gate. It is the gate on the tools; there
is no gate on the rest.

**Fix.** Either forward to a filter rather than to the app — a tiny in-process
listener that accepts only `POST /mcp/<token>` and closes anything else, with
`local_port` pointing at *it* — or move the MCP endpoint onto a second bound
socket that serves only that route, and forward that one. A `before_request`
check cannot do it alone, because the forwarded connection is indistinguishable
from a loopback one at the WSGI layer unless the filter marks it.

Until it is fixed, say the true bound in all three places: the reverse forward
exposes GridVibe's loopback HTTP API to processes on the remote host, and only
the tool surface is token-gated.

**Fixed — 2026-09-17.** The first of the two suggested shapes: the forwarded
channel reaches a filter, and `local_port` is opened only for a request that
gets through it. In `web/ssh_tunnel.py`:

- **One request per forwarded connection, and only this pane's own.** The
  channel is read as one HTTP request before any socket to GridVibe exists.
  It must be `POST`, `HTTP/1.1` or `HTTP/1.0`, and its target must equal this
  pane's `/mcp/<token>` exactly (`secrets.compare_digest`, on bytes). The
  token is now a parameter of `open_reverse_tunnel`, so the listener is opened
  *for* one path — a port opened for one pane can no longer spend another
  pane's token, which was the route finding 2 named. `mcp_path()` is the one
  spelling, and `tunnel_url()` builds the remote config from it, so the URL
  written on the remote host cannot name something the port would refuse.
- **Nothing rides behind the accepted request.** Exactly `Content-Length`
  bytes are read; anything after them is read into a buffer nobody forwards.
  `Transfer-Encoding`, a repeated `Content-Length` and obsolete line folding
  are each refused rather than normalised — a filter that cannot say where the
  body ends cannot say that nothing follows it. The forwarded head is rebuilt
  from the parts that parsed, with hop-by-hop headers dropped, `Content-Length`
  re-derived from the body actually read, and `Connection: close` stated, so
  GridVibe closes the socket when it has answered.
- **Bounded.** 16 KiB of head, 1 MiB of body, and a 30s read timeout on the
  request. The *reply* is deliberately not bounded by it — two tools wait on a
  page for as long as the store may take (25s when this was written; 40s since
  [finding 6](#6)).
- **Refusals say nothing.** 404 for a method or path that is not this pane's
  (the same answer either way, so a caller learns neither which routes exist
  nor whether it guessed a live token), 400 for framing, 413 for an oversized
  declared body, 502 when GridVibe itself cannot be reached. One sentence,
  naming no route. The warning log prints only the target's first segment:
  `/mcp/<token>` is a credential, and a legitimate client that got the verb
  wrong would otherwise have written this pane's own token into the log.

Two of the three docstrings that stated a narrower bound than the code had now
state what the code does — `web/ssh_tunnel.py`'s header and
`web/mcp_http.py`'s. `gridvibe_mcp/README.md` was left alone: its sentence is
about the token and was never false, and the file is the one finding 20
describes as four tools out of date.

Pinned by `ForwardedRequestFilterTestCase` in `tests/test_mcp_remote.py`, which
serves real channels into a stand-in for GridVibe's port and asserts on what
arrives there: the pane's own tool call is forwarded and answered, while eight
of the routes listed above — saved sessions, the destroy tier, the ungated
shell twin, the intent claim — are refused *before a socket is opened*, as are
another pane's token, the right path with the wrong verb, a smuggled second
request, chunked framing, a double `Content-Length`, a folded header, an
oversized body, a head that never ends, and a target that is not even text.

---

<a id="2"></a>
## 2. A pane token is never revoked when its pane closes — High

**Where:** `web/mcp_http.py` (`PaneTokenRegistry`), `web/terminal_io.py:1852`

`pane_tokens.revoke()` has exactly one caller in the codebase, and it is the
failure branch of `_establish_mcp_tunnel` — the tunnel could not be opened, so
the token that was just minted is dropped. No close path calls it.
`_shutdown_connection` pops `mcp_tunnel` and tears the tunnel down; the registry
entry survives, for the life of the process.

Two consequences.

**The stated bound is not kept.** `mcp_http.PaneTokenRegistry`'s docstring, the
`ssh_tunnel` header and the sidecar README all say the token is revoked when its
pane closes and refused once the pane is gone. `tests/test_mcp_remote.py:72`
(`test_closing_a_pane_revokes_its_token`) drives `registry.revoke()` directly —
it proves the registry method works, not that closing a pane reaches it.

**A dead pane's token still answers.** `handle_request` resolves the record, and
`dispatch` runs with an identity naming a session that no longer exists. What
that identity can still do:

| Tool | With a dead pane |
| --- | --- |
| `gridvibe_status`, `list_workspaces`, `list_panes`, `list_agents`, `list_saved_layouts`, `whoami` | all work |
| `create_workspace`, `open_window` | both work |
| `launch_panes` | refused — `resolve_origin_connection` raises on a closed origin |
| `split_pane` | works; the new pane gets no creator stamp (`_live_session_id` returns `""`) |
| `set_pane_agent`, `set_pane_mode`, `clear_pane` | refused — `check_caller` refuses a caller that is not open |

The remote listener is withdrawn with the pane, so the obvious route to the
token is gone. It is not the only one: the forwarded port of *any other*
tunnelled pane reaches the same `/mcp/<token>` (see finding 1), a shared remote
host keeps `~/.gridvibe/mcp-<id>.json` if teardown's SFTP delete failed, and any
local process on the GridVibe machine can post to it directly.

**And the registry only grows.** One entry per tunnelled pane ever opened, with
no TTL and no ceiling — unlike `WindowIntentStore`, which has both.

**Fix.** Call `pane_tokens.revoke(session_id)` where the pane's connection is
torn down (`_shutdown_connection`, or the session-close path that owns the
session id), and add a test that closes a pane and then asserts the token is
refused. A TTL or a ceiling on the registry would make a missed revoke a bounded
leak rather than a permanent one.

**Fixed — 2026-09-17.** Both halves.

- **The revoke.** `web/terminal_io.py` gained `_revoke_pane_mcp_token()`, and
  `_close_ssh_connection()` calls it after `_shutdown_connection()` — the close
  path that owns the session id, rather than `_shutdown_connection`, which does
  not. Every close reaches it: the delete-session routes, the workspace close,
  the close-all, the end of `_stream_ssh_output`, and the relaunch paths. A
  close that names a *different* connection (`expected=`) still returns before
  it, because that token belongs to the connection that replaced it. A
  relaunch therefore mints afresh, which costs nothing: its config is rewritten
  for the new port anyway, and `mint()`'s docstring no longer claims otherwise.
- **The ceiling.** `PaneTokenRegistry` takes `max_tokens` (`MAX_PANE_TOKENS =
  128`) and evicts the oldest entry rather than refusing a new one, so a live
  pane can always mint and a revoke that never runs is a bounded leak. It logs
  when it evicts, because reaching the ceiling means revoking stopped
  happening.

Pinned by `PaneCloseRevokesItsTokenTestCase` in `tests/test_mcp_remote.py`,
which drives the *real* `_close_ssh_connection` against a registered tunnelled
pane and then posts the token to the *real* `/mcp/<token>` route: 200 while the
pane is open, 404 and "Unknown or expired" after the close. Plus the relaunch's
new token, the `expected=` mismatch that must not disarm the live pane, a local
pane's close that touches nothing, and a bounded-registry case beside the
existing registry tests. The old
`test_closing_a_pane_revokes_its_token`, which drove `revoke()` directly, is
still there as the unit-level check it always was.

Finding 1's filter closes the cross-pane route named above, so a leaked token
is no longer reachable from another tunnelled pane's forwarded port.

**Not fixed here:** [finding 7](#7)'s race still exists — a close landing
between `_connection_status(CONNECTED)` and the `mcp_tunnel` assignment leaves
the remote listener, the config file and the SFTP channel behind. A close
landing *after* the mint now revokes that token, so the orphaned config names a
dead one; a close landing in the narrower window *before* the mint still leaves
a token nothing revokes, and the registry ceiling is what bounds it. Finding
7's re-check inside the lock is still the fix, and it is still open.

**Since closed.** [Finding 7](#7) was fixed later the same day, and its re-check
revokes the token minted in that narrower window too, so the registry ceiling is
a backstop rather than the bound.

---

<a id="3"></a>
## 3. The lineage and self gates rest on a variable the constrained agent controls — Medium

**Where:** `web/pane_gates.py`, `gridvibe_mcp/identity.py`, `gridvibe_mcp/server.py`

On the stdio path, the caller's identity is `GRIDVIBE_SESSION_ID`, read out of
the sidecar's own environment — which it inherits from the agent CLI, which
inherits it from the pane's shell. The agent CLI is the thing being constrained,
and it can set that variable to any value before the sidecar starts.
`list_panes` publishes every live `session_id`, so the values are not secret
either.

An agent that did so would present as pane B, and the gates would evaluate
against B: the self gate would stop it re-moding B, not its own pane, and the
lineage gate would admit every pane B created.

`identity.depth_budget()` states its equivalent weakness plainly ("A guardrail
against a runaway loop, not against an adversary: the agent it constrains can
unset the variable"). `pane_gates.py` does not — it reads as a boundary: "What is
*not* shared is the third gate…", "so a tool cannot reach past them".

The deeper point is that the gates were never a security boundary and cannot
be one. A local agent pane has the user's own privileges and GridVibe's API on
loopback: `DELETE /api/sessions/<id>` and `POST /api/sessions/<id>/shell` are one
`curl` away and pass no gate at all. What the gates actually buy is real and
worth keeping — an agent *following its instructions* does not end a pane it did
not make, and a prompt-injected agent has to leave the tool surface and start
constructing HTTP requests to get further. That is a meaningfully higher bar,
and it is not "cannot".

The token path (`_identity_for`) is not spoofable this way: the record comes from
the registry, not from the caller.

**Fix.** No code change; a wording change. State the caveat in `pane_gates.py`
and in the sidecar README's "Stated properties, not discoveries" section, beside
the depth budget's — same shape, same honesty.

**Fixed — 2026-09-17.** The wording, in `web/pane_gates.py`'s own docstring.
It now says that the gates constrain an agent following its instructions and
are not a security boundary; that both of them are evaluated against
`requested_by_session_id`, which is an input rather than a proof, and which on
the stdio path traces back to a variable the constrained CLI sets before the
sidecar starts; that an agent stating another pane's id would be gated against
*that* pane, self protecting it rather than its own; that the token path is not
spoofable that way but is the minority path and nothing below assumes it; and
that one level up a local agent pane has the user's own privileges and the
ungated twins of all three routes on loopback. It closes on what the gates do
buy, which is real. Same shape as `identity.depth_budget()`'s, which is what
made this one readable as a boundary by comparison.

`gridvibe_mcp/README.md` was left alone -- see the note at the top of this file.

Pinned by `StatedCallerTestCase` in `tests/test_pane_gates.py`, which turns the
caveat into behaviour rather than leaving it as a comment: a request stating
another pane's id moves the self gate onto that pane (so the caller's own
becomes reachable) and inherits that pane's whole lineage with no `override`
stated, beside a third case asserting what still binds an agent that states its
own id.

---

<a id="4"></a>
## 4. `make mcp-status` probes a route GridVibe has never had — Medium

**Where:** `utils/mcp_status.py:178`

```python
with urllib.request.urlopen(f"{url}/api/agents", timeout=PROBE_TIMEOUT) as reply:
```

There is no `/api/agents` route. `web/api.py` publishes `/api/health`,
`/api/dashboard`, `/api/sessions`, and the rest; a grep for `api/agents` across
the repository returns this one line. Flask answers 404, `urlopen` raises
`HTTPError` (a subclass of `URLError`), and the handler reports:

> `--   GridVibe    nothing is listening at http://127.0.0.1:5050 -- tools fail until GridVibe runs`

against a GridVibe that is running and answering. The check is a `NOTE`, so it
never fails the run — which is why it has gone unnoticed, and why it is
misleading rather than loud: the one line in the diagnostic that is supposed to
say "your server is fine" always says the opposite.

`tests/test_mcp_status.py` covers the down case (`check_reachable("http://127.0.0.1:1")`)
and the URL that gets probed, but never the up case, so nothing pins it.

**Fix.** Probe `/api/health`, which is the route the sidecar's own
`client.health()` uses and the one `open_window` reads `window_mode` from. Add
the missing test: a stub 200 reports `OK`.

**Fixed — 2026-09-17.** `utils/mcp_status.py` probes `PROBE_PATH`, which is
`/api/health` -- the route the sidecar's own `client.health()` calls and the
one `open_window` reads `window_mode` from, so a server that answers it is a
server the tools can use. Named as a constant so there is one spelling and a
test can reach it. `check_reachable`'s docstring now says why the route has to
be a real one: a 404 raises `HTTPError`, which is a `URLError`, which is the
same branch as no server at all -- and this check being a `NOTE` is what let
that read as normal.

Pinned by three cases in `tests/test_mcp_status.py`, against a real loopback
server publishing exactly one route: the up case reports `OK` (the case nothing
covered, which is why this went unnoticed for so long), a server publishing a
*different* route reports "nothing is listening" (the failure itself,
reproduced rather than described), and `PROBE_PATH` is asserted to be in the
real Flask app's URL map, so renaming the route breaks a test rather than the
diagnostic.

---

<a id="5"></a>
## 5. Over HTTP, `split_pane`/`open_window` hold a request thread for 25s and re-enter the server ~50 times — Medium

**Where:** `gridvibe_mcp/splits.py`, `gridvibe_mcp/windows.py`, `web/mcp_http.py:238-269`

Both verbs record an intent and then poll `GET /api/windows/intents/<id>` every
`0.5s` for up to `25s`.

On the stdio path that loop runs in the sidecar's own process and costs GridVibe
one cheap GET every half second. On the HTTP path `dispatch` runs **inside the
Flask request handler**, so the same loop:

- holds a Werkzeug worker thread for up to 25 seconds,
- issues up to 50 loopback HTTP requests from that thread *back into the same
  server*, each taking a second worker thread,
- and keeps the SSH tunnel channel (and its pump thread) open for the duration.

It works today because `socketio.run(..., async_mode="threading")` serves each
request on a new thread, so the re-entrancy has somewhere to go. That is a load-
bearing property nothing states: introduce a worker cap, a connection limit, or a
different async mode, and a single `split_pane` from a remote pane deadlocks
against itself.

`mcp_http.py`'s docstring says "The extra hop is one local request" — true for a
tool that makes one call, not for the two that poll.

**Fix.** Give the two intent-polling verbs a server-side path that waits on the
store rather than on HTTP — the store is in the same process, so a condition
variable replaces both the poll and the self-request. Failing that, record the
threading-mode dependency as a contract so it cannot be tuned away by accident,
and shorten the HTTP-path wait.

**Fixed — 2026-09-17.** The first of the two suggested shapes: the poll waits
on the store, and the self-request is gone rather than shortened.

- **The store can be waited on.** `WindowIntentStore`'s lock is a
  `threading.Condition` -- still the one lock every access already took, so
  `with self._lock` is unchanged everywhere -- and the two writes that move an
  intent (`claim`, `record_result`) notify it. `wait_for_settled()` blocks
  until the intent settles, is pruned, or the caller's timeout runs out, and
  answers exactly what `read()` would have answered at that moment, through the
  same `_public` projection.
- **The HTTP path uses it.** `web/mcp_http.py` hands `dispatch` a
  `GridVibeClient` subclass whose only override is `read_window_intent`.
  Subclassed rather than branched inside the tools, so `splits.py` and
  `windows.py` are written once and neither of them knows which transport it is
  serving; built lazily and once, because nothing under `web/` imports
  `gridvibe_mcp` at module scope.
- **The bound.** `INTENT_WAIT_SECONDS` is the store's own worst case
  (`INTENT_TTL_SECONDS + CLAIM_TTL_SECONDS`), so one wait ends when the store's
  answer is final rather than on a clock of its own. The sidecar's own deadline
  (finding 6) is longer still and remains the outer bound.

A remote `split_pane` now holds one worker thread and issues one loopback
request -- the intent itself, as it always did -- instead of up to fifty
re-entrant GETs each taking a second thread and each keeping the tunnel channel
busy. The undeclared dependency on `async_mode="threading"` is removed rather
than written down, and `mcp_http.py`'s docstring records what the shape was and
why it is no longer load-bearing.

Pinned by `InProcessIntentPollTestCase` in `tests/test_mcp_remote.py` -- the
poll answers with `urllib`'s opener patched to raise, it answers field for field
what `read()` does, it wakes on another thread's report rather than on a tick,
an id the store never had is expired rather than a block, and the whole
`split_pane` verb over this transport costs exactly one request -- and by
`WaitForSettledTestCase` in `tests/test_window_intents.py` for the store's own
six: an already-settled intent answers at once, a settling write wakes the
waiter, an unclaimed intent ends at its own expiry rather than the caller's
timeout, a claim extends what the waiter sleeps against, the timeout still
bounds it, and an unknown id is expired rather than a wait.

---

<a id="6"></a>
## 6. The sidecar can give up before the intent store does, and then claims nothing was touched — Medium

**Where:** `gridvibe_mcp/splits.py:42`, `gridvibe_mcp/windows.py:36`, `web/window_intents.py:31-35`

```
DEFAULT_WAIT_SECONDS = 25.0          # sidecar, both verbs
INTENT_TTL_SECONDS   = 15.0          # store: how long a page has to claim
CLAIM_TTL_SECONDS    = 20.0          # store: how long a claimant has to report
```

Both sidecar modules say the wait is "comfortably longer than the store's own
TTL so the expiry is the store's answer rather than a race between two clocks".
The store's worst case is not 15s, it is **35s**: a page may claim at t=14.9 and
still has until t=34.9 to report. Any claim after t≈5 that takes longer than
`25 − t` leaves the sidecar answering first.

When it does, `splits.py` returns `NO_PAGE_HINT`, which states a fact:

> The panes and the workspace are untouched.

That can be false. The page holds a valid claim, runs `splitTerminalPane()`, and
a pane appears that the agent has been told does not exist. `windows.py`'s
`FALLBACK_HINT` is safe by comparison — it only says the workspace exists.

The window is small in practice (a split is milliseconds once claimed), which is
exactly why it would be found the hard way.

**Fix.** Either raise the wait above `ttl + claim_ttl`, or reword the hint to
say what is actually known — the request expired without a page reporting back,
and it may still complete. The first is cheaper and keeps the stated invariant
true.

**Fixed — 2026-09-17.** The first of the two, in both `gridvibe_mcp/splits.py`
and `gridvibe_mcp/windows.py`: `DEFAULT_WAIT_SECONDS` is 40.0, above the
store's real worst case of 35s. The expiry is the store's answer again, so
`NO_PAGE_HINT`'s "the panes and the workspace are untouched" is true whenever
it is said. Both comments now state the sum and where it comes from -- 15s for
a page to claim, then the claimant's own 20s to report -- rather than "the
store's own TTL", which is the reading that made 25s look comfortable.

The sidecar cannot import `web/`, so the relation is pinned rather than
derived. `IntentWaitTestCase` in `tests/test_mcp_tools.py` asserts both
constants exceed `INTENT_TTL_SECONDS + CLAIM_TTL_SECONDS`, and drives both
verbs against a page that claims at 14.9s and reports at 34.9s: it settles
inside the new wait, and -- with `wait_seconds=25.0` passed explicitly -- still
reproduces the old answer, which told the agent the workspace was untouched
while the page went on to make the pane.

One residual, stated rather than designed away: a page that claims and then
takes longer than the claim TTL to report is outside the store's own guarantee
too (the store refuses its result), and the split can still have happened.

---

<a id="7"></a>
## 7. A close racing an SSH connect leaks a remote listener, a config file and an SFTP channel — Medium

**Where:** `web/terminal_io.py:_establish_mcp_tunnel` / `_shutdown_connection`

`_establish_mcp_tunnel` runs on the connect path after `_connection_status(CONNECTED)`.
It opens the reverse forward, writes the remote config over SFTP, and only then
stores the record:

```python
with connection_lock:
    connection["mcp_tunnel"] = record
```

It never re-checks that `connection` is still the current one. Every other write
on that path does: the resources insert a few lines above re-validates
`_connection_is_current(...)` inside the lock "so a concurrent close cannot slip
between the session check and the registry insert (which would leak the client)".
This is the same shape and the same leak.

A close landing between `_connection_status(CONNECTED)` and that assignment runs
`_shutdown_connection`, which pops an `mcp_tunnel` that is not there yet, sets
`retired`, and closes the client. The establish then completes and writes the
record onto a retired connection nobody will read again. What is left behind:

- an sshd listener on the remote host's loopback for the life of the transport,
- `~/.gridvibe/mcp-<session>.json` on the remote host, naming a live token,
- an open SFTP channel held only by the orphaned record,
- a token in the registry (see finding 2, which makes this one permanent).

**Fix.** Re-check inside the same lock hold and tear the record down if the
connection is stale:

```python
with connection_lock:
    if not _connection_is_current(session_id, connection) or connection.get("retired"):
        stale = record
    else:
        connection["mcp_tunnel"] = record
if stale:
    teardown(client, stale)
    pane_tokens.revoke(session_id)
```

**Fixed — 2026-09-17.** Exactly that shape. `_establish_mcp_tunnel` re-reads
the connection inside the same `connection_lock` hold that stores the record,
and treats both ways of having stopped being this pane's connection as stale:
`_connection_is_current` says no, or `retired` is set. A stale one is torn down
rather than recorded -- `ssh_tunnel.teardown` withdraws the listener, deletes
the remote file and closes the SFTP channel, and the token is revoked -- and a
teardown that raises does not cost the revoke.

Pinned by `EstablishTunnelTestCase` in `tests/test_mcp_remote.py`, which lands
the close *inside* `establish`, which is the window the race lives in: a pane
still open gets the record and no teardown; a close landing midway records
nothing, tears the record down once, and leaves the token unresolvable; a
connection replaced by a reconnect is stale the same way; and a teardown that
raises still revokes.

This also closes the window [finding 2](#2) left open. Every path out of
`_establish_mcp_tunnel` now either reaches a live connection or revokes, so a
token minted just after a close is revoked on the same path that withdraws the
tunnel, rather than standing until the registry ceiling evicts it.

---

<a id="8"></a>
## 8. An SSH pane whose agent has no MCP mechanism still opens a tunnel and mints a token — Medium

**Where:** `web/terminal_io.py:_establish_mcp_tunnel`, `web/agents.py:_agent_supports_mcp`

`_establish_mcp_tunnel` gates on two things: `session.agent_mcp` and
`initial_command_mode == "agent"`. It never asks whether *this* agent can be
handed a server. Three of the eight registered CLIs can (`claude`, `copilot`,
`codex`); for the other five `_agent_mcp_command_fragment` returns `""` and the
launch line carries nothing.

`agent_mcp` can be true for one of the five. The launcher hides the checkbox,
but the checkbox is not the only way in: `POST /api/sessions` takes
`agent_mcp` from any caller — including the sidecar's own `launch_panes`
(`{"kind": "agent", "agent": "grok", "mcp": true}`) — and
`apply_pane_shell_change` validates `mcp` only as a boolean
(`updates["agent_mcp"] = bool(mcp_enabled) and bool(resolved_agent)`), never
against `_agent_supports_mcp`.

The result is a reverse forward, a minted token, a file written on the remote
host and a widened surface, all for an agent that will never call it.

**Fix.** Ask `_agent_supports_mcp(agent_key)` before opening the tunnel, and
`and` it into `agent_mcp` in `apply_pane_shell_change` so the flag cannot be set
on a CLI that has no mechanism. That also makes the pane header's **MCP** tag
honest on such a pane.

**Fixed — 2026-09-17.** `_agent_supports_mcp` is asked at every write of
`agent_mcp`, not only at the tunnel, so the flag cannot be set on a CLI with no
mechanism by any route:

- **`web/saved_sessions.py`** (`_normalize_terminal_entries`) -- the one
  normalizer every launch body passes through: the launcher's own, an imported
  preset's, and the one `launch_panes` composes. Imported locally, because
  `web/agents.py` imports that module and the dependency can only run one way
  at import time.
- **`web/api.py`** (`_split_pane_overrides`) -- the other create route a tool
  reaches.
- **`web/session_shell.py`** (`apply_pane_shell_change`) -- the pane header's
  dropdown and `set_pane_agent`'s gated twin. `and`-ed in beside the existing
  "a pane with no agent cannot have MCP", and dropped silently for the same
  reason that one is.
- **`web/terminal_io.py`** (`_establish_mcp_tunnel`) -- the last place asked,
  and the only one with a cost attached. Such a pane now opens no port, mints
  no token and writes no file on the remote host, and the log says why.

The pane header's **MCP** tag paints off `agent_mcp`, so it is honest on such a
pane with no change to `agent-identity.js`.

Pinned by `McpFlagGateTestCase` in `tests/test_mcp_launch.py` -- the five CLIs
with no mechanism cannot carry the flag through a launch body, a split or a
relaunch, a custom agent cannot carry it either, and the three that do have a
mechanism still get it -- and by two cases in `EstablishTunnelTestCase`
(`tests/test_mcp_remote.py`): each of the five asks the remote host for nothing
(no `establish`, no SFTP, no record), and each of the three still gets its
tunnel.

---

<a id="9"></a>
## 9. `GridVibeClient.split()` is dead, and is the call `splits.py` exists to avoid — Low

**Where:** `gridvibe_mcp/client.py:504-510`

Nothing calls it — not `server.py`, not `splits.py`, not the suite. Splits reach
GridVibe through `split_intent()` and the intent poll.

It is worse than inert. `splits.py`'s header says:

> A process that posted straight to `/split` would get a pane with no geometry
> at all and would have consulted none of those rules.

`client.split()` is that post, sitting one method above `split_intent()` with no
comment saying not to use it. The next tool that wants a pane will find it first.

**Fix.** Delete it.

---

<a id="10"></a>
## 10. `PaneTokenRegistry.token_for()` is dead — Low

**Where:** `web/mcp_http.py:138-141`

No caller in `web/`, `sessions/`, `gridvibe_mcp/` or `tests/`. `mint()` is
already idempotent per pane, which is the need `token_for()` would have served.

**Fix.** Delete it, or keep it and give it the one caller it was written for.
(`clear()` is different — it is used by `tests/test_mcp_remote.py` as a fixture
reset and should stay.)

---

<a id="11"></a>
## 11. `IDENTITY_VARIABLES` claims a single source of truth it is not — Low

**Where:** `gridvibe_mcp/identity.py:16-22`, `web/mcp_launch.py:195-218`

```python
#: Every variable GridVibe injects at a local pane's spawn. Named once here so
#: the sidecar, the injector and the WSLENV forward list cannot drift.
IDENTITY_VARIABLES = (...)
```

Nothing imports it. `pane_identity_environment()` re-states all five names as
string literals, and `apply_pane_identity()` derives the WSLENV list from
`identity.keys()` — that is, from the injector's own dict, not from this tuple.
`read_identity()` re-states them a third time as literals.

`tests/test_mcp_identity.py:81` compares `IDENTITY_VARIABLES` against a local
fixture dict, not against the injector, so the two lists can diverge with a green
suite. The sidecar cannot import from `web/` (that boundary is load-bearing and
correct), so the tuple can only be a *published* list that the server side is
tested against.

**Fix.** Add the assertion that makes the claim true — `test_mcp_launch.py`
asserting `set(pane_identity_environment(session_id="x")) == set(IDENTITY_VARIABLES)` —
or soften the comment to say it is the sidecar's own reading list and the
injector is pinned to it by test.

---

<a id="12"></a>
## 12. The gated relaunch is the one write path that skips the agent-depth cap — Low

**Where:** `web/session_shell.py:501-507`

```python
caller = session_manager.get_session(request.caller_session_id)
return apply_pane_shell_change(
    session_id, relaunch, effects,
    {"agent_depth": int(getattr(caller, "agent_depth", 0)) + 1},
)
```

Two small things.

`_normalize_agent_depth` is not applied. `SessionManager.create_session` uses it
(`manager.py:979`) and the split route uses it (`api.py:3411`); `update_session_metadata`
is a raw `setattr` over an allowlist and normalizes nothing. `_MAX_AGENT_DEPTH = 64`
exists "so a hand-written snapshot cannot state a depth that overflows the
sidecar's own refusal arithmetic", and this path can write 65 and persist it to
`runtime_state.json`.

And `caller` can be `None`. `check_caller` proved the caller was open a few lines
earlier, but nothing holds it there. `getattr(None, "agent_depth", 0) + 1` is `1`,
so a caller that closed mid-call silently *resets* the chain's depth instead of
extending it.

**Fix.** `_normalize_agent_depth(...)` around the arithmetic, and treat a caller
that has gone as a refusal rather than as depth 0 — it is the same fact
`check_caller` already refuses on.

---

<a id="13"></a>
## 13. The `layout` enum advertises values GridVibe silently rewrites below four panes — Low

**Where:** `gridvibe_mcp/server.py:95` (`LAYOUTS`), `web/saved_sessions.py:138-146`

`_normalize_layout` honours:

| Panes | Accepted | Anything else becomes |
| --- | --- | --- |
| 1 | — | `single` |
| 2 | `vertical`, `horizontal` | `vertical` |
| 3 | `vertical`, `horizontal`, `split` | `vertical` |
| ≥4 | — | `grid` |

The schema offers all five values at every count, and the description says only
that the name is "Advisory above three panes: GridVibe forces 'grid' at four or
more". A three-pane launch asking for `grid` gets `vertical` with no warning, and
`build_launch_request`'s own default of `"split"` for a two-pane group is
rewritten to `vertical` on arrival.

The rewrites all land somewhere sensible, so nothing breaks — but the tool
describes the one case it does not fully cover and stays silent on the two it
does.

**Fix.** Say it in the description: below four panes the name is honoured only
where the count has a choice, and `workspace_layout` is how to place panes
exactly. Refusing client-side would be worse — the server is the owner of that
table.

---

<a id="14"></a>
## 14. A cross-workspace `list_panes` publishes the caller's own group's layout block — Low

**Where:** `gridvibe_mcp/client.py:339-393`

`panes(workspace_id=..., position_group_id=identity.group_id)` is what
`list_panes` sends when a workspace is named and a group is not. If that
workspace is not the caller's own, the geometry read still resolves the
**caller's** group, finds no matching session ids, stamps every pane `index: None`
(right), and then publishes `result["layout"]` for a group that is not in the
workspace being listed.

`LAYOUT_FIELDS` carries `group_id` and `workspace_id`, so a careful reader can
tell. A reader who takes `layout.layout` as "how this workspace's group is
arranged" is reading the wrong group's answer.

**Fix.** Publish the layout block only when at least one projected pane matched a
position, which is the same condition that already decides whether positions
were readable at all.

---

<a id="15"></a>
## 15. `create_workspace` is unbounded, and what it creates is never pruned — Low

**Where:** `gridvibe_mcp/server.py` (`create_workspace`), `web/api.py:2109`

The create tier is small because its blast radius is meant to be small.
`launch_panes` is capped by `terminal.max_sessions` and by the depth budget;
`split_pane` by the group cap; `create_workspace` by nothing. There is no
`max_workspaces` setting anywhere in the codebase.

A workspace created this way is marked `retain_when_empty=True` so cleanup can
tell it from one emptied by a close — so an agent in a loop leaves permanent,
empty workspaces in memory and in the workspace list every page renders.

**Fix.** A ceiling, or a TTL on a workspace that is still empty and was created
by a tool. The `WindowIntentStore`'s `MAX_INTENTS` is the precedent — "a sidecar
in a retry loop cannot grow the store without bound" is the same sentence.

---

<a id="16"></a>
## 16. Every tool-launched terminal pane is a *stated* PowerShell pane — Low

**Where:** `gridvibe_mcp/server.py:625-627`

```python
shell = _choice(_text(pane, "shell"), SHELL_KINDS, "shell", "powershell")
request["use_powershell"] = shell == "powershell"
request["use_wsl"] = shell == "wsl"
```

An omitted `shell` is not "leave it to GridVibe" — it is written into the launch
body as a chosen PowerShell pane, and saved as one. A user whose local panes are
cmd gets PowerShell from every agent launch, with nothing having asked.

The same default is right for a remote group only by accident:
`resolve_origin_connection` strips the local shell family before it reaches the
pane, and says so.

**Fix.** Omit the two keys when `shell` is unstated and let GridVibe's own
default apply, exactly as `build_split_pane_request` already does for `kind`
("an omitted `kind` means 'do what the button does'").

---

<a id="17"></a>
## 17. One transient poll failure reports a split that may have happened as a failure — Low

**Where:** `gridvibe_mcp/splits.py:90`, `gridvibe_mcp/windows.py:148`

`client.read_window_intent()` raises `GridVibeError` on any transport failure,
and neither poll loop catches it. It unwinds through `split_pane`/`open_window`
into `dispatch`, which turns it into `{"error": ..., "kind": "unreachable"}`.

The intent is still pending, a page may still claim it, and the split still
happens — the agent has been told the call failed. `pane_layout()` two files over
takes the opposite decision for the same reason and documents it: "A failed read
is not a failed `list_panes` … the geometry read degrades to an empty answer
rather than raising through its caller."

**Fix.** Swallow a failed poll and keep waiting until the deadline; let the
deadline produce `no_window_available`, which is the honest answer for "I never
saw it settle".

---

<a id="18"></a>
## 18. The streamable-HTTP endpoint implements the POST half only — Low

**Where:** `web/api.py:4158`, `web/mcp_http.py`

`@app.route('/mcp/<token>', methods=['POST'])`. Not implemented: the `GET` SSE
stream, `DELETE` for session termination, `Mcp-Session-Id`, and any validation of
`Origin` or `Accept` — which the transport spec asks servers to do, `Origin`
specifically as DNS-rebinding protection.

The three CLIs that get a URL today are content with request/response, so nothing
is broken. It is worth recording as a known shape rather than discovering it when
a CLI opens the stream first and reads a 405.

**Fix.** None needed now. State in the sidecar README that the endpoint is the
POST half of streamable HTTP, and answer `GET`/`DELETE` with a deliberate 405
rather than Flask's default.

---

<a id="19"></a>
## 19. `whoami` says an agent may not launch panes without saying it may still split one — Low

**Where:** `gridvibe_mcp/server.py:903-906` and `938-947`

At the depth limit `whoami` publishes `may_launch_panes: false` and a refusal
sentence. `split_pane` is more permissive on purpose, and correctly so: only a
split that *creates an agent* costs budget, and a plain terminal split is still
allowed.

An agent reading `whoami` alone concludes it can create nothing.

**Fix.** One more field — `may_split_panes: true` alongside it, or a clause in
the refusal saying a plain split is still available.

---

<a id="20"></a>
## 20. Documentation drift — Info

### `gridvibe_mcp/README.md`

Written at nine tools; the build registers thirteen. Missing entirely:
`list_saved_layouts`, `set_pane_agent`, `set_pane_mode`, `clear_pane`, the
`override` flag and the `web/pane_gates.py` rules behind all three of the last
ones, and the split-intent mechanism (`splits.py` / `web/window_intents.py`).

One line is now actively wrong. Under **absent**:

> closing a pane, a group or a workspace; **switching a pane's mode or shell**;
> moving a group; typing into a terminal. These are not written, not registered,
> and not flag-gated.

Switching a pane's mode and shell are both registered tools.

The scope note ("Phase 0 covers Windows-native local panes") predates SSH panes,
which the same file documents two sections further down.

### Nothing links it

`gridvibe_mcp/README.md` is not referenced from `README.md`, `CLAUDE.md`,
`AGENTS.md` or `docs/engineering_contracts.md`. The root `README.md` describes the
feature in three user-facing bullets (which is the right altitude for it) but
offers no way through to the detail.

### `CLAUDE.md`

The repo layout omits, among the MCP-relevant files: `web/pane_gates.py`,
`web/session_clear.py`, `web/window_intents.py`, `web/pane_geometry.py`,
`gridvibe_mcp/splits.py`, `web/static/js/window-intent.js`, and every MCP test
file (`test_mcp_tools.py`, `test_mcp_client.py`, `test_mcp_identity.py`,
`test_mcp_launch.py`, `test_pane_gates.py`, `test_session_clear.py`,
`test_window_intents.py`, `test_window_intent_client.py`). The Contract
References table has no row for the agent tool surface.

Non-MCP omissions noticed in the same pass, listed so they are not lost:
`web/static/js/dashboard-sidebar.js`, `web/static/css/agent-brand.css`,
`web/static/css/agent-dashboard-sidebar.css`,
`templates/partials/agent_dashboard_sidebar.html`.

### `docs/engineering_contracts.md`

MCP appears in three places — a paragraph under *Pane transitions*, two under
*Agent dashboard*, one line under *Architecture and extraction boundaries* —
and all of them are accurate. What is absent is a section that owns the tool
surface itself: the tiers, the three gates and `override`, the depth budget,
the field allowlists, the token's lifetime, the intent mechanism, and where
those rules live.
