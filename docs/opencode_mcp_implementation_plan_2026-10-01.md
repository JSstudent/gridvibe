# OpenCode MCP support: findings and staged plan, 2026-10-01

- **Written on:** `szua_gridvibe-wrk-focus` at `cb5cccd`.
- **Goal:** an `opencode` agent pane can be given GridVibe's tools, can be
  handed a task, and can report its result. Those are the minimum. Auto mode is
  an optional extra stage.
- **Method:** each claim marked *verified* below was checked by running the
  installed opencode **1.18.34** (npm `latest` on this date) against the real
  sidecar or the real `web/mcp_http.handle_request`. The probe scripts were
  throwaway files in a scratchpad. The rest is from opencode's docs and issue
  tracker (see [Sources](#sources)). No GridVibe code was changed for this
  pass. Line numbers are current as of `cb5cccd`.

## Why the old premise no longer holds

`agent_registry.json` publishes no `mcp` block for opencode. The reason is
recorded in `gridvibe_mcp/README.md:666–669`, `web/agents.py:205–215` and
the CHANGELOG: the only mechanism is `opencode mcp add`, which permanently
edits the user's own config.

That is out of date. opencode reads two environment variables for one process.
Both are **merged** over the user's config, never written into it:

| Variable | Holds | Precedence |
| --- | --- | --- |
| `OPENCODE_CONFIG` | a path to a config file | above global, **below** project `opencode.json` |
| `OPENCODE_CONFIG_CONTENT` | inline config JSON | above project config |

This is per launch and leaves nothing behind. That is the same property
Claude's `--mcp-config` and Copilot's `--additional-mcp-config` have.

## Verified facts

| # | Question | Result on 1.18.34 |
| --- | --- | --- |
| V1 | Does `OPENCODE_CONFIG=<file>` bring the sidecar up? | Yes. `opencode mcp list` shows `✓ gridvibe connected`. `OPENCODE_CONFIG_CONTENT` does the same. |
| V2 | Is it merged with the user's config? | Yes. A server in a project `opencode.json` stayed listed beside `gridvibe`. |
| V3 | Does the MCP child see the pane identity? | Yes, both ways. The child inherits every `GRIDVIBE_*` variable, and `"{env:GRIDVIBE_SESSION_ID}"` inside the file resolves to the same value. |
| V4 | Do tool calls work from a model? | Yes. `opencode run` called `gridvibe_whoami` and got `inside_gridvibe: true`. All 25 tools are visible as `gridvibe_<tool>`. No permission prompt appeared. |
| V5 | Does `--prompt` auto-submit in the TUI? | Yes. Spawned in a PTY with `--port`, the TUI's own `/session/<id>/message` showed the user message and then the assistant's reply, after about 28 s. |
| V6 | Can the opening prompt be positional? | No. `opencode [project]` takes a *path* as its positional, so the prompt must be the `--prompt` flag. |
| V7 | Does `type: "remote"` work against GridVibe's HTTP endpoint? | Yes. The test used the real `handle_request` with a freshly minted pane token. opencode sent `GET` three times, accepted the `405`, and stayed on POST: `initialize`, `notifications/initialized`, `tools/list`, `tools/call`. |
| V8 | Does opencode reject unknown top-level config keys? | No. A file with both `mcpServers` and `mcp` loaded without a schema error. |
| V9 | Does a quoted Windows path survive in the JSON? | Yes, when written by `json.dump`. A hand-written single `\` is invalid JSON and opencode prints a parse error. Always generate the file. |

From docs and issues, **not** run here:

- **Tool-call timeout.** opencode uses the MCP SDK's 60 s default per call,
  capped at 120 s per step ([#25509], [#8701]). GridVibe's 55 s wait budget
  (`gridvibe_mcp/README.md:467`, `:555`) was set under Codex's 60 s, so it fits
  this too.
- **Tool-list timeout.** The per-server `timeout` is for *listing* tools and
  defaults to 5 s. If a slow start exceeds it, the tools are silently absent
  for that session.
- **Future version risk.** [#51135] reports that the reworked TUI v2 puts
  `--prompt` text in the composer but does not submit it. v2 is published under
  the `tui-v2`/`next` tags, not `latest`.
- **Environment inheritance may tighten.** Local MCP children inherit opencode's
  whole environment today (`{ ...process.env, ...mcp.environment }`, [#49998]).
  [#52427] questions that, so GridVibe should not rely on inheritance.

## Design decisions

| Decision | Choice | Why |
| --- | --- | --- |
| How the config reaches opencode | `OPENCODE_CONFIG=<path to a generated file>` | Inline JSON on a launch line, quoted for cmd, PowerShell and POSIX, is the Codex quoting problem again (`_toml_override_flag`). A path needs only the existing path guard. |
| Where the variable is set | A prefix on the composed launch line | It follows the existing rule that MCP is composed per connection. It covers local and SSH panes with one mechanism, and a relaunch restarts the shell (`docs/engineering_contracts.md:470–473`), so the variable cannot outlive a change of agent or MCP. Setting it in the spawn environment would only work locally, and would arm every opencode the pane ever runs. |
| Prefix per shell | posix: `env OPENCODE_CONFIG="<p>" opencode …`<br>PowerShell: `$env:OPENCODE_CONFIG="<p>"; opencode …`<br>cmd: `set "OPENCODE_CONFIG=<p>" & opencode …` | `env` scopes it to one process and works in bash, zsh, fish and csh alike. The SSH login shell is not known. PowerShell and cmd have no per-command form; the variable stays in that pane's shell until the next relaunch, which is accepted. |
| Path guard | Reuse `_UNQUOTABLE_PATH_CHARACTERS` (`"$`%`, `web/agent_session_hooks.py:88`) | It is exactly the set that breaks a double-quoted path in those three shells. Lift it to one owner rather than fork a copy. Anything the guard rejects gives no prefix: no tools, but the agent still starts. |
| Identity | The generated file's `environment` maps each `IDENTITY_VARIABLES` name to `"{env:<name>}"` | States the identity explicitly, like Codex's inline table, without relying on inheritance ([#52427]). Generated from `gridvibe_mcp/identity.py:26` so it cannot drift. One file still serves every pane, because these are references rather than values. |
| One file or two | A separate `.gridvibe_opencode.json` beside `.gridvibe_mcp.json` | Different schema (`mcp`, `type`, a `command` *array*). This keeps `.gridvibe_mcp.json` exactly what Claude and Copilot read, without having to verify that they tolerate an extra key. |
| Remote document | opencode shape for opencode panes: `{"mcp":{"gridvibe":{"type":"remote","url":…,"oauth":false}}}` | `oauth: false` stops opencode from probing for OAuth on an endpoint that has none. Same path, mode and read-back as today. One pane runs one agent per connection. |
| Registry shape | `mcp: {"style": "opencode_config", …, "verified": true}` | It is composed in code, like `inline_toml`, so the registry carries no quotes. The style names the *format*: Kilo is an opencode fork and could reuse it later, once verified. |
| Opening prompt | `opening_prompt: {"flag": "--prompt {prompt}", "verified": true}` | It already matches `_OPENING_PROMPT_FLAG_TEMPLATE` (`web/agents.py`). No code change is needed for the flag itself. |
| Tool-list timeout | `"timeout": 15000` in the local file | A cold venv start on Windows can approach 5 s. A remote server needs no startup, so its document keeps opencode's default. |

## Staging

Each stage leaves the app working and ends in its own commit. Stage 1 is
inert: nothing reads its output until stage 2 switches the registry on.

| Stage | What | User-visible? |
| --- | --- | --- |
| 0 | Re-verify against the opencode version current at implementation time; settle the WSL question | No (no code) |
| 1 | Generate the opencode config documents, local and remote | No |
| 2 | Launch composition and the registry `mcp` block: opencode panes get the GridVibe tools checkbox, locally and over SSH | **Yes** |
| 3 | `opening_prompt` and wording: opencode panes can be handed a task and report back | **Yes** |
| 4 | *(optional)* Auto mode with `--auto` | Yes |

Close-out for each stage:

- Run the focused tests named in the stage, plus the adjacent suites, then
  `ruff check .`.
- Do the manual check in the stage.
- Update the docs named in the stage, then add one CHANGELOG note in the house
  shape. Its headline is the commit subject.
- Replace superseded rules rather than appending to them. The CHANGELOG's past
  entries stay as they are: they are history.

---

### Stage 0 — re-verify and settle WSL (no code)

1. **Version.** Run `opencode --version` and `npm view opencode-ai dist-tags`.
   If `latest` has moved to v2, repeat V5 before stage 3.
2. **Repeat V1/V3 with the real generated file** after stage 1: run
   `OPENCODE_CONFIG=<generated> opencode mcp list` from a GridVibe pane.
   Expect `✓ gridvibe connected`.
3. **WSL panes.** These are local panes (`mode == "wsl"`, `use_wsl=True`) where
   `_pane_shell_family` returns `posix` but the generated files hold
   **Windows** paths: `C:\…\python.exe`, `C:\…\.gridvibe_mcp.json`. Check
   first what Claude gets in a WSL pane today with MCP ticked. Then choose:
   - **If the existing CLIs work in WSL** (for example, through some
     translation not yet found), follow the same route for opencode.
   - **If they are broken there too**, it is a pre-existing gap. Record it in
     `docs/testing_issues.md` and have the opencode composer give WSL panes no
     prefix, so it is not the one CLI that half-works. Fixing it properly needs
     `/mnt/<drive>/…` paths for both `OPENCODE_CONFIG` and the `command` array.
     opencode in Linux spawns the Windows `python.exe` through interop, which
     forwards the identity because `WSLENV` already names it
     (`tests/test_mcp_launch.py:285`). That is a separate change.

### Stage 1 — generate the opencode config documents (inert)

**`web/mcp_launch.py`**

- Add `OPENCODE_CONFIG_FILENAME = ".gridvibe_opencode.json"` and
  `PRODUCTION_OPENCODE_CONFIG_PATH`.
- Add `opencode_config_path()`. It reads a `GRIDVIBE_OPENCODE_CONFIG_PATH`
  override and refuses the production path in test mode, mirroring
  `mcp_config_path()` (`:61`) for the reason given at `:51–58`.
- Add `build_opencode_config(*, interpreter, url)` that returns:
  ```json
  {"$schema": "https://opencode.ai/config.json",
   "mcp": {"gridvibe": {
     "type": "local",
     "command": ["<interpreter>", "<SIDECAR_ENTRY>", "--url", "<url>"],
     "environment": {"GRIDVIBE_URL": "{env:GRIDVIBE_URL}", "…": "one per IDENTITY_VARIABLES"},
     "timeout": 15000}}}
  ```
  Build `command` from the same values as `build_mcp_config` (`:152`), so the
  two files cannot disagree.
- Have `write_mcp_config` (`:164`) write both files from one resolved
  interpreter and URL. A failure on either file is logged and returns `""` for
  that file only, as now. Return a value the callers can still treat as "the
  Claude file path" (`web/api.py:4906`, `Makefile` `mcp-config`, `utils/mcp_status.py`).
  Either keep the return type and write the second file as a side effect, or
  add a separate `write_opencode_config` called beside it. Prefer the latter:
  it is the smaller change to existing callers.

**`web/ssh_tunnel.py`**

- `remote_mcp_document(url)` (`:532`) → `remote_mcp_document(url, style="")`.
  For `style == "opencode_config"` it returns the opencode remote shape
  ([Design decisions](#design-decisions)). Otherwise it returns today's
  document unchanged.
- Pass the style through `write_remote_config` (`:604`) and `establish`
  (`:692`). The caller, `terminal_io._establish_mcp_tunnel` (`:3451`), has the
  session, so it resolves the agent's style with `_agent_mcp_style`.

**Housekeeping**

- `.gitignore`: add `.gridvibe_opencode.json` beside `:63–64`.
- `tests/__init__.py`: redirect `GRIDVIBE_OPENCODE_CONFIG_PATH` to a temporary
  directory, beside `:67`.
- `Makefile` `mcp-config`: write both files.
- `utils/mcp_status.py`: optional. Report whether the opencode file exists and
  names the same interpreter and URL.

**Tests (behavioural)**

- The opencode file parses and names the same interpreter, entry and URL as
  `.gridvibe_mcp.json`. It has an `environment` entry for exactly
  `IDENTITY_VARIABLES`, each the `{env:…}` reference to itself.
- An interpreter path containing backslashes and spaces round-trips through
  `json.load` unchanged (V9).
- Test mode refuses the production opencode path.
- `remote_mcp_document(url, "opencode_config")` has the `mcp.gridvibe.type ==
  "remote"` shape with `oauth: false`, and the default call is unchanged.
- Adjacent suites: `tests.test_mcp_launch`, `tests.test_mcp_remote`,
  `tests.test_mcp_status`, `tests.test_mcp_identity`.

**Docs:** none yet. Nothing is user-visible.

### Stage 2 — opencode panes get the GridVibe tools checkbox

**`agent_registry.json`** — under `opencode`:
```json
"mcp": {
  "style": "opencode_config",
  "description": "Gives this agent tools to see and build GridVibe workspaces.",
  "verified": true
}
```

**`web/agents.py`**

- Add `_MCP_STYLE_OPENCODE_CONFIG = "opencode_config"` beside
  `_MCP_STYLE_INLINE_TOML` (`:164`). `_agent_supports_mcp` (`:205`) accepts it.
  Rewrite that docstring: four of the eight CLIs can now be handed the sidecar,
  not three.
- Add `_opencode_config_prefix(path, shell_family)`, which returns the
  per-shell prefix from [Design decisions](#design-decisions), or `""` when the
  path fails the shared path guard.
- **Placement is the structural catch.** `_compose_agent_startup_command`
  (`:632`) builds everything as suffixes and then inserts the opening prompt
  only while `command.startswith(base)` (`:737–744`). The prefix must therefore
  be applied **last**, after the prompt is inserted. Keep a local
  `env_prefix`; set `mcp_fragment_placed = True` when it is non-empty, so the
  prompt in stage 3 sees it; then return `f"{env_prefix}{command}"`.
  `_agent_mcp_command_fragment` (`:516`) should either return the prefix
  through a separate small function, or return a `(prefix, suffix)` pair. Do
  not have it return a string that callers append, because that would put the
  variable *after* the binary.
- Local pane: the path is `opencode_config_path()`, which must exist on disk
  (the same "missing file costs the tools, never the agent" rule as `:557`).
  Remote pane: the path is `remote_config_path` from the tunnel, and the shell
  family is always `posix`.
- WSL: whichever way stage 0 settled it.
- `_agent_mcp_flag` stays `""` for opencode. The launcher already reads
  `mcp_supported`, not `mcp_flag` (`_agent_options`).

**Nothing else needs to change for agent detection.** The pane's agent is read
from the process tree (`terminal_io._observed_pane_agent_process`, `:931`), not
from the typed line, so a prefix does not hide it. `_note_pending_agent_relaunch`
(`:1193`) reads only lines the *reader* types. The clear prefix and update
prefix (`_agent_launch_line`, `:2556`) still go in front of the whole composed
line. For example, on cmd:
`cls & opencode upgrade & set "OPENCODE_CONFIG=…" & opencode`.

**SSH:** with stage 1 in place, `_establish_mcp_tunnel` opens the tunnel and
writes the opencode-shaped file as soon as `_agent_supports_mcp` turns true.
The composer then sends `env OPENCODE_CONFIG="<remote path>" opencode`.

**Tests**

- *Composition, one per shell family:* the exact prefix, the binary following
  it, and no prefix when the pane did not tick MCP.
- *Failure cases:* a path containing `$`, `%`, a backtick or `"` yields
  `"opencode"` alone. A missing generated file yields `"opencode"` alone.
- *Ordering:* clear, then update, then prefix, then binary, through
  `_agent_launch_line`.
- *Remote:* the posix prefix names the remote path. `_establish_mcp_tunnel` now
  calls `establish` for opencode, with the opencode style.
- *Existing assertions to flip* (change opencode's expectation, and keep the
  other four CLIs as the "no mechanism" examples):
  - `tests/test_mcp_launch.py:457–462`: the "no registry block" example moves
    to `kilo`.
  - `tests/test_mcp_launch.py:777`, `:818` (`UNSUPPORTED`) and `:791–797`.
  - `tests/test_mcp_remote.py:1330`.
  - `tests/test_api.py:22701`.
  - `tests/test_agent_types.py:246–250` and `:311–315`: these assert the refusal
    "opencode cannot be given GridVibe's tools"; move them to `kilo`.
- *Manual:* tick MCP on a local opencode pane, ask it to call `whoami`, and
  expect `inside_gridvibe: true` with this pane's id. On an SSH host with
  opencode installed, the same check should succeed.
- Adjacent suites: `tests.test_mcp_launch`, `tests.test_mcp_remote`,
  `tests.test_agent_types`, `tests.test_api`, `tests.test_session_shell`.

**Docs**

- `README.md:95`: OpenCode row, "GridVibe tools" → Yes.
- `gridvibe_mcp/README.md`:
  - "Which CLIs" (`:628–669`): "Three of the eight" becomes four. Add an
    opencode row (`OPENCODE_CONFIG="<path>"` prefix, `style: opencode_config`,
    opening prompt "—" until stage 3). Remove opencode from the "publish
    nothing" paragraph.
  - Explain the per-shell prefix and why the variable stays in a PowerShell or
    cmd pane until relaunch.
  - Identity section (`:90–93`): opencode states the identity with `{env:…}`
    references.
  - SSH section (`:620`, "three CLIs that are handed a URL"): becomes four.
    Note the opencode remote shape.
- `docs/engineering_contracts.md`, Agent tools (MCP) (`:1905–1925`): the
  generated-config bullet now covers two files. Add one bullet owning the
  prefix rule: applied last, guarded path, no prefix rather than a broken line.
- **CHANGELOG:** `- **(feat) OpenCode panes can be given GridVibe's tools.**`
  Body: until now opencode had no checkbox, because its only known mechanism
  was `opencode mcp add`. opencode reads a per-process `OPENCODE_CONFIG`, so
  GridVibe now points it at a generated file merged over the user's config,
  without editing it. Local and SSH panes are covered.

### Stage 3 — opencode panes can be handed a task and report back

**`agent_registry.json`** — under `opencode`:
```json
"opening_prompt": {
  "flag": "--prompt {prompt}",
  "description": "Starts the TUI and submits this as the first message.",
  "verified": true
}
```

That alone makes `_agent_accepts_task("opencode")` true, so it updates
`task_capable_agents()`, every `task_refusal` sentence and `list_agent_types`'
`task_supported`. The prompt is placed directly after the binary, and the
stage 2 prefix goes in front of the whole line:
`env OPENCODE_CONFIG="…" opencode --prompt "GridVibe handed this pane a task…"`.

**`gridvibe_mcp/server.py:256`:** `TASK_DESCRIPTION` hard-codes
"(claude, codex, copilot)". Add opencode. The sidecar never reads
`agent_registry.json` (`list_agent_types` asks GridVibe over HTTP), so
building the list from the registry would mean a new file read in
`gridvibe_mcp/`. A plain text edit is enough. Optionally, pin it with a test
that compares the names in this sentence with `task_capable_agents()`, so it
cannot go stale silently.

**Tool naming.** `HANDOFF_OPENING_PROMPT` (`web/agent_handoffs.py:65`) says
"Call the gridvibe tool read_handoff", but opencode exposes the tool as
`gridvibe_read_handoff`. V4 shows the model maps the names without help. Leave
the sentence alone: it is pinned to `[A-Za-z0-9 .,_]` and shared by every CLI.
Revisit only if the manual check below fails.

**Tests**

- `tests/test_agent_types.py:106`: `task_supported` is now true for opencode.
  Move `:257–264`'s "cannot be handed a task" example to `kilo`.
- `tests/test_agent_opening_prompt.py:117`: the refusal example moves to `kilo`.
  Add a positive case: the opencode line carries `--prompt "<sentence>"`
  directly after the binary and after the prefix, and
  `launch_line_carries_opening_prompt` is true.
- `opening_prompt_gap` still names the right reason for an opencode pane whose
  prefix was refused (no config found).
- *Manual, end to end:* from a Claude pane, `split_pane` an opencode pane with a
  short task, then `wait_for_results`. The opencode pane should open, submit
  the sentence, call `gridvibe_read_handoff` and then `gridvibe_report_result`,
  and the parent should receive the report. Repeat with opencode as the
  *parent* handing a task to Claude, which exercises `wait_for_results` under
  opencode's 60 s call timeout.
- Adjacent suites: `tests.test_agent_opening_prompt`, `tests.test_agent_types`,
  `tests.test_agent_handoffs`, `tests.test_mcp_tools`.

**Docs**

- `gridvibe_mcp/README.md`: fill in the opening-prompt column of the opencode
  row (`--prompt "<sentence>"`, directly after the binary), and update `:686`
  (`task_supported` list). Note the v2 risk below in one sentence.
- `docs/engineering_contracts.md:1668–1676`: no rule changes. Check that the
  wording does not name the three CLIs.
- **CHANGELOG:** `- **(feat) An OpenCode pane can be handed a task and report its result.**`

### Stage 4 (optional) — Auto mode for opencode

`opencode --help` on 1.18.34 lists `--auto` ("auto-approve permissions that
are not explicitly denied"). It passes `_agent_auto_mode_flag`'s token guard
(`web/agents.py:75`).

- Registry: `"auto_mode": {"flag": "--auto", "description": "Launches with permissions auto-approved unless your config explicitly denies them."}`.
- Tests: `tests/test_api.py:22212` and `:22221` pin opencode's auto-mode flag
  and description as empty. Flip them. The comment at
  `tests/test_mcp_launch.py:457` should no longer use opencode as its example.
- Docs: `README.md:95`, Auto mode → Yes.
- **CHANGELOG:** `- **(feat) OpenCode panes can start in Auto mode.**`

---

## Known limitations to document

1. **`--prompt` auto-submit may break in a future version.** opencode's TUI v2
   only prefills ([#51135]). If v2 becomes `latest` before that is fixed, a
   handed-over task waits in the composer for someone to press Enter, and the
   parent's `wait_for_results` times out. The registry's `verified` bar is the
   lever: re-verify on upgrade. A stronger fallback exists — launch with
   `--port` and call the TUI's `/tui/submit-prompt` — but it opens an
   unauthenticated control port on loopback. Do not add it without a security
   review.
2. **A user's own `OPENCODE_CONFIG` is replaced** in GridVibe-launched opencode
   panes with MCP ticked. There is one variable and opencode has no include
   mechanism. The user's global and project configs still apply.
3. **A project `opencode.json` that defines its own `mcp.gridvibe` wins**, since
   project config ranks above `OPENCODE_CONFIG`.
4. **On PowerShell and cmd the variable stays set in the pane's shell** after
   opencode exits, so a hand-typed `opencode` in the same pane also gets the
   tools. A relaunch or close clears it. POSIX shells are not affected.
5. **SSH panes get tools, not tasks.** A task never leaves its caller's machine
   (`docs/engineering_contracts.md`, Agent tools). This is unchanged and
   applies to every CLI.

## Out of scope

- **Conversation restore.** opencode has `--session <id>` and `--continue`, but
  it would need its own `conversation_restore` strategy.
- **Kilo.** It is an opencode fork and may accept the same style through its own
  variable. That needs verifying separately.
- **The WSL path translation** described in stage 0, if every CLI turns out to
  need it.

## Appendix — how the facts were checked

These can be re-run at stage 0. `$S` is any scratch directory. Each config is
written with Python's `json.dump`, never by hand (V9).

- **V1–V3:**
  ```sh
  OPENCODE_CONFIG="$S/oc.json" opencode mcp list
  ```
  For V3, point a server's `command` at
  `python -c "import os,json; json.dump({k: v for k, v in os.environ.items() if k.startswith('GRIDVIBE')}, open(...))"`
  and compare the dumped values with `{env:…}` references in `environment`.
- **V2:** run the same command from a directory whose `opencode.json` defines a
  second server. Both should be listed.
- **V4:**
  ```sh
  OPENCODE_CONFIG="$S/oc.json" opencode run -m opencode/big-pickle "Call the gridvibe_whoami tool exactly once…"
  ```
  This uses a free model, at no cost.
- **V5:** spawn `opencode --prompt "Reply with only the word PONG." --port 47123 -m opencode/big-pickle`
  under `winpty.PtyProcess` (pywinpty is already in the venv). Poll
  `GET /session` and `GET /session/<id>/message` on that port until an
  assistant message appears. This leaves one throwaway session in the opencode
  history.
- **V7:** serve `web.mcp_http.handle_request` from a small `ThreadingHTTPServer`
  with a token from `pane_tokens.mint(session_id=…)`. Answer `GET` and `DELETE`
  with `405 Allow: POST`. Point a `type: "remote"` config at it with
  `oauth: false`, and log the methods received.

## Sources

- [opencode — MCP servers](https://opencode.ai/docs/mcp-servers/)
- [opencode — Config (precedence, `OPENCODE_CONFIG`, `OPENCODE_CONFIG_CONTENT`)](https://opencode.ai/docs/config/)
- [opencode — CLI](https://opencode.ai/docs/cli/)
- [#49998 — pass configured server env block to child process][#49998]
- [#52427 — local MCP servers inherit `OPENCODE_SERVER_PASSWORD`][#52427]
- [#51135 — `--prompt` no longer auto-submits in 2.0][#51135]
- [#3937 — request for an auto-submit flag](https://github.com/anomalyco/opencode/issues/3937)
- [#25509 — tool execution hard-limited to 120 s][#25509]
- [#8701 — per-server MCP timeout vs `experimental.mcp_timeout`][#8701]

[#49998]: https://github.com/anomalyco/opencode/pull/49998
[#52427]: https://github.com/anomalyco/opencode/issues/52427
[#51135]: https://github.com/anomalyco/opencode/issues/51135
[#25509]: https://github.com/anomalyco/opencode/issues/25509
[#8701]: https://github.com/anomalyco/opencode/issues/8701
