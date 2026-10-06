# tmux-backed SSH terminals: feasibility and design

Status: analysis against the code on 2026-10-06 (branch
`szua_gridvibe-wrk-taper`). Nothing implemented. Once built, the resulting rules
move into `docs/engineering_contracts.md` and `docs/session_state_guideline.md`,
and this note is archived.

## Verdict

Feasible and small, because the scope is deliberately narrow.

tmux is an **experimental, advanced launch option** for remote SSH panes. It
exists only while an experimental App Settings switch is on (see
[The experimental setting](#0-the-experimental-setting)). A pane launched with
it runs inside a named tmux session on the remote host, and the **only** thing
GridVibe restores for that pane is the session itself: after a restart, a
reconnect or a restore, the pane attaches to the same tmux session.

GridVibe preserves nothing else about a tmux pane:

- no agent, agent options, MCP grant or conversation id
- no pane mode (terminal, explorer, browser)
- no observed working directory, and no `cd` replayed. The launch directory is
  kept, but only as the place to recreate the session if it has gone
- no startup command

Whatever runs inside the tmux session (a shell, an editor, an agent the
developer started) keeps running because tmux keeps it running, not because
GridVibe remembered it. GridVibe treats a restored tmux pane as a plain
terminal attached to that session.

The pane's controls stay the same as on any SSH terminal pane. A few of them
behave differently inside tmux; see [Pane controls](#pane-controls).

That narrow scope is what keeps it small: the MCP tunnel's lifetime,
conversation resume, task handoffs and agent detection after restore are all out
of scope, because none of them is restored.

## How SSH panes work today

- **Connect** (`web/terminal_io.py`, `_connect_ssh_session`): paramiko connects,
  opens a shell with `client.invoke_shell(term='xterm', width, height)`, sets up
  the MCP tunnel (`_establish_mcp_tunnel`), then runs `_run_startup_sequence` and
  starts streaming output (`_stream_ssh_output`).
- **Startup sequence** (`_run_startup_sequence`): types into the remote shell,
  in order: the working-directory prompt hook
  (`remote_shell_integration_command()`, `web/terminal_cwd.py`), a `cd` to the
  pane's last seen directory (`_startup_directories`), an invisible marker that
  hides the echo of those two lines (`_arm_ssh_startup_scrub`), and for agent
  panes the agent launch line with any handoff or conversation resume.
- **Restore** (`web/runtime_state.py`, `_SESSION_SNAPSHOT_FIELDS`): replays the
  pane's launch fields. `session_id` is not saved, so a restored pane gets a new
  id and a new shell.
- **Reconnect** (`web/api.py`, `reconnect_session`): drops the old connection
  and runs the same connect path again.
- **Close, mode switch, relaunch** (`_shutdown_connection`,
  `web/session_modes.py`, `web/session_shell.py`): closing the channel sends the
  remote shell SIGHUP and it exits. A relaunch then connects a fresh shell.

## Design

### 0. The experimental setting

The whole feature sits behind one switch, built the same way as the
experimental "Resume agent conversations on restore" setting
(`workspace.agent_conversation_restore`, gated by
`conversation_restore_enabled()` in `web/agent_conversations.py`).

- **Config key:** `ssh.tmux_sessions`, `false` in `default_config.json`. It sits
  beside the other SSH settings, so it travels in the same
  `runtime_config.snapshot().ssh_config` generation the connect path already
  captures once per connection.
- **Gate:** one function, `tmux_sessions_enabled(settings=None)` in
  `web/ssh_tmux.py`, read from a captured settings snapshot. Every consumer asks
  it; nothing reads the key directly.
- **UI:** a checkbox in App Settings → Terminal, "Run SSH terminals in tmux",
  with the existing `settings-experimental-badge` and one line of help text.
  Wired through `web/config.py` validation, the settings GET and PUT in
  `web/api.py`, and `web/static/js/app-settings.js`, as the conversation-restore
  switch is.

When the switch is **off**, SSH panes behave exactly as they did before the
feature existed:

- The launcher does not show the tmux fields.
- The session API ignores a `tmux_session` field in a request, as it does for
  any field it does not know.
- Every connect opens a plain shell, even for a pane that has a name.
- A capture writes no `tmux_session`, so a pane saved while the switch is off
  restores as an ordinary SSH terminal pane in its launch directory.
- No tmux session on any host is ever killed by switching it off. The sessions
  stay where they are, and switching the setting back on before the next
  capture lets their panes attach again.

A pane that is attached when the switch is turned off stays attached until its
connection is replaced, because the gate is read at connect time, not
continuously. The "Also end the tmux session" choice on close follows the live
connection, not the switch, so a still-attached session can always be ended.

### 1. The launch option

In the launcher's advanced SSH options, shown only while the setting is on:

- **"Run in tmux"** (checkbox, off by default).
- **"tmux session"** (optional plain text field). Empty means GridVibe
  generates a name such as `gv-<12 hex chars>`. A developer can instead type the
  name of a session that already exists on the host (for example their own
  `work` session) to attach to it. GridVibe does not list the host's sessions;
  a text field is enough for an advanced option.

The option applies to SSH panes started in terminal or agent mode. Explorer and
browser panes have no terminal and do not offer it.

Validation:

- Allowed characters are `[A-Za-z0-9_-]`. tmux reserves `.` and `:` in target
  names, and a name is passed to a remote shell, so anything else is refused at
  launch and dropped on restore.
- Every tmux command targets the session as `-t =NAME`. The `=` forces an exact
  match. Without it tmux matches by prefix, so `gv-ab` would hit `gv-abc`.
- One live GridVibe pane per (host, port, user, name). A second launch naming a
  session another pane is already attached to is refused. tmux would allow it,
  but two panes mirroring one session fight over its size and make restore
  ambiguous.

### 2. What is stored

A new `TerminalSession` field, `tmux_session`, holds the name. Empty means the
pane is not a tmux pane.

The runtime snapshot of a tmux pane carries only:

- the connection identity: `host`, `port`, `username` (credentials are handled
  exactly as for any SSH pane today; nothing new is stored)
- `tmux_session`
- `launch_directory`, written into both `directory` and `launch_directory`
- the pane's `title`, if the developer set one

Everything else is written as its default, so a tmux pane always restores as an
SSH terminal pane with no startup command, no agent fields and no conversation
id. `_snapshot_session` (or the capture helper it uses) has one branch for this.

**Why the launch directory, and only that one.** It is the directory the
launcher chose and it never moves (`TerminalSession.launch_directory`), so it is
already known and costs nothing to store. Its only use is as the starting
directory when the session has to be created: on the first launch, and on a
restore that finds the session gone (for example after the host rebooted). On an
attach it is ignored. The *observed* directory is deliberately not stored: it
cannot be observed through tmux without a remote round trip, and replaying it is
exactly the kind of state this feature leaves to tmux. A launch directory that no
longer exists on the host is handled in the create step below, so storing it
carries no risk.

Reusable presets: a preset saved from a tmux pane keeps the **option** but not
the generated name, so each launch of the preset gets its own session. A name
the developer typed is kept, because naming a known session is the point of
typing it. (Two launches of such a preset run into the one-pane-per-name rule
above, which is the right outcome.)

### 3. Connect: check, create if needed, attach

For a tmux pane, `_connect_ssh_session` connects and sets up the MCP tunnel as
today, then does two things instead of `invoke_shell`.

**One exec channel decides attach vs create.** It uses the same bounded pattern
as `_remote_process_cwd`: its own channel, a timeout and a capped read.

```sh
command -v tmux >/dev/null || exit 3
tmux has-session -t =NAME 2>/dev/null && exit 0
if [ -n "$DIR" ] && [ -d "$DIR" ]; then
  tmux new-session -d -s NAME -c "$DIR" && exit 10
else
  tmux new-session -d -s NAME && exit 11
fi
```

| Exit | Meaning | Next |
| --- | --- | --- |
| 3 | tmux is not installed, or not on the non-interactive `PATH` | plain shell as today, plus a yellow notice in the pane |
| 0 | the session exists | attach |
| 10 | GridVibe created the session in the launch directory | type the startup command (if any), then attach |
| 11 | GridVibe created the session in the home directory, because the launch directory is empty or missing on the host | as 10, plus a notice naming the missing directory when one was set |
| other | tmux failed | pane goes to ERROR with tmux's message |

- `DIR` is the pane's `launch_directory`, on the first launch and on a restore
  alike, so both create paths are the same code.
- The `[ -d ]` check runs on the host, in the same round trip, so a missing
  directory costs nothing extra and never makes the create fail. An agent pane
  that lands in the home directory this way does **not** get its launch line,
  matching today's rule that an agent is never started somewhere the reader
  did not ask for (`_run_startup_sequence`, "is not available").
- `exec_command` runs through the user's non-interactive shell, whose `PATH`
  may miss the directory tmux is installed in (`/usr/local/bin`, Homebrew on
  macOS). That is the likeliest cause of a wrong exit 3, and the notice should
  say so.
- `NAME` and `DIR` go through `shlex.quote`.

**The PTY channel attaches.** `get_pty(term, width, height)` then
`exec_command("tmux attach-session -t =NAME")`. Resizing through
`channel.resize_pty` works unchanged. The tmux client is the channel's root
process: when the developer detaches, or the session's last shell exits, the
channel closes and the existing `_stream_ssh_output` → `_finalize_stream` path
marks the pane DISCONNECTED. No new code is needed for that.

### 4. Startup typing

| Case | What GridVibe types |
| --- | --- |
| Attach to an existing session | **nothing** |
| Session created by this connect, terminal mode | **nothing** |
| Session created in the launch directory (exit 10), agent mode | the agent launch line, sent with `tmux send-keys -t =NAME -l` before attaching |
| Session created in the home directory (exit 11), agent mode | **nothing**, plus the notice |

- No prompt hook and no `cd` in any case. The directory comes from tmux's `-c`,
  and the hook's escape sequences would not get through tmux anyway (see
  [Working directory](#working-directory)). That also means no echo scrub is
  needed.
- An agent pane that *attaches* (the developer named an existing session) does
  not get its launch line, because it would be typed into whatever is running
  there. The pane says so in a notice.
- Sending the launch line with `send-keys` before attaching avoids racing the
  new session's shell startup.
- The agent launches with everything an agent pane gets today (MCP sidecar,
  handoff, conversation tracking) for the life of that connection. None of it
  is stored for a tmux pane, so after a restart the pane comes back as a plain
  terminal attached to the session, and an agent still running in it has lost
  its GridVibe tools (its tunnel went with the old connection). That is the
  accepted limitation of this scope.

### 5. Pane controls

The controls stay the same. What they do on a tmux pane:

| Control | On a tmux pane |
| --- | --- |
| Reconnect | attach |
| Close pane | **detach** by default. The close dialog adds "Also end the tmux session", which runs `kill-session -t =NAME` over an exec channel before the transport closes |
| Close workspace, quit GridVibe | detach |
| Switch to Files or Browser | detach, and the pane keeps its `tmux_session`. Switching back to Terminal attaches again; it does not open a fresh shell at the explorer's folder |
| Relaunch as agent, change agent, relaunch at a directory | today these need a fresh shell. On a tmux pane they open a **new tmux window** in the same session (`new-window -t =NAME -c <dir>`), and the launch line is sent to it. The session is never ended for this, and the developer's other windows are untouched. `<dir>` is the stated directory, or the pane's current one from `pane_current_path`, with the same on-host `[ -d ]` check as the create step |
| Clear | as today. It clears the screen inside tmux, not tmux's own history |
| Split | the new pane gets the tmux option with a newly generated name, never the source pane's session |
| Broadcast typing | as today: input goes to the attached channel |

Detach-by-default matters because the developer may have attached their own
long-lived session; closing a pane must never destroy it unasked.

A pane switched to Files or Browser still restores as a tmux terminal, because
the mode is not stored.

### 6. tmux server and configuration

Use the developer's **default tmux server** and set **no tmux options**.

- An advanced user already has a `~/.tmux.conf`. Mouse mode, scrollback,
  `window-size`, the prefix key and passthrough are theirs to choose, and
  GridVibe changing them would mutate sessions it does not own.
- The default server is what lets a developer attach an existing session, and
  see GridVibe's sessions with a plain `tmux ls`.

A dedicated `-L gridvibe` server would only pay off if GridVibe set options of
its own. With nothing to configure, it would just cost the ability to attach
existing sessions.

### 7. Where the code goes

A new domain module, `web/ssh_tmux.py`, owns everything tmux-specific:

- `tmux_sessions_enabled(settings=None)`, the experimental gate
- name generation and validation
- `prepare(client, name, directory, launch_line)`: the exec-channel check and
  create, returning missing / attached / created
- `attach_command(name)`
- `new_window(client, name, directory, launch_line)` for the relaunch controls
- `kill(client, name)`
- `current_path(client, name)` for the working directory

The changes elsewhere stay small:

- `web/terminal_io.py`: one branch in `_connect_ssh_session`, one at the top of
  `_run_startup_sequence` (a tmux connection skips it), one in the close path.
- `web/runtime_state.py`: the reduced snapshot for a tmux pane.
- `sessions/manager.py`: the field, in `_session_launch_fields` and
  `update_session_metadata`.
- `web/session_modes.py`, `web/session_shell.py`: the detach and new-window
  behaviour.
- `default_config.json`, `web/config.py`, the settings routes in `web/api.py`,
  `templates/partials/app_settings_modal.html` and
  `web/static/js/app-settings.js`: the experimental setting.
- The launcher form, session API, presets and split path, per the Completeness
  rule.

## What behaves differently inside tmux

### Working directory

By default tmux swallows the OSC escape sequences GridVibe's prompt hook prints
(`OSC 9;9` for the directory, `OSC 777;gridvibe-pid` for the shell pid), so
`current_directory` is never observed for a tmux pane, and
`_remote_process_cwd` has no pid to read.

Ask tmux instead, over an exec channel:

```sh
tmux display-message -p -t =NAME '#{pane_current_path}'
```

`effective_directory` uses this as its process source (B) for a tmux pane. Its
main consumer is the Terminal → Files switch, which needs to know where the
pane is standing. Nothing writes the result into the snapshot.

### Scrollback, size and keys

All of these follow the developer's tmux config, by design:

- tmux draws on the alternate screen, so xterm.js scrollback stays empty unless
  the config turns on mouse mode or disables the alternate screen.
- tmux sizes a window to its smallest attached client. A developer who also
  attaches from elsewhere wants `window-size latest` in their config.
- Check that the tmux prefix (Ctrl+B by default) is not taken by a GridVibe
  shortcut before it reaches xterm.js.

The launcher's help text for the option should mention scrollback, since it is
the first thing a user will notice.

### Unaffected

The SSH explorer and file transfers use their own SFTP channel. GridVibe's
cached output replay on page reload keeps working; on an attach, tmux's own
redraw replaces it.

## Explicitly out of scope

- Restoring an agent, its options, MCP grant or conversation in a tmux pane.
- Keeping an agent's GridVibe tools working across a GridVibe restart.
- Restoring a tmux pane's mode or observed directory.
- Listing the host's existing tmux sessions in the launcher.
- Runtime agent detection inside tmux.
- tmux for local WSL or POSIX panes. The same module could serve them later.
- Listing or cleaning up orphaned tmux sessions on a host. With detach as the
  default close, generated `gv-*` sessions can pile up; a later "tmux sessions
  on this host" view could offer reattach or kill. Until then, `tmux ls` and
  "Also end the tmux session" on close are the tools.

## Tests

Behavioural, using the existing fake paramiko client and channel stubs:

- Setting off: the launcher hides the fields, a request's `tmux_session` is
  ignored, a pane with a name connects to a plain shell, and a capture writes
  no name. No `kill-session` is ever sent because of the switch.
- Setting off then on before the next capture: the pane attaches again on
  reconnect.
- Attach types nothing. Create in terminal mode types nothing. Create in agent
  mode in the launch directory sends the launch line through `send-keys`,
  before the attach.
- An agent pane that attaches an existing session, or whose session was
  created in the home directory because the launch directory is missing, sends
  no launch line and shows the notice.
- tmux missing → plain shell, the notice, and today's startup sequence.
- A snapshot of a tmux agent pane holds the connection identity, the name, the
  launch directory and the title, and nothing else. Restoring it gives a
  terminal pane that attaches, or recreates the session in the launch
  directory when it is gone.
- A preset keeps the option, drops a generated name and keeps a typed one.
  A split gets a new generated name.
- An invalid name is refused at launch and dropped on restore.
- A second pane naming the same session on the same host is refused.
- Close detaches; close with "Also end the tmux session" sends `kill-session`
  before the client closes; workspace close and quit only detach.
- Switch to Files and back attaches again. Relaunch as agent sends
  `new-window`, not a new connection's shell.
- Every remote command uses `=NAME` and quoted arguments.

## Contracts and docs this touches

- **README:** live sessions are described as ending when the Python process
  exits. That stops being true for tmux panes. One bullet, next to the
  conversation-restore one, names the experimental setting and says that a tmux
  pane's session outlives GridVibe.
- **[Security](engineering_contracts.md#security-and-trust):** GridVibe can now
  leave processes running on a remote host after it exits, but only for panes
  where the developer chose tmux. Worth one line.
- **[Terminal transport and working directories](engineering_contracts.md#terminal-transport-and-working-directories):**
  attach vs create, no startup typing, `pane_current_path` as the directory
  source.
- **[Pane transitions](engineering_contracts.md#pane-transitions):** detach on
  mode switch, new window on relaunch, detach-by-default close.
- **[Configuration and durable state](engineering_contracts.md#configuration-and-durable-state)**
  and [`session_state_guideline.md`](session_state_guideline.md): the
  experimental `ssh.tmux_sessions` key and its gate, the field, and the reduced
  snapshot of a tmux pane.
- **CHANGELOG:** one `(feat)` note.
