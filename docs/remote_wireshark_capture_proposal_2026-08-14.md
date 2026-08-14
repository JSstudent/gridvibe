# Open in Wireshark — Live Remote Capture from an SSH Pane

Status: Drafted 2026-08-14. Not implemented; nothing in this document describes
shipped behaviour. Findings marked **verified** were measured on the drafting
machine (Windows 11, Wireshark 4.6.0) and are reproducible; everything else is
design intent.

## Goal

Add one pane-header button to a live SSH terminal session that opens the host's
installed Wireshark on a **live capture of the remote host's traffic** — the
packets arriving on the far end of the SSH connection, rendered in the local
Wireshark GUI in real time.

Not in scope: capturing the local machine's own traffic (Wireshark already does
that unaided), opening `.pcap` files from an explorer pane, or any capture
analysis inside GridVibe.

## Constraints This Proposal Holds Itself To

- No new Python dependency. In particular no `pywin32` — it is not in
  `requirements.txt` today and a named-pipe implementation is the only reason
  it would be needed.
- No credential ever reaches the page, a command line, or a log. The lifecycle
  contract already forbids returning or logging the credential snapshot; this
  feature must not open a side door.
- No new vendored frontend asset, no bundle step.
- No growth of `web/api.py` or `terminals.js` beyond a route stub and a button
  (Regression Guardrail 6).
- The button is absent, not broken, when Wireshark is not installed.

## Precedent in the Codebase

This is not an unprecedented category. `POST /api/explorer/<id>/reveal`
(`web/api.py:1422`) → `open_path_in_os_file_manager()` (`web/explorer.py:863`)
is already "launch a host OS application on behalf of a pane":

- argv list, never a shell string, so a resolved path cannot inject commands
- fire-and-forget `subprocess.Popen`, because the launched app's exit code is
  not ours to interpret
- restricted to the pane kind where it is meaningful (local only — a remote
  path means nothing to the server's file manager)
- non-mutating, and documented as sitting outside the explorer's read-only
  browsing contract rather than as an exception to it

A Wireshark button inherits all four properties. It differs in one way that
matters and is treated as its own section below: reveal launches an app and
forgets it, while a capture is a **long-lived stream with a remote process on
the other end** that has to be stopped.

## What Is Actually Installed (Verified)

Probed on the drafting machine:

| Item | Result |
| --- | --- |
| Wireshark | 4.6.0, `C:\Program Files\Wireshark\Wireshark.exe` |
| On `PATH`? | **No.** `Get-Command wireshark` resolves nothing, so `shutil.which()` will not find it either |
| `extcap/` contents | `etwdump.exe` only — **`sshdump.exe` is absent** |
| Bundled tools | `dumpcap.exe`, `tshark.exe`, `editcap.exe`, … (full standard set) |
| OpenSSH client | `C:\WINDOWS\System32\OpenSSH\ssh.exe` |

Two consequences drive the whole design. Detection cannot rely on `PATH`, and
the approach that would have required the least code from us is unavailable out
of the box.

## Approaches Considered

### A. Wireshark's `sshdump` extcap — rejected

Wireshark ships an extcap plugin that performs remote capture natively:
Wireshark SSHes to the host, runs `dumpcap`/`tcpdump` there, and reads the pcap
stream itself. Invocation is roughly

```
wireshark -i sshdump --remote-host=H --remote-port=22 \
          --remote-username=U --remote-interface=eth0 -k
```

Zero capture code on our side. Rejected on three counts:

1. **It is not installed** (verified above). SSHdump is an optional component in
   the Wireshark installer. We cannot ship it, and telling users to re-run the
   Wireshark installer to light up a GridVibe button is a dependency we should
   not take on for a convenience feature.
2. **Password auth leaks the credential.** `--remote-password` places the SSH
   password in the process command line, readable by any local user. That
   directly contradicts the constraint above.
3. **Key auth cannot be expressed.** `sshdump` wants `--sshkey <path>`.
   GridVibe never records *which* key paramiko negotiated — `connect()` is
   called with `look_for_keys`/`allow_agent` and lets paramiko choose
   (`web/terminal_io.py:973-981`). We have no path to hand over.

Worth keeping as an opportunistic fast path only if detection finds
`extcap/sshdump.exe` *and* the session authenticated by key — but that is a
second code path guarding a minority case, and is deferred out of v1.

### B. Local `ssh.exe` pipeline — rejected

```
ssh user@host "tcpdump -w -" | wireshark -k -i -
```

Rejected: it re-authenticates outside GridVibe's connection handling, has no
non-interactive password path on Windows (`sshpass` is POSIX-only), and would
either prompt in a window we do not own or silently hang. It also duplicates
auth logic paramiko already owns, against Guardrail 6.

### C. paramiko → Wireshark stdin — recommended

Run the capture on the channel GridVibe is already authenticated on, and feed
the bytes to a locally spawned Wireshark through its standard input.

```
   paramiko exec_command("tcpdump -i any -U -w - <filter>")
        │   (a new channel; auth already solved at pane launch)
        ▼
   reader thread  ── chunked copy, no full-buffer copies (Guardrail 3)
        │
        ▼
   subprocess.Popen([wireshark, "-k", "-i", "-"], stdin=PIPE)
```

**Verified**: Wireshark's capture-from-stdin path works on this machine.
Piping a pcap into the bundled `tshark.exe -i -` yields

```
Capturing on 'Standard input'
1 packet captured
```

`Wireshark.exe -k -i -` drives the same dumpcap machinery, so the GUI is
expected to behave identically — **this specific claim is inferred, not
measured**, and is the first thing a prototype should confirm.

Why it fits GridVibe:

- **No new dependency.** `subprocess.Popen(stdin=PIPE)` is sufficient on all
  three platforms. No named pipe, therefore no `pywin32`, therefore no
  platform-specific IPC code.
- **Credentials stay in-process.** The page posts a session id and nothing else.
- **Auth mode is irrelevant.** Password, key, and agent sessions all work,
  because auth was settled at pane launch.
- **Stock Wireshark is enough.** No optional installer component.

### Comparison

| | A. sshdump | B. local ssh pipe | C. paramiko → stdin |
| --- | --- | --- | --- |
| Works with stock install | **No** (verified) | Yes | Yes |
| Credential exposure | Command line | Prompt / hang | None |
| Key-auth sessions | Unsupported | OS-resolved | Works |
| New dependency | None | None | None |
| Capture code we own | None | None | Reader thread + teardown |
| Reuses GridVibe auth | No | No | Yes |

## Design Sketch

### Backend

New module `web/capture.py`. It does not belong in `web/api.py` (Guardrail 6),
and it is not explorer functionality, so it does not belong in `web/explorer.py`
either.

Responsibilities:

- `find_wireshark()` — locate the GUI binary with a result cache, following the
  detect-and-cache pattern in `web/agents.py`. Because `PATH` is unreliable
  (verified), probe in order: `shutil.which`, then `%ProgramFiles%\Wireshark`
  and the `HKLM\SOFTWARE\Wireshark` install path on Windows,
  `/Applications/Wireshark.app/Contents/MacOS/Wireshark` on macOS, and the usual
  `/usr/bin` / `/usr/local/bin` locations elsewhere.
- `start_capture(session, options)` — open the channel, spawn Wireshark, start
  the reader thread, register the capture.
- `stop_capture(session_id)` — see teardown below.
- A registry of live captures keyed by session id, with its own lock. It must
  **not** reuse `connection_lock`, and nothing slow may run while holding it
  (Guardrail 2).

Routes in `web/api.py`, thin, delegating:

| Route | Purpose |
| --- | --- |
| `GET /api/session/<id>/capture/status` | Whether Wireshark was found, whether a capture is live |
| `POST /api/session/<id>/capture/start` | Begin a capture |
| `POST /api/session/<id>/capture/stop` | End it |

Rejected with `400` for any session whose `mode` is not `ssh`, mirroring the
reveal route's local-only refusal.

### Which SSH transport

Two options, and the choice should be deliberate:

- **Reuse the pane's live transport** (`ssh_connections[session_id]`,
  `web/terminal_io.py:1003`) by opening a second channel. Cheap, and the same
  reasoning the SFTP pool uses (`_PooledSSHClient`, `web/explorer.py:2837`).
  Risk: a high-rate capture and the interactive shell then share one TCP
  connection, and the firehose can starve keystroke echo.
- **Open a dedicated connection for the capture.** Costs one handshake per
  capture — which is *not* the per-request handshake Guardrail 3 prohibits,
  since a capture is a single long-lived operation — and keeps terminal
  responsiveness independent of capture volume.

Recommendation: dedicated connection. The guardrail targets per-click
handshakes; one handshake per explicit user-initiated capture is proportionate,
and terminal responsiveness is the thing users will notice.

### Frontend

- Button in the `.terminal-actions` block of `buildPaneCard()`
  (`web/static/js/terminals.js:5120`), rendered only when
  `session.mode === 'ssh'` and the status probe reported Wireshark present —
  the same conditional-render shape the existing `explorer-os-open` button uses
  for local-only panes.
- Icon: a stroke-style `currentColor` SVG in `terminal-icons.js`; no emoji, no
  text glyph (Guardrail 7). Colours from `tokens.css`.
- Busy and active states toggle CSS classes; button markup is never rewritten
  (Guardrail 8).
- Any confirmation or option dialog uses `openGenericConfirmModal(...)` from
  `shared.js`. Never `window.confirm` — WebView2 blocks it, and the guardrail
  covers browser-mode-only paths too (Guardrail 4).
- Controller logic lives in its own file (`web/static/js/capture.js`), not in
  `terminals.js` (Guardrail 6).

## Open Decisions

These are the parts that make this a feature rather than a one-liner. Each
needs an explicit choice before implementation.

### 1. Privileges

`tcpdump` requires root. `TerminalSession.username` defaults to `"root"`
(`sessions/manager.py:55`), so the common case is fine — but for any other user
`sudo` is required, and an `exec_command` channel has no tty, so a sudo
password prompt **hangs silently** rather than failing.

Options: `sudo -n` and fail fast with an actionable message when passwordless
sudo is not configured; or feed the stored session password to `sudo -S` on the
channel's stdin. The second works more often and keeps the credential
in-process, but writes it into a remote process's stdin — acceptable, since the
same password already authenticated the session, but it should be a conscious
decision rather than a default that happens.

### 2. The feedback loop — mandatory, not optional

A capture with no filter captures the SSH transport carrying the capture. Each
captured packet generates traffic, which is captured. This amplifies without
bound and will saturate the link within seconds.

The BPF filter must always exclude the session's own connection, and must use
`session.port` rather than a hardcoded `22` — the session model carries a
configurable port (`sessions/manager.py:56`). A floor of `not port <session
port>` is the minimum; excluding the specific host/port pair is tighter and
still cheap.

This is the single most important correctness item in the proposal.

### 3. Stopping the capture

Closing the local pipe does not reliably terminate remote `tcpdump`; a
half-stopped capture leaves a root process writing to a dead socket on the
user's server.

Needs, in combination: `get_pty=True` on the exec so that channel close signals
the process group; an explicit stop route; teardown wired into session close and
into the lifecycle close/restart path; and a reader thread that exits on both
pipe-closed and channel-EOF. The registry must be reconciled when Wireshark is
closed from its own window — the user will do that, and it should not leave a
stale "capture running" state in the UI.

### 4. Detection UX

Wireshark not being on `PATH` is the normal Windows case (verified), so naive
`which`-only detection would hide the button on nearly every Windows install.
Detection failure should hide the button, never produce a click that fails.

A `capture.wireshark_path` override in `default_config.json` — reached through
`RuntimeConfig` like every other config key (Guardrail 5) — covers non-standard
install locations without a code change.

### 5. Remote-side preflight

`tcpdump` may not exist on the remote host, and the failure should be reported
before Wireshark is spawned rather than as an empty capture window. One
`tcpdump --version` probe, the same shape as the existing agent preflight in
`web/agents.py`.

### 6. Interface and filter selection

"Just capture" means `-i any`, which on a busy production host is a firehose
sharing the user's link. A minimal dialog offering an interface (enumerated
with `tcpdump -D`) and a BPF filter field is what separates a usable feature
from one whose first click on a real server is unpleasant.

This is the main scope question: v1 can ship without it, but should not ship
without acknowledging it.

## Security Posture

If built, `CLAUDE.md`'s security-posture section needs a sentence: the feature
streams a remote host's network traffic to the local machine on explicit user
action. That is consistent with GridVibe's documented local-use, single-user
posture, but it is a new *category* of data crossing the SSH boundary and
should be stated rather than left implicit.

Properties to preserve:

- argv lists throughout; no shell string ever built from user input
- the remote command is assembled server-side, with the interface name and BPF
  filter validated against a conservative pattern before interpolation — a
  filter field is a command-injection surface if it is ever concatenated into a
  shell string
- logging stays shape-only: session id, interface, byte counts, failure
  category. Never the filter contents, never packet data, never credentials
- no capture data is persisted by GridVibe; bytes pass through memory to
  Wireshark's stdin and are never written to disk by us

## Suggested Staging

**Stage 1 — minimum viable.** Detection with cache and config override, status
route, start/stop routes, dedicated SSH connection, `-i any`, mandatory
session-port exclusion filter, `sudo -n` with a clear failure message, button
hidden when Wireshark is absent, teardown on session close. No dialog.

**Stage 2 — usability.** Interface enumeration and BPF filter dialog via
`openGenericConfirmModal`, remote `tcpdump` preflight, live capture indicator on
the pane header, reconciliation when Wireshark is closed externally.

**Stage 3 — opportunistic.** Use `sshdump` when it is present *and* the session
authenticated by key, skipping our reader thread entirely. Only worth doing if
Stage 1 reveals throughput limits in the Python relay.

## Testing Notes

Prefer behavioural tests; no raw source-text assertions (Working Rules).

- Detection: probe order and cache behaviour against a stubbed filesystem,
  including the verified "installed but not on `PATH`" case.
- Route guards: non-SSH session → `400`; unknown session → `404`; Wireshark
  absent → status reports absent rather than the route failing.
- Filter construction: a session on a non-default port produces an exclusion
  filter naming *that* port. This is the regression test for the amplification
  loop and is the one that must not be skipped.
- Teardown: closing the session stops the capture and clears the registry;
  channel EOF stops the reader thread.
- Injection: interface and filter inputs containing shell metacharacters are
  rejected, and no shell string is constructed.
- Frontend (Node, DOM-free where possible): button visibility rules, busy-state
  class toggling, no `window.confirm` on any path.

## Documents to Update if Shipped

`README.md` (user-visible feature), `CHANGELOG.md`, and the `CLAUDE.md`
security-posture note described above. This proposal is not a maintained
contract — if the feature ships, the contract belongs next to the code that
enforces it.
