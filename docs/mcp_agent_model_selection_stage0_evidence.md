# MCP Agent Model Selection: Stage 0 Evidence

Companion to the [implementation plan](mcp_agent_model_selection_implementation_plan.md#stage-0--prove-discovery-and-budgets).
Opened on 2026-10-06 against repository HEAD `a625d6b`. This record holds
evidence only. It sets no contract and authorizes no code change, dependency
install, or repair of a local CLI environment.

**Tracking note.** This record and its plan are tracked in `docs/` only for the
duration of the implementation, so changes to them can be reviewed in commits.
Once the feature ships, both move to `docs/r&d/` and stop being maintained;
neither is a citation of record.

**Secret-free rule.** Never paste tokens, API keys, `auth.json` contents,
account emails, organization IDs, raw config files or unfiltered process output.
Record model IDs, field names, counts, durations, exit codes and file *paths*
only. Where output must be shown, cut it to the fields named in the row.

**Status:** not started. Fill the sections in order. The verdict table at the
end is the Stage 0 exit.

## 1. Plan baseline recheck (2026-10-06)

The plan was written against `3453982`. Since then six commits have landed,
all for crew-link history (`web/crew_history.py`, restore and snapshot wiring).
Every row of the plan's "Current implementation" table was checked against
`a625d6b` and still holds:

- All cited symbols exist: `agent_type_rows`, `_compose_agent_startup_command`,
  `agent_availability_target`, `build_pane_request`, `build_split_pane_request`,
  `build_launch_request`, `_RELAUNCH_BOUND_FIELDS`, `_split_pane_overrides`,
  `open_split_intent`, `split_session`, `SPLIT_RESULT_FIELDS`,
  `_SESSION_SNAPSHOT_FIELDS`, `_prepare_launch_sessions`,
  `_restore_group_request`, `_clear_agent_launch_identity`, `AGENT_TYPE_FIELDS`.
- `agent_types` uses 50 s. Launch, split-intent and relaunch use the 15 s
  default. The split chain is still 20/20/15/40 s (section 6).
- An explicit shell, agent or MCP choice still restarts the pane even when
  nothing changes (`web/session_shell.py`, "A valid, explicitly stated dimension
  is also the relaunch instruction").
- `docs/engineering_contracts.md` still says "Reselecting the current choice is
  a no-op on both sides" (Pane transitions). That line is stale against the
  code, as the plan says. The deleted design document took the stale side, and
  the plan correctly follows the code.
- The MCP contract still says "No byte a tool supplies reaches a launch line".
- `split_session` resolves the stated or explorer directory before
  `agent_handoffs.take`, and takes before `append_session_to_group`.
- No `web/agent_models.py` or `tests/test_agent_models.py` exists yet. Every
  test module the plan names exists.

Drift and gaps to carry forward:

| # | Finding | Affects |
| --- | --- | --- |
| B1 | `web/workspaces.py` is now 2,468 lines, not 2,410. The new `_restore_crew_links` call sits inside `_restore_claimed_workspace`. | Stage 1: a launch/restore extraction must move or wrap the crew-history step without changing its "presentation only, never fails the restore" behaviour. |
| B2 | The architecture contract names the **close-action matrix** as the primary extraction cut at ~2,200 lines, with launch/restore as the alternative. The plan picks the alternative. | Stage 1 should record why launch/restore is the right cut for this feature. |
| B3 | The plan called itself gitignored R&D while tracked at `docs/`, and its relative links assumed a path three levels below `docs/`. | Resolved 2026-10-06: both documents stay tracked in `docs/` until the feature ships, then move to `docs/r&d/` (tracking note above). Links now resolve from `docs/`. |
| B4 | The reviewed design `mcp_agent_model_selection_plan.md` was deleted in `c84064e`. Its last content is at `2706cfc` (`git show 2706cfc:docs/mcp_agent_model_selection_plan.md`). Stage 0 needs its References. | The plan now cites the design by that revision. Its sources are copied into section 9 so this record stands on its own. |
| B5 | Composer ordering has three details the plan leaves implicit. The opening prompt is spliced in **directly after the binary**. OpenCode's MCP grant is an `OPENCODE_CONFIG=` **prefix** in front of the binary, not a suffix. Codex resume is a **subcommand** (`resume_style: subcommand`). | Section 7: prove where `--model` can go for each CLI's create and resume forms. |
| B6 | Agent preflights share one 4-worker pool (`_PREFLIGHT_WORKERS`, `web/agents.py`). | Section 6: decide whether model probes share that pool or get their own. Sharing means a launch preflight and a model discovery wait on each other. |

## 2. Host and target inventory

The versions below come from installed `package.json` metadata. No CLI was run.
Confirm each one with `--version` inside a fixture home (section 3) before you
trust it.

| Item | Value | Confirmed by |
| --- | --- | --- |
| GridVibe host | Windows 11 Pro 10.0.26200 | system |
| Node / npm | `C:\Program Files\nodejs` | PATH |
| Claude Code | 2.1.286 (`@anthropic-ai/claude-code`, npm global) | TBD `--version` |
| Codex CLI | 0.160.0 (`@openai/codex`, npm global) | TBD `--version` |
| OpenCode | 1.18.34 (`opencode-ai`, npm global) | TBD `--version` |
| WSL distributions | `Ubuntu` (WSL 2); `docker-desktop` (excluded, not a user shell) | `wsl -l -v` |
| CLIs inside `Ubuntu` | TBD (binary paths and versions) | TBD |
| SSH target(s) | TBD: host *label* only, OS, which CLIs are installed | TBD |
| Local POSIX host (non-WSL) | TBD, or "not available: recorded as untested" | TBD |

## 3. Method

### 3.1 Two phases

**Phase A, fixture home.** Run each candidate mechanism with every config,
data and cache location pointed into a fresh temp directory, plus a fixture
project directory that holds a sentinel project config. There are no
credentials, so account-dependent catalogs will be empty or refused. Phase A
measures side effects and behaviour without authentication.

**Phase B, real home, read-only observation.** Only for a mechanism that came
through Phase A with no writes, hooks or background processes. Snapshot the real
config directories before and after one run, and record the catalog *shape*
(counts, field names, ID patterns) with credentials in place. Do not copy any
file out of a real home.

Never send a prompt, start a thread or turn, or run any `--refresh`, `login`,
`auth`, `upgrade` or `install` subcommand.

### 3.2 Isolation variables to verify

These are candidates. For each one, confirm in the installed version's docs or
`--help` that it moves the location, and record that in the adapter section.

| CLI | Variables | GridVibe launch overlay |
| --- | --- | --- |
| Claude | `CLAUDE_CONFIG_DIR`, `HOME`/`USERPROFILE` | `--settings <generated hook file>` (additive) |
| Codex | `CODEX_HOME`, `HOME`/`USERPROFILE` | `-c` TOML overrides (title, MCP servers) |
| OpenCode | `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, `XDG_STATE_HOME`, `HOME`/`USERPROFILE` | `OPENCODE_CONFIG=<generated file>` |

Discovery has to see the same configuration the launched agent will see. That
means the overlay column must be applied during discovery too. Whether it is
merged with or replaces the user's config decides whether providers survive.

### 3.3 Side-effect detection, per run

1. Before the run: list the fixture home, the fixture project and (in Phase B)
   the real config directories, recording path, size, mtime and SHA-256. Record
   the process tree under the shell you are using.
2. Run the mechanism under a 15 s wall clock with stdout and stderr captured and
   capped at 1 MiB.
3. After it exits, or after the tree kill: list everything again, diff it, and
   diff the process tree. Note any process that outlived the parent and how
   long it lasted.
4. Record the result in the adapter row: files created or changed (paths only),
   hooks or plugins run, dependency installs, network refresh, leftover
   processes, and the cleanup outcome.

## 4. Adapter evidence

Each candidate mechanism gets one record using these fields, which are the ones
the plan requires:

```text
CLI / version:            Transport / shell family:     Effective cwd:
Metadata source:          (command or protocol method, exact invocation shape)
Exact-ID mapping:         (which field is the --model argument; sample ID shapes)
Provider / config:        (how effective provider, project config and overlay are applied)
Restrictions:             (managed or allowlist policy honoured? how observed)
Hidden / denied models:   (policy chosen and why)
Account access:           (true/false/null semantics this source can support)
Side effects:             (Phase A diff; Phase B diff)
Latency:                  (cold / warm, ms; pagination pages)
Cleanup:                  (exit path, tree kill needed?, leftovers)
Verdict:                  pass / fail / blocker - one sentence
```

### 4.1 Claude Code

Pass criteria: a metadata-only source of **exact launch IDs** under the
effective backend (subscription, API key, or a cloud provider backend) that
honours any managed model restriction. It must not run project hooks or load
plugins. Aliases alone do not pass.

| Candidate | Known concern to settle | Record |
| --- | --- | --- |
| Documented `--model` aliases | Aliases are not account-specific exact IDs, so this cannot pass alone. It may be useful as a label source. | TBD |
| Agent SDK initialization, supported-models call | Needs a new SDK dependency. Initialization may load project settings and hooks unless setting sources can be disabled. Needs a tradeoff record (section 8) before adoption. | TBD |
| Provider models REST endpoint | Needs credentials in GridVibe's backend. May not reflect the CLI's effective backend, subscription tier or managed restriction. Check backend parity. | TBD |
| Managed or user settings model restriction | A filter, not a source. Find where the installed version reads it and whether any metadata source applies it. | TBD |
| Interactive `/model` picker | Scraping it is ruled out by design. List it only to record that it was rejected. | rejected |

### 4.2 Codex

Pass criteria: a stdio `codex app-server` started with the launch's effective
`CODEX_HOME`, cwd and `-c` overrides. It answers `initialize`, then `model/list`
with bounded pagination, and starts no thread or turn. Record the following:

| Question | Record |
| --- | --- |
| Handshake (`initialize` / `initialized`) shape and client-info fields used | TBD |
| `model/list` request params (cursor, limit, any hidden-model flag) | TBD |
| Response row fields; which one is the `--model` argument (`id` vs `model`) | TBD |
| Pagination: page size, `nextCursor` end condition, repeated-cursor behaviour | TBD |
| Hidden-model policy chosen (exclude by default?) and why | TBD |
| Does the catalog follow `-c model_provider=` and project `.codex/config.toml`? | TBD |
| Without auth (Phase A): refusal shape vs empty list | TBD |
| Does closing stdin end the server and all its children on Windows, WSL and POSIX? | TBD |
| Entitlement: does any field prove account access? (expect `null`) | TBD |

### 4.3 OpenCode

Pass criteria: enumerate only **configured or enabled** providers under merged
configuration, including the `OPENCODE_CONFIG` overlay. Every ID must be an
exact `provider/model` string. Never use `models --refresh`.

| Question | Record |
| --- | --- |
| `opencode models [provider]` output format and stability (machine-readable?) | TBD |
| Optional metadata flag (e.g. verbose) — fields, size, safety | TBD |
| Does it list the public catalog or only connected providers? How to filter? | TBD |
| Does `OPENCODE_CONFIG` merge with or replace global and project config? | TBD |
| Do `disabled_providers`, `enabled_providers` or similar policy keys apply? | TBD |
| Does any run start a server, install plugin dependencies, or refresh the cache? | TBD |
| Historical `EEXIST` on the config dir: reproduced? (observe only, no repair) | TBD |
| Entitlement: expect `null` | TBD |

## 5. Transport matrix

Fill one cell per CLI and transport: `pass`, `fail: reason`, `blocker`, or
`untested`. "Pass" means the probe ran in the destination's own environment and
saw the destination's config. A missing path returns an actionable refusal. It
never falls back to the GridVibe host's catalog.

| Transport | Claude | Codex | OpenCode | Notes (quoting, env, PATH) |
| --- | --- | --- | --- | --- |
| Local PowerShell | TBD | TBD | TBD | |
| Local cmd | TBD | TBD | TBD | |
| Local POSIX (non-WSL host) | TBD | TBD | TBD | |
| WSL `Ubuntu` | TBD | TBD | TBD | login vs non-login shell PATH |
| SSH (centralized host-key trust) | TBD | TBD | TBD | remote teardown proof required |

For SSH, record how the probe reuses the existing trusted connection path. Also
record what proves the **remote** process tree ended: closing the channel alone
is not proof.

## 6. Bounds, teardown and budgets

### 6.1 Existing constants (verified 2026-10-06)

| Constant | Value | Location |
| --- | --- | --- |
| Sidecar default request | 15 s | `gridvibe_mcp/client.py` `DEFAULT_TIMEOUT_SECONDS` |
| `agent_types` request | 50 s | `gridvibe_mcp/client.py` `AGENT_TYPES_TIMEOUT_SECONDS` |
| Page split POST | 20 s | `web/static/js/terminals.js` `SPLIT_REQUEST_TIMEOUT_MS` |
| Intent claim window | 20 s | `web/window_intents.py` `CLAIM_TTL_SECONDS` |
| Intent pending window | 15 s | `web/window_intents.py` `INTENT_TTL_SECONDS` |
| Sidecar split wait | 40 s | `gridvibe_mcp/splits.py` `DEFAULT_WAIT_SECONDS` |
| In-process intent wait (HTTP transport) | 35 s (15 + 20) | `web/mcp_http.py` `INTENT_WAIT_SECONDS` |
| Agent preflight pool | 4 workers, shared | `web/agents.py` `_PREFLIGHT_WORKERS` |
| Tree kill / reap | 5 s | `web/process_bounds.py` `PROCESS_REAP_TIMEOUT`, `terminate_process_tree` |

The split recording step runs inside the sidecar's `split_intent` POST, which
uses the 15 s default. A 12 s recording budget, cleanup included, leaves about
3 s for transport. Measure that margin rather than assume it.

### 6.2 Targets vs measured

| Budget | Target | Measured worst case | Holds? |
| --- | --- | --- | --- |
| One discovery probe, cleanup included | 15 s | TBD | TBD |
| Complete discovery or model preflight (queue, binary/cwd checks, cleanup) | 40 s | TBD | TBD |
| HTTP request for model operations | 50 s | n/a (constant) | |
| Split recording validation, complete | 12 s | TBD | TBD |
| Split final validation, complete, inside the 20 s page POST, plus geometry and report | 12 s | TBD | TBD |
| Admission: 5th concurrent probe refuses before mutation | refuse | TBD | TBD |
| Captured output cap | 1 MiB | TBD | TBD |
| Repeated cursor detected and refused | refuse | TBD | TBD |

If either split row fails, write the revised POST/claim/wait/CLI-tool chain
here before Stage 5. The constants and their tests change together. A late
append must never coexist with an expired intent that reports "untouched".

## 7. Launch argument composition

These checks prove flag placement and acceptance only, using `--help` for each
subcommand or a launch that is killed before any prompt. Never run inference.

| CLI | Create form | Resume form | With MCP fragment / prefix | With opening prompt | Notes |
| --- | --- | --- | --- | --- | --- |
| Claude | `claude --model <id> …` TBD | `--resume` / `--session-id` with `--model` TBD | `--mcp-config` is variadic: model must precede it TBD | prompt spliced after binary TBD | |
| Codex | `codex --model <id> …` TBD | `codex resume <id>`: does `--model` go after the subcommand? TBD | `-c` overrides TBD | prompt spliced after binary TBD | |
| OpenCode | `opencode --model <provider/model>` TBD | session continuation form TBD | `OPENCODE_CONFIG=` prefix is unaffected TBD | TBD | |

Sample ID shapes for freezing the safe-token alphabet (Stage 2). Shapes only,
for example `provider/name-1.2-variant`, not a catalog:

| CLI | Characters observed beyond `[A-Za-z0-9._-]` | Longest ID seen | Sample shapes |
| --- | --- | --- | --- |
| Claude | TBD | TBD | TBD |
| Codex | TBD | TBD | TBD |
| OpenCode | TBD (`/`; others such as `:` or `@`?) | TBD | TBD |

## 8. Blockers and tradeoff records

Add an entry for any adapter that needs a new dependency, an undocumented
protocol, or has no safe source:

```text
ID / CLI:
Problem:
Options considered:
Decision (or "blocker, adapter stays unavailable"):
Consequence for the feature claim:
```

## 9. Sources to recheck

Copied from the deleted design (B4). Re-verify each one against the installed
versions above and note the date checked.

| Source | Checked | Notes |
| --- | --- | --- |
| [Claude CLI reference](https://code.claude.com/docs/en/cli-reference) | TBD | `--model` |
| [Claude model configuration](https://code.claude.com/docs/en/model-config) | TBD | aliases, restrictions |
| [Codex app-server: list models](https://learn.chatgpt.com/docs/app-server#list-models-modellist) | TBD | confirm the URL is still canonical |
| [OpenCode CLI](https://opencode.ai/docs/cli/) | TBD | `--model`, `models`, `--refresh` |
| [OpenCode providers](https://opencode.ai/docs/providers/#deepseek) | TBD | connect vs select |

## 10. Exit verdict

| CLI | Safe metadata source | Transports passing | Budgets hold | Verdict |
| --- | --- | --- | --- | --- |
| Claude | TBD | TBD | TBD | TBD |
| Codex | TBD | TBD | TBD | TBD |
| OpenCode | TBD | TBD | TBD | TBD |

An adapter without a demonstrated source stays unavailable for explicit model
selection, and the three-CLI feature is not called finished. Record cleanup of
all fixture directories here when Stage 0 closes.
