# GridVibe MCP — Phase 0.5: Awareness and In-Place Capability

**Status:** r&d proposal. Not a contract. Nothing here constrains the code until
it lands in `docs/engineering_contracts.md` or the module that owns the rule.

**Parent:** [`mcp_phase0_plan.md`](mcp_phase0_plan.md). Phase 0 shipped: the
sidecar, pane identity, the five read tools and the four create tools, the
window-intent store, and — after `a7e6e52` / `8ec6bab` — the same tool surface
for a pane whose shell is on another machine over SSH.

Phase 0 gave an agent a workspace it could **build**. Phase 0.5 is about the
workspace it is **already sitting in**: where its own pane is, how the panes
around it are arranged, which pane is above or below it, and how to shape panes
that already exist.

---

## 0. What Phase 0.5 is

Three capabilities, in the order they unlock each other:

1. **Awareness.** An agent can describe the workspace it is in — pane order,
   pane geometry, relative size, and which pane is its own. Read-only.
2. **Spatial reference.** "The terminal below me", "the smaller two", "Terminal
   4" all resolve to a session id, derived from geometry rather than guessed.
3. **In-place capability.** An agent can split a pane on a chosen axis and say
   what the new pane should run, instead of appending an unplaced plain
   terminal.

And one thing it is explicitly preparing for, without shipping it:

> "Split the explorer pane vertically, then split the new pane horizontally,
> start a codex agent in each, and give each one a task on the problem you just
> analyzed."

That sentence needs chained splits (each split's answer naming the pane the
next split targets), per-pane agent selection at creation time, and eventually
a way to hand a pane an opening instruction. The first two are in this phase.
The third is not — see §11.

---

## 1. The prompts this phase must serve

Four prompts, in increasing distance from what Phase 0 can do. The asymmetry
between them is the whole shape of this document.

| | Prompt | What it needs |
| --- | --- | --- |
| **A** | *"Make a new workspace on this directory using this session's layout and terminal modes, with claude and codex in the smaller terminals."* | Reads only. The write half already exists end to end. |
| **B** | *"Launch my saved 'review' layout into a new workspace, with an agent in pane 2."* | One new read over saved presets, with its own field list. |
| **C** | *"Split Terminal 4 vertically and start a codex agent in each of the new terminals."* | A new mechanism — the split axis never reaches the server — plus a relaunch verb Phase 0 deliberately omitted. |
| **D** | *"Start a claude agent in the terminal below this one."* | Spatial resolution over pane rectangles, then the same relaunch verb as C. |

**A and B are nearly free.** Once an agent can *read* a layout, the write half
is already built: `POST /api/sessions` accepts and normalizes a
`workspace_layout` today (F3), and the sidecar simply never sends one.

**C and D run into the fact this phase is mostly about.** The geometry of a
split is computed by the page, not by the server — and "the terminal below" is
only answerable from geometry the page holds.

---

## 2. What Phase 0 already answers

| Question | Today | How |
| --- | --- | --- |
| Which session am I in? | **Yes** | `whoami` → `session_id`, `group_id`, live `directory` |
| Which workspace am I in? | **Yes** | `whoami` → `workspace_id`; `list_panes` defaults to it |
| What else is open here? | **Yes** | `list_panes` → title, mode, host, directory, status per pane |
| Which machine is my pane on? | **Yes** | `whoami` → `host`, `runs_on`, and the remote-path note |
| Where is my pane on screen? | **Implicitly** | Array order only, and only when a `group_id` is passed |
| How are the panes arranged? | **No** | The geometry is on the wire and the sidecar drops it (F2) |
| Which pane is below mine? | **No** | Nothing derives adjacency |
| Change a pane that exists | **No** | `split_pane` appends one unplaced plain terminal |

---

## 3. What the code actually says

Eight findings, read out of the tree. F2, F3 and F8 are the ones that change
the size of the work: two of them make it smaller than it looks, and the third
makes the last decision purely a policy question.

### F1 — No pane index is published, but the order is real

`TerminalSession.to_dict()` carries no index. Position lives in
`group.pane_order`, and `SessionManager.get_group_sessions` applies it — so
`list_panes` **with** a `group_id` already returns panes in on-screen order.
Without one it returns the whole workspace across groups, where array position
means nothing.

`web/dashboard.py` does compute a true `index` per pane. Phase 0's own
`AGENT_FIELDS` allowlist then drops it before `list_agents` returns. That is an
oversight, not a decision.

> `sessions/manager.py:1466` · `web/dashboard.py:134,149` · `gridvibe_mcp/client.py:67`

### F2 — The geometry is already on the wire; the sidecar discards it

`GET /api/sessions?group=<id>` returns the group meta, and that meta carries
`workspace_layout` — the real per-pane rectangle record — alongside `layout`,
`connection_mode` and `terminal_count`.

`GridVibeClient.panes()` reads only `payload["sessions"]` and projects it
through `PANE_FIELDS`. Everything else in the response, geometry included, is
thrown away before the tool result is built.

So D1's read half is not a server change at all. It is a projection change.

> `web/api.py:695-707,1042-1043` · `gridvibe_mcp/client.py:265-275`

### F3 — `launch_panes` could already carry a geometry

`launch_session_group` reads `workspace_layout` straight off the request body
and runs it through `_normalize_workspace_layout` — the same validator the
presentation route uses — before handing it to `create_group`.

`build_launch_request` never sets the key. Prompt A's write half is therefore
**zero server work**: pass the field through and let the existing normalizer
refuse a bad record.

> `web/workspaces.py:837-841,916-921` · `gridvibe_mcp/server.py:332`

### F4 — The layout name and the geometry are two different facts

`group.layout` is a preset — `single`, `vertical`, `horizontal`, `split`,
`grid` — and above three panes it is **advisory**: `_normalize_layout` forces
`grid` at four or more regardless of what was asked.

The shape a reader actually sees is `workspace_layout`: a per-pane rectangle in
grid coordinates plus fractional track weights. That record is what answers
"the smaller terminals", and it is written only when a local split has
happened — an unsplit group carries `null` and wears one of the preset grids
instead (§4.2).

> `web/saved_sessions.py:138-146` · `web/session_presentation.py:642-712`

### F5 — The split axis never reaches the server

The pane header carries two split buttons — side-by-side and stacked — and
`splitTerminalPane(index, axis)` posts `{ axis }` with the request.
`grep -c axis web/api.py` returns **0**. The server appends a pane; the page
then computes `splitSlotRect(rect, axis)`, splices its own rect list, and
persists the result through `POST /api/session-presentation`.

The page also owns every refusal. `getSplitCandidates` requires a rect at least
two tracks wide (or tall) to halve, each half to keep `MIN_SPLIT_COLS = 8`
columns and `MIN_SPLIT_ROWS = 4` rows measured from the live pane, and refuses
outright below a 700 px viewport or at `MAX_SPLIT_TERMINALS`.

A sidecar posting to `/split` therefore gets a pane with **no geometry at all**,
and none of those refusals were consulted.

> `web/static/js/terminals.js:4030,4047,6885-6989` · `web/api.py:3006`

### F6 — Phase 0's layout enum is wrong

`LAYOUTS` in the sidecar reads `("single", "split", "grid", "stack")`.

`stack` is not a GridVibe layout. `vertical` and `horizontal` — the only two
`_normalize_layout` accepts at two panes — are **missing**, so the schema would
refuse them. The Phase 0 acceptance scenario passed only because three panes
plus `split` happens to be a legal pair.

The real set is `single | vertical | horizontal | split | grid`.

> `gridvibe_mcp/server.py:55` · `web/saved_sessions.py:138`

### F7 — A split always produces a plain terminal, and the tool sends nothing

The route hardcodes `initial_command=None`, `initial_command_mode="command"`,
`agent_selection=""` and titles the pane `Terminal N`. It clones the shell
family and the directory — an explorer pane splits off a terminal rooted where
it is browsing — and deliberately does not clone the pane kind.

It accepts a `directory` in the body, and only from an explorer or browser
source. The sidecar's `split_pane` does not even send that: it calls
`client.split(session_id, {})`.

> `web/api.py:3006-3085` · `gridvibe_mcp/server.py:513-518`

### F8 — The relaunch verb exists and is complete; only the tool is absent

`POST /api/sessions/<id>/shell` relaunches a pane under a stated shell family
and agent, and since `7ec35f6` it also carries a tri-state `mcp` flag, so a
running pane can be given GridVibe tools without going back to the launcher.
Each dimension is read tri-state: an unstated one is left alone.

As a tool that is `set_pane_agent`, and Phase 0 left it out of the build rather
than behind a flag — a relaunch **kills whatever is running in the pane**,
which is exactly what a prompt-injected agent should not be able to reach.

The server work for it is zero. What remains is entirely a blast-radius
decision, which is why it is D6 rather than a work item.

> `web/session_shell.py:120-131,311-352` · `gridvibe_mcp/server.py:46-51`

---

## 4. What "smaller", "above" and "below" mean

A pane's size is never stored. It is derived: each pane holds a rectangle in a
CSS grid, and each grid track carries a weight. A pane's area is the sum of its
spanned column weights times the sum of its spanned row weights.

### 4.1 The geometry record

As `_normalize_workspace_layout` validates it:

```json
{
  "class_name": "layout-split-local",
  "split_slot_rects": [
    { "originSlot": 0, "x": 1, "y": 1, "w": 2, "h": 2 },
    { "originSlot": 1, "x": 3, "y": 1, "w": 1, "h": 1 },
    { "originSlot": 2, "x": 3, "y": 2, "w": 1, "h": 1 }
  ],
  "split_column_weights": [1.4, 1.4, 0.8],
  "split_row_weights":    [1, 1],
  "original_split_slot_count": 3
}
```

Rectangles are 1-based, in pane order, one per pane. Weights are clamped to
`0.01 … 100`. A geometry record is **all-or-nothing** — one unrepresentable
rectangle and the whole record is refused rather than clamped, because clamping
would silently assign a rectangle to a different pane.

Three equal rectangles over unequal tracks are three different sizes, so
"the smaller terminals" needs the rectangles **and** the weights. Publishing
one without the other is worse than publishing neither.

### 4.2 When there is no record

The page writes `workspace_layout` only for `layout-split-local` — a group that
has actually been split locally. Every other group wears a preset grid, whose
tracks are fixed in `terminals.css` and whose pane placement is implicit in DOM
order:

| Panes | Layout | Columns × rows | Notes |
| --- | --- | --- | --- |
| 1 | `single` | 1 × 1 | |
| 2 | `vertical` | `1fr 1fr` × 1 | side by side |
| 2 | `horizontal` | 1 × `1fr 1fr` | stacked |
| 3 | `vertical` | 3 × 1 | |
| 3 | `horizontal` | 1 × 3 | |
| 3 | `split` | `2fr 1fr` × `1fr 1fr` | pane 3 spans both rows |
| 4–5 | `grid` | 2 × 2 | `getGridMetrics` |
| 6–7 | `grid` | 3 × 2 | |
| 8+ | `grid` | 4 × 2 | |

This table is a real duplication risk: it exists today as CSS classes plus
`getGridMetrics` in `web/static/js/shared.js`, and deriving it in Python makes
a second copy. D3 states where the copy lives and what pins it.

### 4.3 Adjacency — what "below" means

With rectangles in hand, adjacency is exact and needs no measurement:

- **below** — panes whose `y == mine.y + mine.h` and whose column span
  `[x, x+w)` overlaps mine.
- **above** — panes whose `y + h == mine.y`, overlapping columns.
- **right** — panes whose `x == mine.x + mine.w`, overlapping rows.
- **left** — panes whose `x + w == mine.x`, overlapping rows.

Each direction is a **list**, ordered along the cross axis, because a tall pane
can sit above two stacked ones. "The terminal below me" resolving to two panes
is an honest refusal that names both, never a pick. That refusal is the whole
reason adjacency is published as lists rather than as four optional ids.

---

## 5. Where a split is decided

This is the finding that shapes the phase. The split button looks like an HTTP
call and is not one.

```text
  today
  ─────
  page ──POST /api/sessions/<id>/split { axis }──▶ server
                                                    │ appends one plain terminal
                                                    │ never reads axis
  page ◀───────────────── session ───────────────────┘
  page: splitSlotRect(rect, axis) → splices its own rect list
  page ──POST /api/session-presentation { workspace_layout }──▶ server

  sidecar ──POST /api/sessions/<id>/split {}──▶ server
  sidecar ◀── a pane with no geometry, placed nowhere
```

The axis is spent entirely on the page. A process outside the browser can
append a pane but cannot place it.

Two ways forward:

**Move the geometry server-side.** Reimplement the rect arithmetic, the
minimum-size refusals and the viewport rule somewhere that cannot measure a
pane. `getSplitCandidates` reads `estimatePaneCharacters(index, axis)` off the
live terminal — the server would have to guess at what the page can see, and a
wrong guess produces an 8×4 pane nobody can use.

**Make a split an intent.** The page that can measure the pane performs the
split, using the button's own handler, so the minimum-size rules and the rect
arithmetic stay in their one existing home. Phase 0 already built this shape
for `open_window`:

```text
  sidecar ──split_pane(session_id, axis, …)──▶ intent store (TTL, claim-once)
                                                      ▲ claims      │ polls
                                                      │             ▼
                                              the page runs splitTerminalPane()
                                              and reports split | refused |
                                              no_window_available
```

Same three honest outcomes as `open_window`, and the same honesty: it never
retries and it never pretends.

---

## 6. Decisions

### D1 — Position and layout are reads, folded into `list_panes`

Not a new tool. One call answers "what is here and how is it arranged":

- each pane gains an `index` (its position in `group.pane_order`),
- each pane gains its `rect` and a derived `relative_area`, so "the smaller
  terminals" needs no grid arithmetic from the agent,
- each pane gains `neighbours: { above, below, left, right }` as lists of
  session ids (§4.3),
- the result gains a `layout` block: the group's layout name, whether that name
  is advisory, and the geometry record itself.

`index`, `rect` and `neighbours` are only meaningful within one group, so they
are published **only when a `group_id` is resolved** — including the default
case, where `list_panes` uses the caller's own group. A workspace-wide read
across several groups states `index: null` rather than an index that means
nothing.

The same `index` goes back into the dashboard rows that `AGENT_FIELDS`
currently drops (F1).

### D2 — `whoami` gains its own position

`whoami` already reads the caller's pane. It gains `index`, `rect`, and the
same `neighbours` block, so "the terminal below this one" is answerable in one
call rather than a `list_panes` plus a search for one's own session id.

### D3 — The implied preset geometry is derived in one Python owner, pinned to the CSS

A new `web/pane_geometry.py` owns exactly two things: the §4.2 preset table and
the §4.3 adjacency rules. It is the only place either exists in Python.

The duplication with `shared.js` is real and is accepted knowingly, because the
alternative — persisting a `workspace_layout` for every group so the page
becomes the single source — churns presentation revisions on every launch for a
benefit no user can see. What makes the copy safe is a test that reads
`terminals.css` and asserts the declared tracks of each layout class match the
table, plus one asserting `getGridMetrics`' three breakpoints match. A source
assertion on CSS and named constants is the sanctioned kind.

### D4 — A split is an intent, not an HTTP call

Because the axis is a page decision (F5). `web/window_intents.py` gains a second
intent kind; its TTL, claim-once rule and poll are reused unchanged. The page
claims it and runs `splitTerminalPane` — the same handler the button runs,
including `getSplitCandidates` — and reports one of:

- `split`, with the new pane,
- `refused`, with GridVibe's own sentence and the axis that *would* have worked
  if either does,
- `no_window_available`, when the intent expired unclaimed.

In browser mode there is no page to claim it, so a split reports
`no_window_available`. This is the one place Phase 0.5 is weaker in browser mode
than native, and it is worth stating rather than hiding. (`open_window` has a
browser-mode fallback because `webbrowser.open` is a real alternative; there is
no equivalent for "measure this pane".)

### D5 — The split verb says what to create

The split route already accepts a `directory`. It gains the new pane's kind,
agent and MCP flag, so an agent pane is **created directly** instead of
created-then-converted.

This is the shape that keeps the destroy tier empty: the agent gets a verb that
only ever configures a pane it just made, and no verb that can relaunch a pane
someone is working in. It is also what makes chained splits possible — each
split's result names the new pane, which the next split can target.

### D6 — Open: the word "each" in Prompt C

*"Split Terminal 4 and run codex and claude in each"* asks for two agent panes
where one terminal stood. D5 covers the pane that is created. The **existing**
Terminal 4 still has to become an agent pane, and that is a relaunch — the tool
Phase 0 deliberately did not build (F8). Prompt D has exactly the same shape:
the terminal below already exists.

Three ways out, and this one is the user's to pick:

1. **Narrow it.** A `set_pane_agent` that refuses any pane not in plain
   terminal mode, so it can never interrupt a running agent — but it can still
   interrupt a shell mid-command.
2. **Lineage-gate it.** Allow it only on panes this agent itself created, using
   the `agent_depth` stamp already in the session record
   (`sessions/manager.py:98,183`). Safe, and it makes the prompt fail on a pane
   the user made by hand — which is most of them.
3. **Reinterpret it.** Split Terminal 4 twice and leave the original alone.
   Honest, cheap, and not what the sentence says.

Options 1 and 2 compose: gate on lineage *and* on plain-terminal mode. That is
the narrowest thing that still makes Prompt D work for a pane the agent made.

### D7 — Saved presets are readable, through their own field list

Prompt B needs the saved launcher presets, and they already carry everything it
wants: `layout`, `terminal_count`, and a `workspace_layout` per preset.

**The read is not `GET /api/saved-sessions/<id>` as it stands.** That route
answers with `include_config=True`, and a saved config holds **decrypted** SSH
passwords — `_normalize_stored_payload` decrypts on load by design. A tool that
forwarded it would hand an agent a credential.

So the sidecar gains `SAVED_LAYOUT_FIELDS` in `gridvibe_mcp/client.py`, beside
the existing allowlists, projected through the same `scrub()` that enforces
`FORBIDDEN_KEY_MARKERS`. It publishes shape and never connection: preset id and
name, layout, terminal count, the geometry record, and per-pane `startup_mode`,
`title` and `agent_selection`. No host, no username, no port, no password — a
preset launched by an agent runs on the agent's own pane's machine via
`origin_session_id`, exactly as `launch_panes` already does.

> `web/saved_sessions.py:713,849-865` · `web/api.py:2945-2956`

---

## 7. The surface after 0.5

| Tool | Change | Serves |
| --- | --- | --- |
| `list_panes` | Gains `index`, `rect`, `relative_area` and `neighbours` per pane, and a `layout` block | A, C, D |
| `whoami` | Gains its own `index`, `rect` and `neighbours` | D |
| `list_agents` | Stops dropping `index` | position |
| `launch_panes` | Accepts `workspace_layout`; layout enum corrected (F6) | A, B |
| `list_saved_layouts` | **New.** Saved presets through their own field list | B |
| `split_pane` | Gains `axis`, and what to create in the new pane | C, D |
| `set_pane_agent` | **Undecided** — see D6 | C and D's "each" |
| `close` · `move` · `resize` · `send_input` | Still absent | — |

---

## 8. Work plan

Each step lands something usable on its own.

**1. Publish position and layout.**
`index` through the dashboard allowlist and `list_panes`; the group's layout
name, its geometry record, and a derived per-pane area. Read-only — no new
verbs, no new risk. *Lands:* the agent can describe the workspace it is in,
including which pane is its own.

**2. Derive the implied geometry and adjacency.**
`web/pane_geometry.py` (D3), with the CSS-pinned test. `neighbours` on
`list_panes` and `whoami`. *Lands:* "the terminal below this one" resolves to a
session id, or refuses naming both candidates.

**3. Correct the layout enum.**
`single | vertical | horizontal | split | grid`, with a schema note that above
three panes the name is advisory and the geometry is what holds. *Lands:* a
two-pane launch can finally ask for `horizontal`.

**4. `launch_panes` carries a geometry.**
Pass `workspace_layout` through; the existing normalizer validates it (F3).
*Lands:* Prompt A passes end to end. A real checkpoint — useful shipped alone.

**5. Saved layouts become readable.**
`list_saved_layouts` with `SAVED_LAYOUT_FIELDS` (D7), and a test asserting no
credential-shaped key survives the projection. *Lands:* Prompt B passes.

**6. Split intents.**
A second intent kind in the existing store; the claim policy and poll already
exist. The page runs the button's own handler and reports which of the three
outcomes happened. *Lands:* an agent can split a pane on a chosen axis, and a
pane too small to halve says so.

**7. The split says what to create.**
Kind, agent and MCP flag on the split route, beside the `directory` it already
takes. *Lands:* "split this and run claude in the new pane" works with no
relaunch verb in the build — and chained splits work, because each answer names
its pane.

**8. Settle D6.**
Whichever of the three the user picks. If it is a new verb, its refusals are
asserted on a whole-pane snapshot the way `test_session_modes.py` and
`test_session_shell.py` already assert theirs. *Lands:* Prompts C and D pass, or
are reported honestly as half-supported.

**9. Docs and the changelog.**
The sidecar README's tool table, README feature bullets in the house style, one
`(feat)` changelog bullet per the CHANGELOG shape in `CLAUDE.md`. *Lands:*
`make check` green.

---

## 9. Failure modes to verify

| Condition | Required behaviour |
| --- | --- |
| Pane too small to halve on the asked axis | Refused with the reason, and the axis that *would* work if either does. Never split on the other axis instead |
| Browser mode, or no page open | `no_window_available`; the panes and the workspace are untouched |
| Group already at `MAX_SPLIT_TERMINALS` | GridVibe's own capacity sentence, unretried |
| Viewport below 700 px | The page's own refusal, reported as `refused` — not worked around |
| Four or more panes with a named layout | The layout name is reported advisory; the geometry is what the agent reads back |
| A group with no geometry record | The preset rectangles are derived (§4.2) and marked as implied, never invented |
| Two panes below the caller | `neighbours.below` lists both; a verb asking for "the one below" refuses and names them |
| The pane changed hands between the read and the split | The captured session id no longer matches — refuse, do not split its replacement |
| A geometry with one bad rectangle | The whole record is refused, never partially applied |
| Two pages open when a split intent lands | One split, exactly as one window opens today |
| A saved preset carrying an SSH password | The password never appears in a tool result; the projection test asserts it |
| `list_panes` across a whole workspace | `index` is `null` and no `rect` or `neighbours` are published |

---

## 10. The risk this phase adds

Phase 0's create tier could only make **new** things. Its whole safety argument
was that a prompt-injected agent could waste resources but could not touch work
in progress.

Phase 0.5 lets an agent rearrange a workspace the user is looking at. A split
reflows the panes around it. And if D6 admits a relaunch verb, an agent can
interrupt work in a pane it did not create.

That is a genuine widening of what a prompt-injected agent can reach, and it is
the reason D6 is a question here rather than a decision. The mitigations that
survive whatever is picked:

- The destroy tier stays empty. Nothing in this phase closes a pane, a group or
  a workspace.
- Every split is performed by the page, under the page's own refusals — an
  agent cannot produce a pane the user could not have produced with the button.
- The intent store's claim-once rule means two open pages produce one split,
  the same way they produce one window.
- `agent_depth` still bounds recursion: an agent that splits a pane and starts
  an agent in it hands down a depth budget that runs out.

---

## 11. Out of scope, deliberately

- **Typing into a terminal** — `send_input`, and with it "give each agent a
  task". This is the single largest missing piece for the chained-split prompt
  in §0, and it is deliberately a phase of its own: writing keystrokes into a
  pane someone is using is a different blast radius from arranging panes, and it
  deserves its own decision rather than riding in on this one. The nearest thing
  that exists today is launching a pane *with* an agent and an opening command,
  which D5 gives to the split verb.
- **Resizing panes** — moving a divider is a third kind of geometry write, and
  nothing asked for it.
- **Closing a pane, a group or a workspace.**
- **Moving a group between workspaces.**
- **Server-side split geometry** — see §5 for why.
- **Re-rooting or mode-switching an existing pane** — `session_modes.py` and
  `session_root.py` are the same shape of decision as D6 and wait on it.
