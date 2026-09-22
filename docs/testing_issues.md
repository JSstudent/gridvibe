# GridVibe Testing Issues
Last updated: 2026-09-22

## Open Issues

### Issue ID: ISSUE-2026-057
- Title: An agent cannot re-root a pane above the explorer root the pane derived for itself
- Priority: Medium
- Status: Open
- Area: `web/session_modes.py`, `web/explorer.py`, `gridvibe_mcp/server.py`
- Assignee: Unassigned
- Tags: `mcp`, `explorer`, `terminal`, `cwd`, `transitions`, `tests`
- Reported: 2026-09-17

Description:
`set_pane_mode` publishes a `directory` field, so an agent asked to "make the Files pane a PowerShell terminal one level up" has a tool that looks like it can do exactly that. It cannot. There is no single call that moves a pane to a directory outside the explorer root the pane last derived for itself, and every way the attempt fails is either a refusal naming a rule nobody set or a success that changed nothing. Turning one explorer pane into a PowerShell terminal one directory above its repo took four calls and a reading of `web/session_modes.py` to discover the ordering that works.

Observed on a live workspace (`saved-session-session-20260821-214012-57a685`): pane `b832d655`, an explorer rooted at `...\Tutorial_apps\DiveDeep` with `explorer_root_configured: false`, asked by the agent pane beside it to become a terminal at `...\Tutorial_apps`.

Steps to reproduce:
1. Open a workspace with a Files pane rooted inside a Git repository and an agent pane in the same group, and let the Files pane's own shell history report the repo as its cwd.
2. From the agent pane call `set_pane_mode(session_id=<files pane>, mode="terminal", directory=<the repo's parent>, override=true)`.
   → `400 Explorer path must stay inside the configured root`.
3. Call it again with no `directory`.
   → The pane becomes a terminal, but at the repo, and its host is the stored shell family (here `cmd`, because that explorer carried `use_powershell: false`), so a second `set_pane_agent(shell="powershell", agent="")` is needed to get the shell that was asked for.
4. Repeat step 2 now that the pane is a terminal.
   → `200`, payload returned with `directory` still at the repo. Nothing happened and nothing said so.
5. Call `set_pane_mode(mode="explorer", directory=<the repo's parent>, override=true)` while the pane still has a live shell.
   → `200`, and `directory` comes back as the repo, not the parent.
6. Call the same thing once the pane has no live shell behind it (i.e. it is already an explorer).
   → Now the parent is taken, and only then does step 2 succeed.

Expected behavior:
A stated `directory` is either honored or refused with a sentence naming the rule that actually refused. An agent permitted to change a pane at all can re-point that pane at any directory that exists on the pane's own machine, including one above the root a previous mode derived — root containment is the boundary a *live Files pane browses inside*, not a limit on where a pane may be re-rooted. A call that changes nothing does not answer `200` with a pane payload.

Actual behavior / logs:
Four mechanisms, all confirmed by inspection.

**The clamp reaches a derived root.** The explorer/browser → terminal branch resolves the hand-off directory through `_resolve_pane_terminal_directory()` (`web/session_modes.py:313` → `web/explorer.py:4069`), which calls `_resolve_explorer_candidate_path()` (`web/explorer.py:897`). Containment there is tested against `_explorer_root_directory()` (`web/explorer.py:106`), which answers with the *stored* root whether or not anybody chose it — and `commonpath` rejects any path above it:

```python
if os.path.normcase(common_path) != os.path.normcase(root_path):
    raise ValueError("Explorer path must stay inside the configured root")
```

In the case above that root was derived by the pane's own previous transition (`explorer_root_configured: False`, set at `web/session_modes.py:268-270`), so the refusal names "the configured root" for a root no configuration holds. `_configured_explorer_root_directory()` (`web/explorer.py:115`) exists precisely to distinguish the two and is consulted for what the pane *hands back*, never for containment.

**An observed cwd outranks a stated path.** The terminal → explorer branch overwrites the stated directory unconditionally (`web/session_modes.py:202-205`):

```python
requested_directory = data.get("directory")
cwd_probe = _refresh_pane_cwd(session_id, session, bool(data.get("refresh_cwd")))
if cwd_probe["directory"]:
    requested_directory = cwd_probe["directory"]
```

`_refresh_pane_cwd()` reads an existing observation even when no refresh was requested, by design (`web/session_modes.py:94-133` — "a caller that did not ask for a refresh still gets one"). The MCP dispatcher states `refresh_cwd` only for `mode="explorer"` **with no directory given** (`gridvibe_mcp/server.py:1047-1055`), so the one call that deliberately does not want the probe's answer is the one the probe overrules. That is step 5, and it is also what puts the derived root back on the repo, which is what makes the clamp above refuse.

**A directory-only change is not a transition.** Past the explorer and browser targets, the terminal branch opens with (`web/session_modes.py:309`):

```python
if not (_is_explorer_session(session) or _is_browser_session(session)):
    return session.to_dict()
```

A terminal pane asked to become a terminal at a different directory returns its unchanged record with a `200`. Step 4 is indistinguishable from a success at the tool boundary — `client.switch_pane_mode` builds its result from `PANE_FIELDS` (`gridvibe_mcp/client.py:46`) and the pane simply still reads the old path.

**No other tool fills the gap.** The relaunch transaction accepts `shell`, `agent`, `mcp` and `distribution` only (`web/session_shell.py:110-143`, `:348`) and deliberately re-targets to the observed cwd (`:247-250`), so `set_pane_agent` cannot carry a path. `launch_panes` and `split_pane` both take a `directory`, but they create a pane rather than move one — the workaround an agent is left with is to split a correctly-rooted pane off and ask the user to close the old one, which the destroy tier cannot do either.

### Proposed solution:
Make a stated path authoritative, so the MCP surface can perform the re-root its schema already advertises.

- **Stated wins over probed.** In the explorer branch, consult `cwd_probe` for the directory only when no `directory` was stated, or when `refresh_cwd` was explicitly asked for. The header's own toggle states no directory, so its behaviour — root where the shell is standing — is unchanged; the probe's `requested`/`resolved` reporting stays as it is, because a caller that named a path needs no fallback notice.
- **Containment applies to the pane's selection, not to a stated path.** In `_resolve_pane_terminal_directory()`, resolve an explicitly stated `directory` on its own merits — exists, is a directory, and (for SSH) normalizes through `sftp` on the pane's own host — and keep `_resolve_explorer_candidate_path()` for the *unstated* case, where the path being resolved really is the live explorer's current selection and must stay inside the boundary it was browsing. When a stated path falls outside the old root, leave the pane with no root and `explorer_root_configured: False`, which is the state the explorer branch already produces for a derived root.
- **Let a directory-only change relaunch.** Narrow the early return at `web/session_modes.py:309` so a terminal pane with a stated `directory` that differs from its current one runs the branch's ordinary metadata-update/teardown/restart, and returns unchanged only when no stated field actually differs. The blast radius is one shell the caller is already gated to replace. If that is judged too wide for this transaction, the alternative is a dedicated gated twin (`set_pane_directory` in `session_modes.py`, one MCP tool in `tool_specs()`/`_run()`) under the same self/lineage/agent-running gates from `web/pane_gates.py` — same gates, same wording, no new tier.
- **Fix the sentence either way.** The containment refusal should name the root it tested against and whether it was derived, so a caller that hits it knows whether the fix is a different path or a re-root.
- **Then correct the records.** `gridvibe_mcp/server.py`'s `directory` description ("Omit to use where the pane is standing now") must say that a stated path re-roots the pane, and the Pane transitions contract's "Only a shell-family change retargets the directory" (`docs/engineering_contracts.md:292-296`) needs the stated-directory case added beside it.

Tests: in `tests/test_api.py`, over the agent mode-switch route — a stated directory above a derived root is applied; a stated directory wins over an observed cwd; a stated path that does not exist still answers `400`; a terminal pane given only a new directory relaunches there; and the no-directory call still opens on the observed cwd, so the header toggle's behaviour is pinned against this change. One dispatcher case that a stated `directory` does not set `refresh_cwd` (`gridvibe_mcp/server.py`), and one SSH case that a stated remote path is normalized through `sftp` and refused when missing.

Edge cases: the live explorer's own containment (`web/explorer_fs.py:255`, and `_resolve_explorer_paths()` for every browse/read/transfer route) must not move — this changes only the re-root transition, not what a Files pane can walk into. A root restored from a saved session is replayed verbatim and must stay `explorer_root_configured: True`, so a re-root that drops the root has to write that flag rather than leave a stale `True` behind. A stated path on the wrong machine must be refused rather than silently resolved against the GridVibe host: the local branch's `os.path.isdir()` runs where GridVibe runs, which is correct for a Local Repo pane and wrong for an SSH one, so the remote branch owns its own check.
