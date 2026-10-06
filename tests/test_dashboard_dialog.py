"""The agent dashboard dialog, driven through its own wiring.

`dashboard-dialog.js` is one fetch, one builder and a delegated click, so it is
exercised by *running* it: the real module and the real `agent-identity.js` are
loaded in Node behind a stub page, wired the way the page wires it, and its
markup is parsed back into the rows a reader would see.

What is pinned is what the dashboard is *for*:

- **It is a dialog on the page that opened it.** Opening arms the poll and
  reads once; closing disarms it and aborts what is in flight. It goes away on
  the ×, on a press on the backdrop and on Escape — but Escape only while it is
  the top `.modal-shell` on the page, or cancelling the close prompt it raised
  would take the list away with it.
- **Leaving the window is not a dismissal.** The dialog stays up on the window
  it was raised from while the reader works in another one, which is the whole
  point of a surface that names what is running everywhere. A document nobody
  can *see* stands the poll down rather than dismissing it, and coming back into
  view re-arms it and reads once; a window merely unfocused on another monitor
  is not hidden and never stops reading.
- **A row that names the workspace this page already is lands without opening
  anything.** Raising a window that is already raised fires no `focus` event, so
  the stored target would never be claimed and the row would do nothing at all.
- **Acting closes it only when the act lands here.** A row for the workspace
  this page already is takes the dialog with it, because a dialog over the pane
  it just took you to is in the way; a row for anywhere else leaves it standing,
  because that pane came up in another window and this one is covering nothing
  the reader wanted. A close verb ends something and leaves you here, so it
  never closes it either.
- **There is one of it, across every window.** Opening broadcasts a claim and
  the newest open wins. A claim is a notice and never a lock, so nothing can
  refuse to open — a window that died with its dialog up leaves nothing standing
  that could make the button dead somewhere else — and two opens inside one
  round trip cannot annihilate each other.
- **Three levels, drawn as three things.** A workspace is a band, a session is
  a card inside it, an agent is a row inside that — the complaint about the
  dropdown this replaced was that all three were the same list at different
  indents, so the nesting is asserted as structure and not as padding.
- **One row per pane, and the row says everything.** The agent's mark, its
  name and the shell it runs on sit to the left of the chat title on the pane's
  own line — they used to be a heading over a block of sibling panes, which
  bought a saving only when several panes of one agent shared a card and cost
  every other case a two-line entry and a stack per agent.
- **The mark says which agent.** Every row wearing the same glyph said only
  "this is a row"; the mark comes from the registry key the name does, and an
  agent GridVibe has not drawn falls back rather than disappearing.
- **A row says what is running without going to look**: the agent's own name,
  what it is running on, what the pane announced, and whether it is working.
- **A session card is drawn in its own tab's colour.** The hue comes from
  `session-colour.js`, which is the same answer the workspace window's tab
  strip paints, so a card is matched to a tab by colour rather than by reading
  two names in two windows. No colour module, no custom property — the card
  falls back to the dialog's own tokens.
- **The state and the percentage are separate readings.** Every agent has a
  state; only one that speaks the progress sequence has a number. A working
  agent with no number still gets a moving bar, so "no percentage" never reads
  as "stalled at 0%". A pane that is not connected reports that instead.
- **Every row is a way out, to the pane it names.** An agent, its session and
  its workspace all open the window that owns it — and an agent row leaves the
  session tab and the pane waiting for that window to claim, because a window
  that is already open is raised without being reloaded and would otherwise land
  wherever it was left.
- **Values reach the markup escaped**, so an agent that announces markup in its
  window title cannot rewrite the rows.
- **A repaint that changes nothing is not performed**, because this dialog
  stays up while it is read: an identical reading must not drop the caret,
  the selection or the scroll. A reading that *did* change puts the caret back
  on the row it was on and the scroller back where it was.
- **A slow answer never repaints over a newer one**, and a failed read leaves
  the last good tree on screen behind a stated notice rather than blanking it.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
AGENT_IDENTITY_JS = STATIC_JS / "agent-identity.js"
AGENT_GLYPHS_JS = STATIC_JS / "agent-glyphs.js"
SESSION_COLOUR_JS = STATIC_JS / "session-colour.js"
AGENT_CREWS_JS = STATIC_JS / "agent-crews.js"
DASHBOARD_DIALOG_JS = STATIC_JS / "dashboard-dialog.js"
DASHBOARD_SIDEBAR_JS = STATIC_JS / "dashboard-sidebar.js"
LIST_DOM_JS = REPO_ROOT / "tests" / "dashboard_list_dom.js"

NODE = shutil.which("node")

# workspaces.js owns the one wording for a blocked pop-up; the window reports
# it rather than inventing a second.
WORKSPACE_TAB_BLOCKED_HINT = (
    "Allow pop-ups for this site so GridVibe can open workspace tabs."
)

HARNESS_STUBS = r"""
/* shared.js's own escaper, copied rather than neutered: every value reaches the
   parser below through it, so a stub that did not escape would let an
   announced title containing markup rewrite the rows the assertions read. */
function escHtml(value) {
    return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const AGENT_OPTIONS = [
    { value: 'claude', label: 'claude', display_name: 'Claude Code' },
    { value: 'codex', label: 'codex', display_name: 'OpenAI Codex CLI' }
];

/* workspaces.js's own wording for the one browser behaviour it reports. */
const WORKSPACE_TAB_BLOCKED_HINT =
    'Allow pop-ups for this site so GridVibe can open workspace tabs.';

const calls = {
    fetches: 0,
    openWorkspaceWindow: [],
    focusTargets: [],
    workspaceReturns: 0,
    launcherOpens: 0
};

/* workspaces.js's own names, stubbed with the shape the module reads. The
   request is recorded rather than stored: what matters here is that a row asks
   for its own pane, and *before* the window is opened. */
function requestWorkspaceFocusTarget(workspaceId, options) {
    calls.focusTargets.push({
        workspaceId,
        options,
        openedSoFar: calls.openWorkspaceWindow.length
    });
    return true;
}

const WORKSPACE_RETURN_NONE = 'none';
const WORKSPACE_RETURN_FOCUSED = 'focused';
const WORKSPACE_RETURN_OPENED = 'opened';
const WORKSPACE_RETURN_BLOCKED = 'blocked';

/* Swapped per case: what the shared return resolver answered. */
let workspaceReturnOutcome = WORKSPACE_RETURN_FOCUSED;
async function returnToOriginWorkspace() {
    calls.workspaceReturns += 1;
    return { outcome: workspaceReturnOutcome, workspaceId: 'default' };
}

let launcherOpens = true;
async function openLauncherWindow() {
    calls.launcherOpens += 1;
    return launcherOpens;
}
/* Swapped per case: `false` is a browser that blocked the pop-up. */
let workspaceOpens = true;
async function openWorkspaceWindow(workspaceId, options) {
    calls.openWorkspaceWindow.push({ workspaceId, options });
    return workspaceOpens;
}
function workspaceDisplayLabel(workspace, index) {
    return String(workspace && workspace.label ? workspace.label : '')
        || (workspace && workspace.workspace_id === 'default' ? 'Main workspace' : `Workspace ${index + 1}`);
}

function fakeListeners() {
    const handlers = new Map();
    return {
        addEventListener(type, handler) {
            if (!handlers.has(type)) { handlers.set(type, new Set()); }
            handlers.get(type).add(handler);
        },
        removeEventListener(type, handler) {
            handlers.get(type)?.delete(handler);
        },
        listenerCount(type) { return handlers.get(type)?.size || 0; },
        fire(type, event) { [...(handlers.get(type) || [])].forEach(handler => handler(event)); }
    };
}

/* The scroller answers the two questions a render asks it — "is the caret in
   me" and "where is this row" — so the focus-retention path is executable
   rather than a no-op. Both default to "no", which is the ordinary case. */
let focusIsInside = false;
let focusTarget = null;
const focusQueries = [];

/* Enough of a DOMTokenList for the module to write to: the notice line carries
   its tone as a class, so a stub without one would fail every path that
   reports anything. */
function fakeClassList() {
    const names = new Set();
    return {
        names,
        add(name) { names.add(name); },
        remove(name) { names.delete(name); },
        contains(name) { return names.has(name); },
        toggle(name, force) {
            const on = force === undefined ? !names.has(name) : Boolean(force);
            if (on) { names.add(name); } else { names.delete(name); }
            return on;
        }
    };
}

function fakeElement(id) {
    return {
        id,
        innerHTML: '',
        textContent: '',
        hidden: false,
        scrollTop: 0,
        dataset: {},
        style: {},
        attributes: {},
        classList: fakeClassList(),
        setAttribute(name, value) { this.attributes[name] = value; },
        focus() { this.focused = true; },
        contains: () => focusIsInside,
        querySelector(selector) { focusQueries.push(selector); return focusTarget; },
        ...fakeListeners()
    };
}

const byId = new Map();
[
    'agentDashboardShell',
    'agentDashboardBody',
    'agentDashboardTotals',
    'agentDashboardNotice',
    'agentDashboardRefreshBtn',
    'agentDashboardCloseBtn'
].forEach(id => byId.set(id, fakeElement(id)));

/* Swapped per case: another `.modal-shell.visible` above this one, which is
   what a session × raises. */
let otherShellOpen = false;

/* Swapped per case: `false` is this window no longer being the one in front,
   which is what the close asks before it moves focus anywhere. `activeElement`
   deliberately does *not* move with it — that is exactly how a real page
   behaves when its window is deactivated, and it is why `hasFocus()` has to be
   asked at all. */
let documentFocused = true;

const document = {
    activeElement: null,
    hidden: false,
    /* The page's own body. Nothing in this module may write to it: the scrim is
       the shell's, and the cross-window dim that used to land here is gone. */
    body: fakeElement('body'),
    hasFocus: () => documentFocused,
    getElementById: id => byId.get(id) || null,
    /* The Escape guard asks the page which shells are up. This one is the
       dialog itself; the other is whatever it raised on top of itself. */
    querySelectorAll: selector => {
        if (selector !== '.modal-shell.visible') { return []; }
        const open = [];
        if (shell().classList.contains('visible')) { open.push(shell()); }
        if (otherShellOpen) { open.push({ id: 'somethingElse' }); }
        return open;
    },
    ...fakeListeners()
};

function shell() { return byId.get('agentDashboardShell'); }
function dialogOpen() { return shell().classList.contains('visible'); }

/* The page's own sequence: wire it, then press the button. A case that wants
   the reading without the lifecycle marks the shell up directly instead. */
function showDashboard() {
    wireAgentDashboard();
    openAgentDashboardDialog();
}

/* Up, with none of the module's side effects: the cases below that are about
   the *reading* were written against a surface that was always on screen, and
   putting the whole open sequence in front of each of them would spend a fetch
   on an answer they have not set up yet. */
function dashboardShown() {
    shell().classList.add('visible');
}

/* A key press, as the document delivers one. */
function press(key, overrides) {
    let prevented = false;
    document.fire('keydown', Object.assign({
        key,
        preventDefault() { prevented = true; }
    }, overrides || {}));
    return prevented;
}

/* window is the real global object so the UMD module below can attach itself
   where dashboard-dialog.js looks for it. */
const window = globalThis;
const timers = { armed: 0, cleared: 0 };
/* Node's global object is not an EventTarget, so the window-level listeners the
   module registers (`focus`, `pagehide`, `pageshow`, `pywebviewready`) need a
   ledger of their own — without one they are silently never registered and the
   `focus` reader, which is the one that fires every time the reader clicks back
   into the page, could not be exercised at all. */
const windowListeners = fakeListeners();
Object.assign(globalThis, {
    /* Real timers would keep the process alive; the cadence itself is not what
       these cases are about, only whether one is armed. */
    setInterval: () => { timers.armed += 1; return timers.armed; },
    clearInterval: () => { timers.cleared += 1; },
    addEventListener: windowListeners.addEventListener,
    removeEventListener: windowListeners.removeEventListener,
    fireWindow: windowListeners.fire
});

let fetchAnswer = null;
globalThis.fetch = async () => {
    calls.fetches += 1;
    const answer = typeof fetchAnswer === 'function' ? await fetchAnswer() : fetchAnswer;
    if (answer === null) {
        return { ok: false, status: 500, json: async () => ({}) };
    }
    return { ok: true, status: 200, json: async () => answer };
};

function body() { return byId.get('agentDashboardBody'); }
function totals() { return byId.get('agentDashboardTotals'); }
function notice() { return byId.get('agentDashboardNotice'); }

function settle() { return new Promise(resolve => setTimeout(resolve, 0)); }

function camel(name) {
    return name.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
}

/* How many of each section the reader is looking at: the nesting is the point,
   so it is counted rather than inferred from the row list. */
function sectionCounts() {
    const count = pattern => (renderedListHtml().match(pattern) || []).length;
    return {
        workspaces: count(/<section class="dash-workspace[ "]/g),
        sessions: count(/<section class="dash-session[ "]/g),
        agents: count(/class="dash-agent"/g)
    };
}

/* One agent row, read back as the reader meets it: the mark, the agent's name,
   the shell it claims and what it announced -- all four on the one line, in the
   order they are painted, which is what replaced the heading-and-block. */
function parseAgentRows() {
    const rows = [];
    const pattern = /<button\b[^>]*class="dash-agent"([^>]*)>([\s\S]*?)<\/button>/g;
    let found;
    while ((found = pattern.exec(renderedListHtml())) !== null) {
        const agent = /data-agent="([^"]*)"/.exec(found[1]);
        const inner = found[2];
        const name = /<span class="dash-agent-who">([\s\S]*?)<\/span>/.exec(inner);
        /* The shell, off the last line of the row's hover: the chip that used
           to state it on the line itself is gone. */
        const hover = /title="([^"]*)"/.exec(found[1]);
        const transport = hover && hover[1].includes('\n') ? hover[1].split('\n').pop() : '';
        const glyph = /(?:<svg class="dash-agent-glyph"[\s\S]*?<\/svg>|<img class="dash-agent-glyph"[^>]*>)/.exec(inner);
        const line = /<span class="dash-agent-line">([\s\S]*?)<\/span>/.exec(inner);
        rows.push({
            agent: agent ? agent[1] : '',
            name: name ? name[1].trim() : '',
            transport: transport.trim(),
            glyph: glyph ? glyph[0] : '',
            line: line ? line[1].trim() : ''
        });
    }
    return rows;
}

/* The colour a session card is drawn in, as it reaches the markup: the inline
   custom properties `session-colour.js` supplies, keyed by group id. */
function parseSessionColours() {
    const colours = [];
    const pattern = /<section class="dash-session" style="([^"]*)"[\s\S]*?data-dashboard-key="session:([^"]*)"/g;
    let found;
    while ((found = pattern.exec(renderedListHtml())) !== null) {
        const edge = /--dash-session-color:([^;"]*)/.exec(found[1]);
        const soft = /--dash-session-color-soft:([^;"]*)/.exec(found[1]);
        colours.push({
            groupId: found[2],
            colour: edge ? edge[1].trim() : '',
            soft: soft ? soft[1].trim() : ''
        });
    }
    return colours;
}

/* The rendered window, read back as the reader meets it. */
function parseRows() {
    const rows = [];
    const pattern = /<button\b([^>]*)>([\s\S]*?)<\/button>/g;
    let found;
    while ((found = pattern.exec(renderedListHtml())) !== null) {
        const attributes = {};
        const dataset = {};
        const attributePattern = /([a-zA-Z-]+)="([^"]*)"/g;
        let attribute;
        while ((attribute = attributePattern.exec(found[1])) !== null) {
            attributes[attribute[1]] = attribute[2];
            if (attribute[1].startsWith('data-')) {
                dataset[camel(attribute[1].slice(5))] = attribute[2];
            }
        }
        const inner = found[2];
        const label = /<span class="dash-(?:agent-line|session-name|workspace-name)">([\s\S]*?)<\/span>/.exec(inner);
        const state = /class="dash-activity dash-state-([a-z]+)"/.exec(inner);
        const stateHover = /class="dash-activity dash-state-[a-z]+" title="([^"]*)"/.exec(inner);
        const word = /<span class="dash-state-word">([\s\S]*?)<\/span>/.exec(inner);
        const percent = /<span class="dash-progress-value">(\d+)%<\/span>/.exec(inner);
        /* Which shell the agent runs on, read off the last line of the row's
           own hover -- which is where it went when the chip that used to state
           it gave the width back to the chat title. `tags` stays what it always
           was: the markers that are true of *this* row and not of every one of
           its siblings. */
        const hover = attributes['title'] || '';
        const transport = hover.includes('\n') ? hover.split('\n').pop() : '';
        const tagHovers = [];
        const tagClasses = [];
        const tags = [];
        /* A chip may carry a hover of its own -- `MCP` is three characters the
           reader may not recognise -- so the attributes after the class are
           read past rather than required to be absent. */
        const tagPattern = /<span class="(dash-tag[^"]*)"([^>]*)>([\s\S]*?)<\/span>/g;
        let tag;
        while ((tag = tagPattern.exec(inner)) !== null) {
            tags.push(tag[3].trim());
            tagClasses.push(tag[1].split(/\s+/).filter(Boolean));
            const tagHover = /\btitle="([^"]*)"/.exec(tag[2]);
            tagHovers.push(tagHover ? tagHover[1] : '');
        }
        rows.push({
            kind: attributes['data-dashboard-action'] || '',
            key: attributes['data-dashboard-key'] || '',
            dataset,
            label: label ? label[1].trim() : '',
            /* The row's hover, which is where the line's shortened-away path
               went. Read as the attribute rather than as rendered text: it is
               the only place a value spans two lines. */
            hover,
            agent: attributes['data-agent'] || '',
            name: /<span class="dash-agent-who">([\s\S]*?)<\/span>/.exec(inner)?.[1].trim() || '',
            transport: transport.trim(),
            tags,
            tagHovers,
            tagClasses,
            state: state ? state[1] : '',
            stateHover: stateHover ? stateHover[1].trim() : '',
            word: word ? word[1].trim() : '',
            hasBar: inner.includes('dash-progress-fill'),
            indeterminate: inner.includes('is-indeterminate'),
            percent: percent ? Number(percent[1]) : null,
            /* The row's own markup, for the assertion that is about where its
               columns are rather than about what any one of them says. */
            html: inner
        });
    }
    return rows;
}

function rowFor(key) {
    const row = parseRows().find(candidate => candidate.key === key);
    if (!row) { throw new Error(`no row for ${key}`); }
    return row;
}

/* A click, dispatched the way the page dispatches one: through the delegated
   listener, with a target that resolves to the row that was pressed. */
function clickRow(key) {
    const row = rowFor(key);
    body().fire('click', {
        target: { closest: () => ({ dataset: row.dataset }) },
        preventDefault() {}
    });
}

function pane(overrides) {
    return Object.assign({
        session_id: 's1',
        group_id: 'g1',
        workspace_id: 'default',
        index: 0,
        title: 'Terminal 1',
        host: '10.0.0.5',
        directory: '/srv/app',
        mode: 'ssh',
        startup_mode: 'agent',
        agent_selection: 'claude',
        custom_agent: '',
        agent_auto_mode: false,
        agent_mcp: false,
        use_wsl: false,
        use_powershell: false,
        distribution: '',
        status: 'connected',
        activity: null
    }, overrides || {});
}

function activity(overrides) {
    return Object.assign({
        state: 'working',
        title: '',
        idle_seconds: 0,
        progress_state: '',
        progress_value: 0
    }, overrides || {});
}

function group(panes, overrides) {
    const list = panes || [pane()];
    return Object.assign({
        group_id: 'g1',
        workspace_id: 'default',
        name: 'API work',
        is_active: true,
        pane_count: list.length,
        agent_count: list.length,
        panes: list
    }, overrides || {});
}

/* A session the server kept and sorted last: it is listed so the reader can
   get to it, and it carries no agent row because it holds no agent. Its own
   builder rather than an override, because `agent_count: 0` beside a non-empty
   pane list is not a state the payload can be in. */
function quietGroup(overrides) {
    return group([], Object.assign({
        group_id: 'g9',
        name: 'Notes',
        is_active: false,
        pane_count: 3,
        agent_count: 0
    }, overrides || {}));
}

function snapshot(groups, overrides) {
    const list = groups || [group()];
    const agents = list.reduce((total, entry) => total + entry.panes.length, 0);
    return Object.assign({
        generated_at: 100,
        workspaces: [{
            workspace_id: 'default',
            label: '',
            active_group_id: 'g1',
            group_count: list.length,
            agent_count: agents,
            groups: list
        }],
        totals: { workspaces: 1, sessions: list.length, agents }
    }, overrides || {});
}

/* shared.js's identity for this document — every cross-window message in the
   app is tagged with it, and this dialog's claim is no exception. */
let GRIDVIBE_WINDOW_ID = 'window-a';

/* The two transports the claim rides, as a ledger. `BroadcastChannel` is
   modelled the way the real one behaves and the way this module depends on:
   a post never reaches the object that sent it, but it *does* reach every
   other channel object — including the listening one in this same document,
   which is what makes skipping our own `source` load-bearing rather than
   tidy. */
const channels = new Set();
const posted = [];
globalThis.BroadcastChannel = class {
    constructor(name) { this.name = name; channels.add(this); }
    postMessage(data) {
        posted.push({ name: this.name, data });
        channels.forEach(other => {
            if (other !== this && other.name === this.name) { other.onmessage?.({ data }); }
        });
    }
    close() { channels.delete(this); }
};

const stored = new Map();
globalThis.localStorage = {
    getItem: key => (stored.has(key) ? stored.get(key) : null),
    setItem: (key, value) => stored.set(key, value),
    removeItem: key => stored.delete(key)
};

/* Another window raising its own dialog, seen from here. `at` defaults to a
   moment after this window's own claim, which is the ordinary case: the reader
   pressed the button over there second. */
function claimFrom(source, overrides) {
    return Object.assign({
        source,
        at: Date.now() + 1000,
        nonce: 'n'
    }, overrides || {});
}

/* Through the channel, which is how a claim arrives when the browser has one. */
function broadcastClaim(claim) {
    const sender = new BroadcastChannel('gridvibe.dashboardOpen');
    sender.postMessage(claim);
    sender.close();
}

/* And through `storage`, which is how it arrives when it has not. */
function storageClaim(claim, key) {
    fireWindow('storage', {
        key: key === undefined ? 'gridvibe.dashboardOpen' : key,
        newValue: claim === null ? null : JSON.stringify(claim)
    });
}

/* This window's own last published claim, read back out of the ledger. */
function myClaim() {
    return JSON.parse(stored.get('gridvibe.dashboardOpen') || 'null');
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard dialog tests")
class DashboardDialogTestCase(unittest.TestCase):
    # Load the shipped dialog and its shared list without source switches.
    def _dialog_source(self) -> str:
        return DASHBOARD_DIALOG_JS.read_text(encoding="utf-8")

    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + LIST_DOM_JS.read_text(encoding="utf-8")
            + AGENT_IDENTITY_JS.read_text(encoding="utf-8")
            + AGENT_GLYPHS_JS.read_text(encoding="utf-8")
            + SESSION_COLOUR_JS.read_text(encoding="utf-8")
            + AGENT_CREWS_JS.read_text(encoding="utf-8")
            + self._dialog_source()
            + (REPO_ROOT / "tests" / "dashboard_list_exports.js").read_text(encoding="utf-8")
            + DASHBOARD_SIDEBAR_JS.read_text(encoding="utf-8")
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class DashboardDialogStructureTestCase(DashboardDialogTestCase):
    def test_zero_progress_and_stale_or_disconnected_progress(self):
        result = self._run_node(
            """
            dashboardShown();
            const readings = [
                pane({ activity: activity({ progress_state: 'normal', progress_value: 0 }) }),
                pane({ activity: activity({ progress_state: 'normal', progress_value: 65, progress_fresh: false }) }),
                pane({ status: 'disconnected', activity: activity({ progress_state: 'normal', progress_value: 65 }) }),
                pane({ status: 'connecting', activity: activity({ progress_state: 'normal', progress_value: 65 }) })
            ];
            report(readings.map(p => ({
                indicator: dashboardActivityHtml(p),
                progress: dashboardProgressHtml(p)
            })));
            """
        )
        self.assertIn('width:0%', result[0]["progress"])
        self.assertIn('>0%</span>', result[0]["progress"])
        for reading in result[1:]:
            self.assertEqual(reading["progress"], "")
        # The indicator is one dot and its word, on every one of them: a bar
        # drawn there would put the row's first column at the mercy of whichever
        # agent it happens to run.
        for reading in result:
            self.assertNotIn('dash-progress', reading["indicator"])

    def test_chat_changes_and_idle_ages_update_rows_without_rebuilding_buttons(self):
        result = self._run_node(
            """
            dashboardShown();
            const first = pane({ activity: activity({ title: 'First chat', state: 'idle', idle_seconds: 5 }) });
            fetchAnswer = snapshot([group([first])]);
            await refreshAgentDashboard();
            const originalHtml = body().innerHTML;
            const line = { textContent: 'First chat' };
            const reading = { innerHTML: dashboardActivityHtml(first) };
            const progress = { innerHTML: dashboardProgressHtml(first) };
            /* The hover the first render actually gave this row, path and all,
               so an update that dropped it would show here. */
            const row = { classList: fakeClassList(), removeAttribute() {}, dataset: { sessionId: 's1' }, title: dashboardPaneHover(first),
                querySelector: selector => ({
                    '.dash-agent-line': line,
                    '.dash-agent-reading': reading,
                    '.dash-agent-progress': progress
                }[selector]) };
            listBody.querySelectorAll = () => [row];
            body().scrollTop = 123;
            fetchAnswer = snapshot([group([pane({ activity: activity({
                title: 'Renamed chat', state: 'idle', idle_seconds: 70
            }) })])]);
            await refreshAgentDashboard();
            report({ sameButtons: body().innerHTML === originalHtml, line: line.textContent,
                tooltip: row.title, reading: reading.innerHTML, scroll: body().scrollTop });
            """
        )
        self.assertTrue(result["sameButtons"])
        self.assertEqual(result["line"], "Renamed chat")
        # The line is shortened to a leaf, and that is only lossless because
        # the hover still carries the whole path. An in-place update writes the
        # hover's own value, never the line's.
        self.assertEqual(result["tooltip"], "Renamed chat\n10.0.0.5: /srv/app\nSSH")
        self.assertIn("Idle 1m", result["reading"])
        self.assertEqual(result["scroll"], 123)

    def test_waiting_on_another_agent_is_a_reading_updated_in_place(self):
        """`waiting` overrides the activity reading after the transport, in the
        crew module's words, and it is a reading: an agent entering
        `wait_for_results` changes its dot and never rebuilds its row."""
        result = self._run_node(
            """
            dashboardShown();
            const busy = pane({ activity: activity({ state: 'working' }) });
            fetchAnswer = snapshot([group([busy])]);
            await refreshAgentDashboard();
            const originalHtml = body().innerHTML;
            const reading = { innerHTML: dashboardActivityHtml(busy) };
            const progress = { innerHTML: dashboardProgressHtml(busy) };
            const row = { classList: fakeClassList(), removeAttribute() {}, dataset: { sessionId: 's1' }, title: dashboardPaneHover(busy),
                querySelector: selector => ({
                    '.dash-agent-line': { textContent: '' },
                    '.dash-agent-reading': reading,
                    '.dash-agent-progress': progress
                }[selector]) };
            listBody.querySelectorAll = () => [row];
            fetchAnswer = snapshot([group([pane({ waiting: 'crew', activity: activity({ state: 'working' }) })])]);
            await refreshAgentDashboard();
            const words = ['task', 'crew', ''].map(waiting => dashboardPaneStateWord(pane({ waiting })));
            report({
                sameButtons: body().innerHTML === originalHtml,
                reading: reading.innerHTML,
                words,
                disconnected: dashboardPaneStateKey(pane({ waiting: 'crew', status: 'disconnected' }))
            });
            """
        )
        self.assertTrue(result["sameButtons"])
        self.assertIn("dash-state-waiting", result["reading"])
        self.assertIn("Waiting on its crew", result["reading"])
        self.assertEqual(
            result["words"],
            ["Standing by for its next task", "Waiting on its crew", "No output yet"],
        )
        self.assertEqual(result["disconnected"], "error")

    def test_a_pane_that_only_moved_updates_the_hover_its_line_does_not_show(self):
        """`directory` is deliberately absent from the structure key, so a pane
        that has only changed directory takes the in-place path -- and its line,
        which is the chat title the agent announced, does not move with it. The
        hover is the only thing on the row that says where the pane now is, so
        it is the thing that has to be rewritten."""
        result = self._run_node(
            """
            dashboardShown();
            const before = pane({ activity: activity({ title: 'Fix the parser', state: 'working' }) });
            fetchAnswer = snapshot([group([before])]);
            await refreshAgentDashboard();
            const line = { textContent: dashboardPaneLine(before) };
            const reading = { innerHTML: dashboardActivityHtml(before) };
            const progress = { innerHTML: dashboardProgressHtml(before) };
            const row = { classList: fakeClassList(), removeAttribute() {}, dataset: { sessionId: 's1' }, title: dashboardPaneHover(before),
                querySelector: selector => ({
                    '.dash-agent-line': line,
                    '.dash-agent-reading': reading,
                    '.dash-agent-progress': progress
                }[selector]) };
            listBody.querySelectorAll = () => [row];
            const originalHtml = body().innerHTML;
            fetchAnswer = snapshot([group([pane({
                directory: '/srv/app/worker',
                activity: activity({ title: 'Fix the parser', state: 'working' })
            })])]);
            await refreshAgentDashboard();
            report({ sameButtons: body().innerHTML === originalHtml,
                line: line.textContent, tooltip: row.title });
            """
        )
        self.assertTrue(result["sameButtons"])
        self.assertEqual(result["line"], "Fix the parser")
        self.assertEqual(
            result["tooltip"], "Fix the parser\n10.0.0.5: /srv/app/worker\nSSH"
        )

    def test_a_hidden_page_aborts_its_request_and_ignores_the_late_result(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const initial = body().innerHTML;
            let resolve, signal;
            globalThis.fetch = (_url, options) => {
                signal = options.signal;
                return new Promise(done => { resolve = done; });
            };
            const pending = refreshAgentDashboard();
            document.hidden = true;
            document.fire('visibilitychange');
            resolve({ ok: true, json: async () => snapshot([], { workspaces: [] }) });
            await pending;
            report({ aborted: signal.aborted, unchanged: initial === body().innerHTML });
            """
        )
        self.assertEqual(result, {"aborted": True, "unchanged": True})

    def test_stalled_reads_time_out_and_the_next_refresh_recovers(self):
        result = self._run_node(
            """
            dashboardShown();
            const realTimeout = globalThis.setTimeout;
            let expire;
            globalThis.setTimeout = (fn, delay) => { expire = fn; return 0; };
            globalThis.fetch = (_url, { signal }) => new Promise((_resolve, reject) => {
                signal.addEventListener('abort', () => reject(new Error('aborted')));
            });
            const pending = refreshAgentDashboard();
            expire();
            await pending;
            const failed = !notice().hidden;
            globalThis.setTimeout = realTimeout;
            globalThis.fetch = async () => ({ ok: true, json: async () => snapshot() });
            await refreshAgentDashboard();
            report({ failed, recovered: notice().hidden, agents: sectionCounts().agents });
            """
        )
        self.assertEqual(result, {"failed": True, "recovered": True, "agents": 1})

    def test_a_successful_poll_does_not_erase_a_failed_navigation(self):
        result = self._run_node(
            """
            dashboardShown();
            workspaceOpens = false;
            await openDashboardTarget({ workspaceId: 'default' });
            const failure = notice().textContent;
            fetchAnswer = snapshot();
            await refreshAgentDashboard();
            const afterPoll = notice().textContent;
            workspaceOpens = true;
            await openDashboardTarget({ workspaceId: 'default' });
            report({ failure, afterPoll, cleared: notice().hidden });
            """
        )
        self.assertTrue(result["failure"])
        self.assertEqual(result["failure"], result["afterPoll"])
        self.assertTrue(result["cleared"])

    def test_malformed_response_preserves_the_last_tree(self):
        result = self._run_node(
            """
            dashboardShown();
            fetchAnswer = snapshot();
            await refreshAgentDashboard();
            const initial = body().innerHTML;
            fetchAnswer = { workspaces: [{ groups: null }] };
            await refreshAgentDashboard();
            report({ unchanged: body().innerHTML === initial, error: !notice().hidden });
            """
        )
        self.assertEqual(result, {"unchanged": True, "error": True})

    def test_the_three_levels_are_three_sections(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([
                group([pane(), pane({ session_id: 's2', index: 1 })]),
                group([pane({ session_id: 's3', group_id: 'g2' })], {
                    group_id: 'g2', name: 'Docs', is_active: false
                })
            ]);
            showDashboard();
            await settle();
            report({ sections: sectionCounts(), rows: parseRows().map(row => row.kind) });
            """
        )
        self.assertEqual(
            result["sections"], {"workspaces": 1, "sessions": 2, "agents": 3}
        )
        # One pressable head per level, in reading order.
        self.assertEqual(
            result["rows"],
            ["workspace", "session", "pane", "pane", "session", "pane"],
        )

    def test_a_row_says_what_is_running_without_going_to_look(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'Claude: fixing the parser' })
            })])]);
            showDashboard();
            await settle();
            report({ rows: parseAgentRows(), row: rowFor('pane:s1') });
            """
        )
        row = result["rows"][0]
        # Four facts, one line: which agent, what it runs on, what it announced,
        # and -- read off the same button by the row parser -- how it is getting
        # on. None of them is on a heading above it any more.
        self.assertEqual(row["name"], "Claude Code")
        self.assertEqual(row["transport"], "SSH")
        self.assertEqual(row["agent"], "claude")
        self.assertIn("/docs/images/agent/claude-code.svg", row["glyph"])
        self.assertEqual(row["line"], "Claude: fixing the parser")
        self.assertEqual(result["row"]["label"], "Claude: fixing the parser")
        self.assertEqual(result["row"]["tags"], [])

    def test_several_panes_of_one_agent_are_one_row_each(self):
        """The block this replaced gathered them under a shared heading, which
        cost every single-pane agent a heading of its own. Two panes are two
        rows, and each states its own agent."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({ mode: 'wsl', use_powershell: true, host: 'PowerShell',
                       activity: activity({ title: 'Agent dashboard todos' }) }),
                pane({ session_id: 's2', index: 1, mode: 'wsl', use_powershell: true,
                       host: 'PowerShell',
                       activity: activity({ title: 'Button in both windows' }) })
            ])]);
            showDashboard();
            await settle();
            report(parseAgentRows());
            """
        )
        self.assertEqual(
            [(row["name"], row["transport"], row["line"]) for row in result],
            [
                ("Claude Code", "PowerShell", "Agent dashboard todos"),
                ("Claude Code", "PowerShell", "Button in both windows"),
            ],
        )

    def test_one_agent_on_two_shells_says_so_on_each_rows_hover(self):
        """The shell is a claim about the pane, so it rides the pane's own row:
        two Claude panes on two shells are two machines as far as the work is
        concerned, and nothing here can fold them into one."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({ mode: 'wsl', use_powershell: true, host: 'PowerShell', directory: 'C:/repo' }),
                pane({ session_id: 's2', index: 1, mode: 'wsl', use_wsl: true,
                       distribution: 'Ubuntu', host: 'wsl', directory: '/srv' })
            ])]);
            showDashboard();
            await settle();
            report(parseAgentRows().map(row => [row.name, row.transport, row.line]));
            """
        )
        self.assertEqual(
            result,
            [
                ["Claude Code", "PowerShell", "New session · repo"],
                ["Claude Code", "WSL · Ubuntu", "New session · srv"],
            ],
        )

    def test_two_agents_wear_two_marks(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane(),
                pane({ session_id: 's2', index: 1, agent_selection: 'codex' }),
                pane({ session_id: 's3', index: 2, agent_selection: 'other',
                       custom_agent: 'house-agent --resume' })
            ])]);
            showDashboard();
            await settle();
            report(parseAgentRows().map(row => ({
                agent: row.agent, name: row.name, glyph: row.glyph
            })));
            """
        )
        self.assertEqual(
            [entry["agent"] for entry in result], ["claude", "codex", "default"]
        )
        self.assertEqual(
            [entry["name"] for entry in result],
            ["Claude Code", "OpenAI Codex CLI", "house-agent"],
        )
        # Three different marks, and the agent with no mark of its own still
        # gets one rather than an empty chip.
        self.assertEqual(len({entry["glyph"] for entry in result}), 3)
        for entry in result:
            self.assertIn("<svg" if entry["agent"] == "default" else "<img", entry["glyph"])

    def test_an_agent_that_has_announced_nothing_says_so(self):
        """A freshly opened agent has no conversation, and the row states that.

        It used to fall through to the pane's directory, which put an absolute
        path on a line that is one `nowrap` row with an ellipsis at its end --
        so the segment that identified the pane was the first thing clipped.
        The place still rides along, shortened to that segment.
        """
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane()])]);
            showDashboard();
            await settle();
            report({
                line: rowFor('pane:s1').label,
                hover: rowFor('pane:s1').hover
            });
            """
        )
        self.assertEqual(result["line"], "New session \u00b7 10.0.0.5:app")
        # And the full path is one hover away, so shortening the line lost
        # nothing that was on it. The shell the pane runs on is the third
        # line, which is where it went when its chip left the row.
        self.assertEqual(
            result["hover"],
            "New session \u00b7 10.0.0.5:app\n10.0.0.5: /srv/app\nSSH",
        )

    def test_a_codex_row_reads_by_the_name_behind_the_id_it_announced(self):
        """The defect this closes: a named thread that publishes only its id.

        Codex is launched asking for `thread-title`, and a thread's title is
        its id until something else is known -- so the row said `New session`
        about a conversation Codex's own resume picker lists by name. The
        backend resolves the name (`web/agent_conversations.py`) and publishes
        it beside the announcement; the row paints the name and the id is
        never on the surface at all.
        """
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({
                agent_selection: 'codex',
                activity: activity({
                    title: '01a085f4-cc1c-7993-8ffc-2fd04d47c731',
                    conversation_title: 'Review OCR delegation'
                })
            })])]);
            showDashboard();
            await settle();
            report({
                line: rowFor('pane:s1').label,
                hover: rowFor('pane:s1').hover,
                html: renderedListHtml()
            });
            """
        )
        self.assertEqual(result["line"], "Review OCR delegation")
        self.assertEqual(
            result["hover"],
            "Review OCR delegation\n10.0.0.5: /srv/app\nSSH",
        )
        self.assertNotIn("01a085f4", result["html"])

    def test_a_name_that_arrives_later_rewrites_the_line_and_nothing_else(self):
        """The lookup is asynchronous, so the row is painted before the answer.

        The arriving name has to take the in-place path -- it is not a
        structural change -- and the poll after it, which says exactly the same
        thing, has to write nothing at all.
        """
        result = self._run_node(
            """
            dashboardShown();
            const id = '01a085f4-cc1c-7993-8ffc-2fd04d47c731';
            const unnamed = pane({ agent_selection: 'codex',
                activity: activity({ title: id, state: 'idle', idle_seconds: 5 }) });
            fetchAnswer = snapshot([group([unnamed])]);
            await refreshAgentDashboard();
            const originalHtml = body().innerHTML;
            const line = { textContent: dashboardPaneLine(unnamed) };
            const reading = { innerHTML: dashboardActivityHtml(unnamed) };
            const progress = { innerHTML: dashboardProgressHtml(unnamed) };
            const row = { classList: fakeClassList(), removeAttribute() {}, dataset: { sessionId: 's1' }, title: dashboardPaneHover(unnamed),
                querySelector: selector => ({
                    '.dash-agent-line': line,
                    '.dash-agent-reading': reading,
                    '.dash-agent-progress': progress
                }[selector]) };
            listBody.querySelectorAll = () => [row];
            const before = line.textContent;

            const named = pane({ agent_selection: 'codex',
                activity: activity({ title: id, conversation_title: 'Review OCR delegation',
                    state: 'idle', idle_seconds: 5 }) });
            fetchAnswer = snapshot([group([named])]);
            await refreshAgentDashboard();
            const after = line.textContent;

            // The next poll says the same thing, so the row is left alone.
            line.textContent = 'SENTINEL';
            fetchAnswer = snapshot([group([named])]);
            await refreshAgentDashboard();
            report({ before, after, skipped: line.textContent,
                sameButtons: body().innerHTML === originalHtml });
            """
        )
        self.assertEqual(result["before"], "New session \u00b7 10.0.0.5:app")
        self.assertEqual(result["after"], "Review OCR delegation")
        self.assertEqual(result["skipped"], "SENTINEL")
        self.assertTrue(result["sameButtons"])

    def test_the_active_chat_title_outranks_a_pane_label(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({
                title: 'release cut',
                activity: activity({ title: 'Claude: fixing the parser' })
            })])]);
            showDashboard();
            await settle();
            report({ line: rowFor('pane:s1').label, agent: parseAgentRows()[0].name });
            """
        )
        self.assertEqual(result["line"], "Claude: fixing the parser")
        # And the agent's own name is still stated beside it, so the title
        # never has to identify the agent that announced it.
        self.assertEqual(result["agent"], "Claude Code")

    def test_a_local_agent_is_tagged_with_the_shell_it_runs(self):
        """And the row's own chip is the only place that is said.

        A local pane's `host` field holds the shell it started, so a line that
        also printed it spent half of itself saying "PowerShell" twice. A remote
        pane's host is a machine, and stays.
        """
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({
                    mode: 'wsl', use_wsl: true, distribution: 'Ubuntu',
                    host: 'wsl', directory: 'C:/repo'
                }),
                pane({
                    session_id: 's2', index: 1,
                    mode: 'wsl', use_powershell: true,
                    host: 'PowerShell', directory: 'C:/repo'
                })
            ])]);
            showDashboard();
            await settle();
            report({
                groups: parseAgentRows().map(entry => entry.transport),
                lines: [rowFor('pane:s1').label, rowFor('pane:s2').label]
            });
            """
        )
        self.assertEqual(result["groups"], ["WSL · Ubuntu", "PowerShell"])
        self.assertEqual(
            result["lines"], ["New session · repo", "New session · repo"]
        )

    def test_auto_approval_is_marked_on_the_pane_that_has_it(self):
        """It is a per-pane property: two panes of one agent need not have been
        launched alike, so `auto` is on the row that has it and on neither of
        its siblings. The transport chip that used to sit beside it is gone."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({ agent_auto_mode: true }),
                pane({ session_id: 's2', index: 1 })
            ])]);
            showDashboard();
            await settle();
            report({
                auto: rowFor('pane:s1').tags,
                plain: rowFor('pane:s2').tags,
                transports: parseAgentRows().map(entry => entry.transport),
                html: renderedListHtml(),
                rows: parseAgentRows().length
            });
            """
        )
        self.assertEqual(result["auto"], [])
        self.assertEqual(result["plain"], [])
        self.assertEqual(result["transports"], ["SSH", "SSH"])
        # And nothing on the drawn row says it: the chip is gone, so `tags` is
        # only ever the markers that differ between two panes of one agent.
        self.assertNotIn("dash-tag-transport", result["html"])
        self.assertEqual(result["rows"], 2)



    def test_the_flag_says_nothing_about_a_pane_that_is_no_longer_an_agent(self):
        """`agent_mcp` outlives the agent that justified it, and the row is not
        an agent row for a pane that stopped being one -- but a stale flag must
        not paint a tag wherever such a pane is still listed."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({ startup_mode: 'terminal', agent_selection: '', agent_mcp: true })
            ])]);
            showDashboard();
            await settle();
            report(rowFor('pane:s1').tags);
            """
        )
        self.assertEqual(result, [])

    def test_a_session_card_wears_its_own_tab_colour(self):
        """The card is a session, and a session already has a colour: the tab
        strip picks one off the group id. Drawing the card in the dialog's own
        accent made the reader match card to tab by name across two windows."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([
                group([pane()]),
                group([pane({ session_id: 's2', group_id: 'g2' })], {
                    group_id: 'g2', name: 'Docs', is_active: false
                })
            ]);
            showDashboard();
            await settle();
            report({
                cards: parseSessionColours(),
                tabs: ['g1', 'g2'].map(id => window.GridVibeSessionColour.sessionColour(id))
            });
            """
        )
        cards = result["cards"]
        self.assertEqual([card["groupId"] for card in cards], ["g1", "g2"])
        # The card's edge is exactly what the workspace window would paint that
        # session's tab -- one answer from one module, not two hashes.
        self.assertEqual([card["colour"] for card in cards], result["tabs"])
        # And the faint companion is the same hue, so the card's hover and its
        # heading rule cannot be tinted from somewhere else.
        for card, colour in zip(cards, result["tabs"]):
            self.assertTrue(card["soft"].startswith("rgba("))
            self.assertNotEqual(card["soft"], colour)

    def test_a_card_with_no_colour_module_still_renders(self):
        """The same rule the × and the band's verbs follow: no controller, no
        control -- the card falls back to the dialog's own tokens rather than
        emitting a broken custom property."""
        result = self._run_node(
            """
            delete globalThis.GridVibeSessionColour;
            fetchAnswer = snapshot([group([pane()])]);
            showDashboard();
            await settle();
            report({
                styled: parseSessionColours().length,
                cards: (renderedListHtml().match(/<section class="dash-session"[ >]/g) || []).length,
                rows: parseAgentRows().length
            });
            """
        )
        self.assertEqual(result, {"styled": 0, "cards": 1, "rows": 1})

    def test_the_session_card_says_how_many_of_its_panes_are_agents(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane()], { pane_count: 4, agent_count: 1 })]);
            showDashboard();
            await settle();
            report({ html: renderedListHtml(), active: rowFor('session:g1').tags });
            """
        )
        self.assertIn("1 agent · 3 other", result["html"])
        self.assertEqual(result["active"], [])

    def test_the_totals_line_counts_what_the_window_is_about(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane(), pane({ session_id: 's2', index: 1 })])]);
            showDashboard();
            await settle();
            report(totals().textContent);
            """
        )
        self.assertEqual(result, "2 agents · 1 session · 1 workspace")

    def test_nothing_running_says_so_rather_than_showing_an_empty_frame(self):
        """The tree carries every live workspace now, so an absent one is a
        server with nothing open rather than one with nothing agentic open."""
        result = self._run_node(
            """
            fetchAnswer = { generated_at: 1, workspaces: [], totals: { workspaces: 0, sessions: 0, agents: 0 } };
            showDashboard();
            await settle();
            report({ html: renderedListHtml(), totals: totals().textContent, rows: parseRows().length });
            """
        )
        self.assertIn("Nothing is running.", result["html"])
        self.assertEqual(result["totals"], "Nothing running")
        self.assertEqual(result["rows"], 0)

    def test_a_session_with_no_agent_is_a_card_that_says_so(self):
        """The reason it is listed at all is that it is a way to that tab, so
        it is a card with the same heading control the others have -- and where
        the rows would be there is one muted line saying why there are none."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group(), quietGroup()]);
            showDashboard();
            await settle();
            report({
                html: renderedListHtml(),
                cards: sectionCounts().sessions,
                agents: parseAgentRows().length,
                quiet: rowFor('session:g9'),
                loud: rowFor('session:g1')
            });
            """
        )
        self.assertEqual(result["cards"], 2)
        # The agent-free card draws no rows, so every agent row on the page
        # still belongs to a session that has one.
        self.assertEqual(result["agents"], 1)
        self.assertIn("No active agents", result["html"])
        self.assertIn('class="dash-session is-quiet"', result["html"])
        self.assertEqual(result["quiet"]["label"], "Notes")
        # The two headings are the same control, naming the same two ids the
        # landing needs; only the card around them differs.
        self.assertEqual(result["quiet"]["kind"], result["loud"]["kind"])
        self.assertEqual(result["quiet"]["dataset"]["workspaceId"], "default")
        self.assertEqual(result["quiet"]["dataset"]["groupId"], "g9")

    def test_a_card_with_no_agent_states_its_size_rather_than_its_lack(self):
        """"0 agents - 3 other panes" describes the session by what it is not
        and makes the reader subtract to find out what it is."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group(), quietGroup()]);
            showDashboard();
            await settle();
            report(renderedListHtml());
            """
        )
        self.assertIn("3 panes", result)
        self.assertNotIn("0 agent", result)

    def test_the_totals_line_states_the_agent_count_even_at_zero(self):
        """The list is every session now, so the line has to say how many of
        them hold an agent -- and a zero must not read like a missing word."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([quietGroup()], {
                totals: { workspaces: 1, sessions: 1, agents: 0, working: 0 }
            });
            showDashboard();
            await settle();
            report({ totals: totals().textContent, html: renderedListHtml() });
            """
        )
        self.assertEqual(result["totals"], "no agents · 1 session · 1 workspace")
        # And the band above it says the same thing about itself.
        self.assertIn("no agents", result["html"])
        self.assertIn('class="dash-workspace is-quiet"', result["html"])

    def test_an_announced_title_cannot_rewrite_the_rows(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: '<img src=x onerror=alert(1)>' })
            })])]);
            showDashboard();
            await settle();
            report({ html: renderedListHtml(), line: rowFor('pane:s1').label });
            """
        )
        self.assertNotIn("<img src=x", result["html"])
        self.assertEqual(result["html"].count('<img '), 1)  # Only bundled agent artwork.
        self.assertIn("&lt;img", result["line"])


class DashboardWindowActivityTestCase(DashboardDialogTestCase):
    def _row(self, overrides: str):
        return self._run_node(
            """
            fetchAnswer = snapshot([group([pane(%s)])]);
            showDashboard();
            await settle();
            report(rowFor('pane:s1'));
            """
            % overrides
        )

    def test_a_published_percentage_is_drawn_as_one(self):
        row = self._row(
            "{ activity: activity({ progress_state: 'normal', progress_value: 65 }) }"
        )
        self.assertEqual(row["state"], "working")
        self.assertEqual(row["word"], "Working")
        self.assertTrue(row["hasBar"])
        self.assertFalse(row["indeterminate"])
        self.assertEqual(row["percent"], 65)

    def test_working_with_no_number_still_moves_rather_than_reading_as_stalled(self):
        row = self._row(
            "{ activity: activity({ progress_state: 'indeterminate' }) }"
        )
        self.assertTrue(row["hasBar"])
        self.assertTrue(row["indeterminate"])
        self.assertIsNone(row["percent"])

    def test_an_agent_that_publishes_nothing_gets_a_state_and_no_bar(self):
        row = self._row("{ activity: activity({ state: 'working' }) }")
        self.assertEqual(row["state"], "working")
        self.assertFalse(row["hasBar"])

    def test_idle_says_how_long_it_has_been_waiting(self):
        row = self._row(
            "{ activity: activity({ state: 'idle', idle_seconds: 247 }) }"
        )
        self.assertEqual(row["state"], "idle")
        self.assertEqual(row["word"], "Idle 4m")

    def test_a_pane_with_no_transport_yet_says_nothing_was_observed(self):
        row = self._row("{ activity: null }")
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["word"], "No output yet")

    def test_an_unreachable_pane_reports_that_instead_of_an_activity_reading(self):
        # "Idle 4m" about a pane whose shell died answers a different question.
        row = self._row(
            "{ status: 'disconnected', activity: activity({ state: 'idle', idle_seconds: 247 }) }"
        )
        self.assertEqual(row["state"], "error")
        self.assertEqual(row["word"], "Disconnected")
        self.assertFalse(row["hasBar"])

    def test_a_pane_still_connecting_says_so(self):
        row = self._row("{ status: 'connecting', activity: null }")
        self.assertEqual(row["state"], "unknown")
        self.assertEqual(row["word"], "Connecting")

    def test_the_word_the_dot_replaced_is_the_dots_own_hover(self):
        """The state is drawn as a colour, so the reading a colour cannot carry
        -- how long idle, and the three transport words -- has to be somewhere a
        reader can ask for it. It is a hover on the indicator, which is inside
        the row's own and so answers on the dot rather than on the whole row:
        the row's hover is still the chat line and where the pane is."""
        idle = self._row("{ activity: activity({ state: 'idle', idle_seconds: 247 }) }")
        self.assertEqual(idle["stateHover"], "Idle 4m")
        self.assertNotEqual(idle["hover"], idle["stateHover"])
        dead = self._row("{ status: 'disconnected', activity: null }")
        self.assertEqual(dead["stateHover"], "Disconnected")

    def test_the_row_comes_first_and_the_bar_comes_last(self):
        """The dot is the row's leading column and the bar its trailing one, so
        the mark, the name and the title start at the same offset on every row
        whatever the agent beside them publishes."""
        row = self._row(
            "{ activity: activity({ progress_state: 'normal', progress_value: 65 }) }"
        )
        order = [
            row["html"].index('class="dash-agent-reading"'),
            row["html"].index('class="dash-agent-icon"'),
            row["html"].index('class="dash-agent-who"'),
            row["html"].index('class="dash-agent-line"'),
            row["html"].index('class="dash-agent-progress"'),
        ]
        self.assertEqual(order, sorted(order))


class DashboardDialogRowActionTestCase(DashboardDialogTestCase):
    def test_every_level_opens_the_window_that_owns_it(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({ workspace_id: 'ws2', group_id: 'g7' })], {
                workspace_id: 'ws2', group_id: 'g7'
            })], { workspaces: [{
                workspace_id: 'ws2', label: 'api', active_group_id: 'g7',
                group_count: 1, agent_count: 1,
                groups: [group([pane({ workspace_id: 'ws2', group_id: 'g7' })], {
                    workspace_id: 'ws2', group_id: 'g7'
                })]
            }] });
            showDashboard();
            await settle();
            clickRow('pane:s1');
            clickRow('session:g7');
            clickRow('workspace:ws2');
            await settle();
            report(calls.openWorkspaceWindow);
            """
        )
        self.assertEqual(
            result,
            [
                {"workspaceId": "ws2", "options": {"groupId": "g7"}},
                {"workspaceId": "ws2", "options": {"groupId": "g7"}},
                # A workspace head names no session, so it opens the window
                # wherever that window last was.
                {"workspaceId": "ws2", "options": {"groupId": ""}},
            ],
        )

    def test_an_agent_row_leaves_its_own_pane_waiting_to_be_claimed(self):
        """The bug the request answers: the native bridge raises an already-open
        workspace window without retargeting it, so a row that named only the
        workspace landed on whichever tab that window was last left on. The
        request has to be standing *before* the window is asked for, because a
        raised window claims it the moment it comes to the front."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane(), pane({ session_id: 's2', index: 1 })
            ])]);
            showDashboard();
            await settle();
            clickRow('pane:s2');
            await settle();
            report(calls.focusTargets);
            """
        )
        self.assertEqual(
            result,
            [{
                "workspaceId": "default",
                "options": {"groupId": "g1", "sessionId": "s2"},
                # Nothing had been opened yet when the request was written.
                "openedSoFar": 0,
            }],
        )

    def test_a_session_head_names_its_tab_and_no_pane_in_it(self):
        """It is the way to a tab that may hold panes this surface does not
        list, so it must not land on one of the ones it does."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            clickRow('session:g1');
            clickRow('workspace:default');
            await settle();
            report(calls.focusTargets.map(entry => entry.options));
            """
        )
        self.assertEqual(
            result, [{"groupId": "g1", "sessionId": ""}, {"groupId": "", "sessionId": ""}]
        )

    def test_landing_in_another_window_leaves_the_list_standing(self):
        """The pane the reader asked for came up somewhere else, so this dialog
        is not in front of it and there is nothing for it to get out of the way
        of. Leaving it up is the whole reason to raise it on a screen the work
        is not on: one row is rarely the only one the reader wants. Only the
        last action's confirmation goes with the press."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({
                open: dialogOpen(),
                hidden: shell().attributes['aria-hidden'],
                rows: parseRows().length,
                notice: notice().hidden,
                opened: calls.openWorkspaceWindow.length
            });
            """
        )
        self.assertTrue(result["open"])
        self.assertEqual(result["hidden"], "false")
        self.assertEqual(result["rows"], 3)
        self.assertTrue(result["notice"])
        self.assertEqual(result["opened"], 1)

    def test_a_second_row_is_reachable_without_raising_the_dialog_again(self):
        """The consequence worth pinning: a list that survives the first press
        is a list the reader can go on using. Two rows, two windows asked for,
        one opening."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group(), quietGroup()]);
            showDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            clickRow('session:g9');
            await settle();
            report({ open: dialogOpen(), opened: calls.openWorkspaceWindow });
            """
        )
        self.assertTrue(result["open"])
        self.assertEqual(
            result["opened"],
            [
                {"workspaceId": "default", "options": {"groupId": "g1"}},
                {"workspaceId": "default", "options": {"groupId": "g9"}},
            ],
        )

    def test_a_refusal_keeps_the_dialog_up_because_that_is_where_it_is_said(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            workspaceOpens = false;
            showDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({ open: dialogOpen(), text: notice().textContent });
            """
        )
        self.assertTrue(result["open"])
        self.assertEqual(result["text"], WORKSPACE_TAB_BLOCKED_HINT)

    def test_a_blocked_pop_up_is_reported_in_the_one_wording_that_owns_it(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            workspaceOpens = false;
            showDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({ hidden: notice().hidden, text: notice().textContent });
            """
        )
        self.assertFalse(result["hidden"])
        self.assertEqual(result["text"], WORKSPACE_TAB_BLOCKED_HINT)


class DashboardDialogLifecycleTestCase(DashboardDialogTestCase):
    """Up, down, and what each costs.

    The dashboard was a window and is a dialog, which makes its own opening and
    shutting behaviour rather than the browser's. Everything here is a rule the
    window did not need and the dialog cannot do without.
    """

    def test_nothing_is_read_until_it_is_opened(self):
        """A wired page is not an open dialog. The reading is a whole-tree
        compose every two seconds; a background window quietly refetching the
        state of every workspace forever is exactly what a dialog is for
        avoiding."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            await settle();
            const wired = { open: dialogOpen(), fetches: calls.fetches, armed: timers.armed };
            openAgentDashboardDialog();
            await settle();
            report({
                wired,
                opened: { open: dialogOpen(), fetches: calls.fetches, armed: timers.armed },
                hidden: shell().attributes['aria-hidden'],
                agents: sectionCounts().agents
            });
            """
        )
        self.assertEqual(result["wired"], {"open": False, "fetches": 0, "armed": 0})
        # Opening arms the poll *and* reads once now: waiting out the first
        # tick would show the reader an empty frame for two seconds.
        self.assertEqual(result["opened"], {"open": True, "fetches": 1, "armed": 1})
        self.assertEqual(result["hidden"], "false")
        self.assertEqual(result["agents"], 1)

    def test_closing_disarms_the_poll_and_abandons_what_is_in_flight(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            let signal = null;
            globalThis.fetch = (_url, options) => {
                signal = options.signal;
                return new Promise(() => {});
            };
            refreshAgentDashboard();
            const cleared = timers.cleared;
            closeAgentDashboardDialog();
            await settle();
            report({
                open: dialogOpen(),
                hidden: shell().attributes['aria-hidden'],
                aborted: signal.aborted,
                disarmed: timers.cleared > cleared
            });
            """
        )
        self.assertFalse(result["open"])
        self.assertEqual(result["hidden"], "true")
        self.assertTrue(result["aborted"])
        self.assertTrue(result["disarmed"])

    def test_a_shut_dialog_reads_nothing_however_it_is_asked(self):
        """Every reader goes through the one gate, the window-focus listener
        included — that one fires whenever the reader clicks back into the page,
        and it used to be the whole point of keeping the count honest."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            closeAgentDashboardDialog();
            const before = calls.fetches;
            await refreshAgentDashboard();
            fireWindow('focus', {});
            document.fire('visibilitychange');
            await settle();
            report({ before, after: calls.fetches });
            """
        )
        self.assertEqual(result["after"], result["before"])

    def test_the_backdrop_closes_it_and_the_surface_does_not(self):
        """A press that lands on the surface is a press *in* the dialog, however
        much of the backdrop is around it — the same test the shared confirm
        shell uses for its own."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            shell().fire('click', { target: { id: 'somethingInside' } });
            const afterInside = dialogOpen();
            shell().fire('click', { target: shell() });
            report({ afterInside, afterBackdrop: dialogOpen() });
            """
        )
        self.assertTrue(result["afterInside"])
        self.assertFalse(result["afterBackdrop"])

    def test_the_title_bar_close_closes_it(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            byId.get('agentDashboardCloseBtn').fire('click', {});
            report({ open: dialogOpen() });
            """
        )
        self.assertFalse(result["open"])

    def test_escape_closes_it_but_never_two_surfaces_at_once(self):
        """A session x here raises the close prompt on top of this dialog, and
        that prompt answers Escape itself. An unguarded handler would cancel the
        close *and* take away the list the reader was working through."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            otherShellOpen = true;
            press('Escape');
            const underneath = dialogOpen();
            otherShellOpen = false;
            press('Escape');
            const alone = dialogOpen();
            press('Escape');
            report({ underneath, alone, whenShut: dialogOpen() });
            """
        )
        self.assertTrue(result["underneath"])
        self.assertFalse(result["alone"])
        self.assertFalse(result["whenShut"])

    def test_escape_is_not_claimed_so_the_page_behind_still_reads_it(self):
        """`EXPLORER_ESCAPE_CLAIM_SELECTOR` already covers a visible
        `.modal-shell`, which is how a pane's selection survives this press. A
        `preventDefault` here would change what Escape means everywhere behind
        it as well."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            report({ prevented: press('Escape'), open: dialogOpen() });
            """
        )
        self.assertFalse(result["prevented"])
        self.assertFalse(result["open"])

    def test_focus_moves_in_and_is_handed_back_only_if_it_is_still_here(self):
        """The surface itself, not the first control in it: the reader opened a
        list to read, and parking the caret on Refresh means the first Enter
        re-reads. And a close provoked by a press somewhere else must not yank
        focus off what was pressed."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            const opener = { focused: false, focus() { this.focused = true; } };
            document.activeElement = opener;
            focusTarget = { focused: false, focus() { this.focused = true; } };
            focusIsInside = true;
            showDashboard();
            await settle();
            const surfaceFocused = focusTarget.focused;
            const queried = focusQueries[0];
            closeAgentDashboardDialog();
            const handedBack = opener.focused;

            /* Again, with the caret somewhere else entirely by the time it
               closes. */
            opener.focused = false;
            focusIsInside = false;
            openAgentDashboardDialog();
            await settle();
            closeAgentDashboardDialog();
            report({ surfaceFocused, queried, handedBack, leftAlone: opener.focused });
            """
        )
        self.assertTrue(result["surfaceFocused"])
        self.assertEqual(result["queried"], ".dash-dialog")
        self.assertTrue(result["handedBack"])
        self.assertFalse(result["leftAlone"])

    def test_an_action_notice_does_not_survive_into_the_next_opening(self):
        """A confirmation belongs to the pass that provoked it; reported again
        four minutes later it is something the reader cannot place. The read
        notice is not touched -- it describes the tree still on screen."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            setAgentDashboardNotice('Closed the window for api.', 'action', 'info');
            const during = notice().textContent;
            closeAgentDashboardDialog();
            openAgentDashboardDialog();
            await settle();
            report({ during, after: notice().textContent, hidden: notice().hidden });
            """
        )
        self.assertEqual(result["during"], "Closed the window for api.")
        self.assertEqual(result["after"], "")
        self.assertTrue(result["hidden"])

    def test_reopening_is_idempotent_and_costs_one_read(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const first = { fetches: calls.fetches, opened: openAgentDashboardDialog() };
            await settle();
            report({ first, fetches: calls.fetches, closedTwice: [
                closeAgentDashboardDialog(), closeAgentDashboardDialog()
            ] });
            """
        )
        self.assertEqual(result["first"], {"fetches": 1, "opened": False})
        self.assertEqual(result["fetches"], 1)
        self.assertEqual(result["closedTwice"], [True, False])

    def test_the_toggle_is_the_whole_control(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            /* What it answers is the state it is now in, not whether the
               press did anything: the button and the chord are one control and
               what a caller wants back is "is it up?". */
            const answers = [
                toggleAgentDashboardDialog(),
                toggleAgentDashboardDialog(),
                toggleAgentDashboardDialog()
            ];
            await settle();
            report({ answers, open: dialogOpen() });
            """
        )
        self.assertEqual(result["answers"], [True, False, True])
        self.assertTrue(result["open"])

    def test_a_page_with_no_dialog_on_it_reports_a_shut_one(self):
        """The button's partial and the dialog's are two includes, so a page
        that ships one without the other is a real state -- and the answer has
        to be "it is not up", never an exception out of an inline onclick."""
        result = self._run_node(
            """
            byId.delete('agentDashboardShell');
            report({
                wired: (wireAgentDashboard(), true),
                toggled: toggleAgentDashboardDialog(),
                opened: openAgentDashboardDialog(),
                closed: closeAgentDashboardDialog()
            });
            """
        )
        self.assertEqual(
            result, {"wired": True, "toggled": False, "opened": False, "closed": False}
        )

    def test_leaving_the_window_leaves_it_up_and_still_reading(self):
        """The gesture the reader actually makes, and the one this surface now
        exists to survive: they go and work in another workspace with the list
        up on another monitor. `blur` is a window losing the focus, not a window
        nobody can see, so nothing is dismissed and nothing stands down —
        a dialog that put itself away here could never be read while working."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const cleared = timers.cleared;
            documentFocused = false;
            fireWindow('blur', {});
            await settle();
            /* Still reading: the poll was never disarmed, and the tick that
               comes round while the reader is elsewhere is served. */
            await refreshAgentDashboard();
            report({
                open: dialogOpen(),
                hidden: shell().attributes['aria-hidden'],
                disarmed: timers.cleared > cleared,
                fetches: calls.fetches
            });
            """
        )
        self.assertTrue(result["open"])
        self.assertEqual(result["hidden"], "false")
        self.assertFalse(result["disarmed"])
        self.assertEqual(result["fetches"], 2)

    def test_a_window_nobody_can_see_keeps_it_and_stops_reading_for_it(self):
        """The cost the old dismissal was really paying for. A minimized window
        or a tab behind another tab can show the reader nothing, so composing
        the whole tree every two seconds for it is waste — but it is the poll
        that stands down, not the dialog, because the reader never said they
        were done with it."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const armed = timers.armed;
            const cleared = timers.cleared;
            document.hidden = true;
            documentFocused = false;
            document.fire('visibilitychange');
            await settle();
            report({
                open: dialogOpen(),
                hidden: shell().attributes['aria-hidden'],
                disarmed: timers.cleared > cleared,
                /* Nothing put back in its place: the tick simply stops
                   arriving until the window can be seen again. */
                rearmed: timers.armed > armed,
                fetches: calls.fetches
            });
            """
        )
        self.assertTrue(result["open"])
        self.assertEqual(result["hidden"], "false")
        self.assertTrue(result["disarmed"])
        self.assertFalse(result["rearmed"])
        self.assertEqual(result["fetches"], 1)

    def test_a_close_from_outside_never_pulls_focus_into_the_window_it_closed(self):
        """`activeElement` does not move when a window is deactivated, so a
        close arriving from elsewhere looks exactly like an in-page dismissal
        unless `hasFocus()` is asked. Leaving the window is no longer such a
        close, but the cross-window claim is: the reader raised the dialog over
        *there*, and this one putting the caret on a button in a window nobody
        is looking at is meaningless at best — in a host where `element.focus()`
        raises its window it is this window yanking itself back in front of the
        one the reader has just chosen."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            const opener = { focused: false, focus() { this.focused = true; } };
            document.activeElement = opener;
            focusIsInside = true;
            showDashboard();
            await settle();

            documentFocused = false;
            broadcastClaim(claimFrom('window-b'));
            const closedFromOutside = !dialogOpen();
            const afterClaim = opener.focused;

            /* The same close from a window that still has focus is an in-page
               dismissal and does hand it back. */
            documentFocused = true;
            openAgentDashboardDialog();
            await settle();
            closeAgentDashboardDialog();
            report({ closedFromOutside, afterClaim, afterDismissing: opener.focused });
            """
        )
        self.assertTrue(result["closedFromOutside"])
        self.assertFalse(result["afterClaim"])
        self.assertTrue(result["afterDismissing"])

    def test_coming_back_into_view_re_arms_the_poll_and_reads_once(self):
        """Suspended, not dismissed. The dialog is still the surface in front of
        the reader when the window comes back, so it reads immediately rather
        than sitting out a tick showing the tree from before it was put away."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            document.hidden = true;
            document.fire('visibilitychange');
            await settle();
            const away = { open: dialogOpen(), fetches: calls.fetches, armed: timers.armed };
            document.hidden = false;
            document.fire('visibilitychange');
            await settle();
            report({
                away,
                open: dialogOpen(),
                fetches: calls.fetches,
                rearmed: timers.armed > away.armed
            });
            """
        )
        self.assertTrue(result["away"]["open"])
        self.assertEqual(result["away"]["fetches"], 1)
        self.assertTrue(result["open"])
        self.assertEqual(result["fetches"], 2)
        self.assertTrue(result["rearmed"])

    def test_the_blur_stops_at_this_window_and_reaches_no_other(self):
        """The scrim is the page's own `.modal-shell` and goes exactly as far as
        the page does. A cross-window lease used to dim every *other* GridVibe
        page while this dialog had focus; it was worth having while the dialog
        put itself away the moment the reader left, and it is the wrong window
        to dim now that they keep it up to work elsewhere. So nothing here
        publishes a dim, and the only cross-window message an open sends is the
        claim that keeps there being one dialog."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            closeAgentDashboardDialog();
            report({
                messages: posted.map(entry => entry.name),
                bodyClasses: [...(document.body?.classList?.names || [])]
            });
            """
        )
        self.assertEqual(result["messages"], ["gridvibe.dashboardOpen"])
        self.assertEqual(result["bodyClasses"], [])


class DashboardDialogHereTestCase(DashboardDialogTestCase):
    """A row that names the workspace this page already is.

    The one case a separate window could not have had, and the one that would
    fail silently: `openWorkspaceWindow` on the window you are already in raises
    a window that is already raised, so no `focus` event fires, the stored
    target is never claimed, and the row does nothing whatever.
    """

    HERE = """
            globalThis.CURRENT_WORKSPACE_ID = 'default';
            globalThis.normalizeWorkspaceId = value => String(value || 'default');
            calls.landed = [];
            globalThis.applyWorkspaceFocusTarget = target => calls.landed.push(target);
    """

    def test_a_row_for_this_workspace_lands_here_and_opens_no_window(self):
        result = self._run_node(
            self.HERE
            + """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({
                landed: calls.landed,
                opened: calls.openWorkspaceWindow.length,
                /* Nothing is stored either: a request nobody arrives on sits in
                   localStorage until it expires, and the next window opened for
                   any reason claims it. */
                stored: calls.focusTargets.length,
                open: dialogOpen()
            });
            """
        )
        self.assertEqual(result["landed"], [{"groupId": "g1", "sessionId": "s1"}])
        self.assertEqual(result["opened"], 0)
        self.assertEqual(result["stored"], 0)
        # Shut before the landing, because the landing focuses a pane and a pane
        # focused under an open dialog takes the caret somewhere the reader can
        # neither see nor type into.
        self.assertFalse(result["open"])

    def test_a_session_heading_lands_on_its_tab_with_no_agent_in_it(self):
        """Pressed, not merely rendered: the card that exists so a reader can
        reach an agent-free tab has to actually reach it, by the same route the
        agent-bearing ones take."""
        result = self._run_node(
            self.HERE
            + """
            fetchAnswer = snapshot([group(), quietGroup()]);
            showDashboard();
            await settle();
            clickRow('session:g9');
            await settle();
            report({ landed: calls.landed, opened: calls.openWorkspaceWindow.length, open: dialogOpen() });
            """
        )
        self.assertEqual(result["landed"], [{"groupId": "g9", "sessionId": ""}])
        self.assertEqual(result["opened"], 0)
        self.assertFalse(result["open"])

    def test_a_row_for_another_workspace_still_opens_its_window(self):
        result = self._run_node(
            self.HERE
            + """
            fetchAnswer = snapshot([group([pane({ workspace_id: 'ws2', group_id: 'g7' })], {
                workspace_id: 'ws2', group_id: 'g7'
            })], { workspaces: [{
                workspace_id: 'ws2', label: 'api', active_group_id: 'g7',
                group_count: 1, agent_count: 1,
                groups: [group([pane({ workspace_id: 'ws2', group_id: 'g7' })], {
                    workspace_id: 'ws2', group_id: 'g7'
                })]
            }] });
            globalThis.normalizeWorkspaceId = value => String(value || 'default');
            showDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({ landed: calls.landed, opened: calls.openWorkspaceWindow, open: dialogOpen() });
            """
        )
        self.assertEqual(result["landed"], [])
        self.assertEqual(
            result["opened"], [{"workspaceId": "ws2", "options": {"groupId": "g7"}}]
        )
        # And the list stays: the pane came up in the other window, so this
        # dialog is in front of nothing the reader asked for.
        self.assertTrue(result["open"])

    def test_a_page_that_is_no_workspace_opens_the_window_as_before(self):
        """The launcher carries the same dialog and is in no workspace at all,
        so the question is not asked there."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({ opened: calls.openWorkspaceWindow.length, stored: calls.focusTargets.length });
            """
        )
        self.assertEqual(result["opened"], 1)
        self.assertEqual(result["stored"], 1)

class DashboardDialogExclusivityTestCase(DashboardDialogTestCase):
    """One dashboard at a time, across every window.

    Every GridVibe window carries this dialog now, so two of them up at once is
    two readings of one tree drifting apart on their own polls, each with the
    reader's x and Close workspace on it. Raising it anywhere puts away whichever
    window had it -- and the direction that rule runs in is the whole design: a
    claim is a notice, never a lock, so nothing can refuse to open and a window
    that died with its dialog up leaves no claim to make the button dead
    somewhere else.
    """

    def test_opening_it_tells_every_other_window_to_put_theirs_away(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const claim = myClaim();
            report({
                open: dialogOpen(),
                source: claim && claim.source,
                dated: Number.isFinite(claim && claim.at),
                /* Both transports, because a claim that only rode one would be
                   silently lost wherever that one is unavailable. */
                channels: posted.map(entry => entry.name),
                stored: stored.has('gridvibe.dashboardOpen')
            });
            """
        )
        self.assertTrue(result["open"])
        self.assertEqual(result["source"], "window-a")
        self.assertTrue(result["dated"])
        self.assertEqual(result["channels"], ["gridvibe.dashboardOpen"])
        self.assertTrue(result["stored"])

    def test_a_claim_from_another_window_closes_this_one(self):
        """Over both transports: a browser without `BroadcastChannel` falls
        back to `storage`, and a rule that only ran on one of them would leave
        two dialogs up exactly there."""
        for arrival in ("broadcastClaim", "storageClaim"):
            with self.subTest(arrival=arrival):
                result = self._run_node(
                    """
                    fetchAnswer = snapshot();
                    showDashboard();
                    await settle();
                    const before = dialogOpen();
                    %s(claimFrom('window-b'));
                    report({
                        before,
                        after: dialogOpen(),
                        hidden: shell().attributes['aria-hidden']
                    });
                    """
                    % arrival
                )
                self.assertTrue(result["before"])
                self.assertFalse(result["after"])
                self.assertEqual(result["hidden"], "true")

    def test_this_windows_own_claim_never_closes_its_own_dialog(self):
        """A `BroadcastChannel` post never reaches the object that sent it, but
        it does reach every other channel object in the same document -- and the
        publisher opens a fresh one per message while the listener holds one
        open. So the dialog hears its own claim, and skipping it is what stops
        the open from immediately undoing itself."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const afterOwnBroadcast = dialogOpen();
            /* And the storage path, which a same-origin window in another tab
               would deliver but this one never does. */
            storageClaim(myClaim());
            report({ afterOwnBroadcast, afterOwnStorage: dialogOpen() });
            """
        )
        self.assertTrue(result["afterOwnBroadcast"])
        self.assertTrue(result["afterOwnStorage"])

    def test_an_older_claim_is_not_obeyed_so_two_opens_cannot_annihilate(self):
        """Two presses inside one message round trip would otherwise each tell
        the other to close and leave the reader with none. A claim is compared
        with this window's own: later wins, and an exact tie -- which a coarse
        clock can genuinely produce -- is broken on the window id, any total
        order serving as long as both windows compute the same one."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const mine = myClaim();

            broadcastClaim(claimFrom('window-b', { at: mine.at - 1 }));
            const afterOlder = dialogOpen();

            /* A tie this window wins: 'window-a' sorts above 'window-0'. */
            broadcastClaim(claimFrom('window-0', { at: mine.at }));
            const afterTieWon = dialogOpen();

            /* And one it loses. */
            broadcastClaim(claimFrom('window-z', { at: mine.at }));
            report({ afterOlder, afterTieWon, afterTieLost: dialogOpen() });
            """
        )
        self.assertTrue(result["afterOlder"])
        self.assertTrue(result["afterTieWon"])
        self.assertFalse(result["afterTieLost"])

    def test_a_claim_never_refuses_an_open_however_stale_it_is(self):
        """The direction the whole rule runs in. A window killed with its dialog
        up leaves a claim nobody can release, so a design that asked permission
        before opening would leave this button dead in every other window until
        something expired that claim. There is no such state to be in."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            /* Somebody else's claim, standing before this window ever opened. */
            broadcastClaim(claimFrom('window-b', { at: Date.now() + 60000 }));
            storageClaim(claimFrom('window-b', { at: Date.now() + 60000 }));
            const opened = openAgentDashboardDialog();
            await settle();
            report({ opened, open: dialogOpen(), agents: sectionCounts().agents });
            """
        )
        self.assertTrue(result["opened"])
        self.assertTrue(result["open"])
        self.assertEqual(result["agents"], 1)

    def test_a_shut_dialog_has_nothing_to_close_and_stays_shut(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            closeAgentDashboardDialog();
            broadcastClaim(claimFrom('window-b'));
            const afterClaim = dialogOpen();
            /* And the next open still wins, with a claim of its own. */
            const before = stored.get('gridvibe.dashboardOpen');
            openAgentDashboardDialog();
            await settle();
            report({ afterClaim, reopened: dialogOpen(), republished: stored.get('gridvibe.dashboardOpen') !== before });
            """
        )
        self.assertFalse(result["afterClaim"])
        self.assertTrue(result["reopened"])
        self.assertTrue(result["republished"])

    def test_coming_back_from_the_back_forward_cache_claims_again(self):
        """A frozen page receives no messages, so a dialog restored from the
        back/forward cache has missed every claim made while it was away and may
        be the second one up. It claims rather than reads: it is the surface in
        front of the reader, so it is the one that should win."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const first = myClaim();
            fireWindow('pagehide', {});
            fireWindow('pageshow', { persisted: true });
            await settle();
            const back = myClaim();

            /* And a shut one claims nothing, because it is not in front of
               anybody. */
            closeAgentDashboardDialog();
            fireWindow('pageshow', { persisted: true });
            report({
                reclaimed: back.nonce !== first.nonce,
                whileShut: myClaim().nonce === back.nonce
            });
            """
        )
        self.assertTrue(result["reclaimed"])
        self.assertTrue(result["whileShut"])

    def test_a_close_is_not_broadcast_because_nothing_reads_it(self):
        """The other windows have no dialog up, so there is nothing for them to
        do about it -- and a second piece of cross-window state with no reader is
        one more thing to fall out of step."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const afterOpen = posted.length;
            closeAgentDashboardDialog();
            report({ afterOpen, afterClose: posted.length });
            """
        )
        self.assertEqual(result["afterOpen"], 1)
        self.assertEqual(result["afterClose"], 1)

    def test_a_malformed_or_unrelated_message_is_ignored(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            broadcastClaim(null);
            broadcastClaim({ source: 'window-b' });
            broadcastClaim({ source: '', at: Date.now() + 5000 });
            storageClaim(claimFrom('window-b'), 'gridvibe.somethingElse');
            storageClaim(null);
            fireWindow('storage', { key: 'gridvibe.dashboardOpen', newValue: 'not json' });
            report({ open: dialogOpen() });
            """
        )
        self.assertTrue(result["open"])

    def test_a_page_with_no_window_identity_claims_nothing(self):
        """Every cross-window message in the app is tagged with shared.js's id.
        An untagged claim is one every window would act on, this one included,
        so a page somehow without that id publishes nothing rather than
        something worse than nothing."""
        result = self._run_node(
            """
            GRIDVIBE_WINDOW_ID = undefined;
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            report({ open: dialogOpen(), posted: posted.length, stored: stored.size });
            """
        )
        self.assertTrue(result["open"])
        self.assertEqual(result["posted"], 0)
        self.assertEqual(result["stored"], 0)

class DashboardDialogRepaintTestCase(DashboardDialogTestCase):
    def test_an_unchanged_reading_is_not_repainted_at_all(self):
        """This window stays open while it is read, so an identical tick must
        not drop the caret, the selection or the scroll."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            /* A mark nothing in the module would ever write: it survives only
               if the second reading was not painted over it. */
            body().innerHTML += '<!--the reader was here-->';
            await refreshAgentDashboard();
            const unchanged = body().innerHTML.includes('the reader was here');
            fetchAnswer = snapshot([group([pane({ activity: activity({ title: 'now doing something else' }) })])]);
            await refreshAgentDashboard();
            report({ unchanged, afterChange: body().innerHTML.includes('the reader was here'), line: rowFor('pane:s1').label });
            """
        )
        self.assertTrue(result["unchanged"])
        self.assertTrue(result["afterChange"])
        self.assertEqual(result["line"], "now doing something else")



    def test_a_reading_that_changed_keeps_the_caret_and_the_scroll(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            /* The open's own query for the surface it focuses is not what this
               case is about; only the render's is. */
            focusQueries.length = 0;
            body().scrollTop = 240;
            focusIsInside = true;
            focusTarget = { focused: false, focus() { this.focused = true; } };
            document.activeElement = { dataset: { dashboardKey: 'pane:s1' } };
            fetchAnswer = snapshot([group([pane({ session_id: 's2', activity: activity({ title: 'a different thing' }) })])]);
            await refreshAgentDashboard();
            report({ scrollTop: body().scrollTop, queries: focusQueries, refocused: focusTarget.focused });
            """
        )
        self.assertEqual(result["scrollTop"], 240)
        self.assertIn('[data-dashboard-key="pane:s1"]', result["queries"])
        self.assertTrue(result["refocused"])

    def test_a_slow_answer_never_repaints_over_a_newer_one(self):
        result = self._run_node(
            """
            dashboardShown();
            let pending = null;
            fetchAnswer = () => new Promise(resolve => { pending = resolve; });
            const slow = refreshAgentDashboard();
            const slowResolve = pending;
            fetchAnswer = snapshot([group([pane({ activity: activity({ title: 'the newer reading' }) })])]);
            await refreshAgentDashboard();
            const afterNewer = rowFor('pane:s1').label;
            slowResolve(snapshot([group([pane({ activity: activity({ title: 'the older reading' }) })])]));
            await slow;
            report({ afterNewer, afterSlowLanded: rowFor('pane:s1').label });
            """
        )
        self.assertEqual(result["afterNewer"], "the newer reading")
        self.assertEqual(result["afterSlowLanded"], "the newer reading")

    def test_a_failed_read_says_so_and_keeps_the_last_good_tree(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            fetchAnswer = null;
            await refreshAgentDashboard();
            const failed = { hidden: notice().hidden, text: notice().textContent, rows: parseRows().length };
            fetchAnswer = snapshot();
            await refreshAgentDashboard();
            report({ failed, recovered: { hidden: notice().hidden, rows: parseRows().length } });
            """
        )
        self.assertFalse(result["failed"]["hidden"])
        self.assertIn("Use Refresh to retry", result["failed"]["text"])
        self.assertEqual(result["failed"]["rows"], 3)
        self.assertTrue(result["recovered"]["hidden"])
        self.assertEqual(result["recovered"]["rows"], 3)

    def test_a_page_the_reader_cannot_see_suspends_the_poll_and_resumes_it(self):
        """The poll gate asks two things, not one: the dialog outlives the
        reader going elsewhere, so *open* no longer implies *being looked at*.
        Hidden stands the poll down and reads nothing on the way out; visible
        again re-arms it and reads once, and the dialog was there the whole
        time. Losing the focus alone does neither -- a window on another monitor
        is exactly what this is for."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const up = { armed: timers.armed, fetches: calls.fetches };

            /* Unfocused but visible: nothing changes at all. */
            documentFocused = false;
            fireWindow('blur', {});
            await settle();
            const unfocused = {
                open: dialogOpen(),
                armed: timers.armed,
                cleared: timers.cleared,
                fetches: calls.fetches
            };

            document.hidden = true;
            document.fire('visibilitychange');
            await settle();
            const away = {
                open: dialogOpen(),
                armed: timers.armed,
                cleared: timers.cleared,
                fetches: calls.fetches
            };

            document.hidden = false;
            documentFocused = true;
            document.fire('visibilitychange');
            await settle();
            report({
                up,
                unfocused,
                away,
                back: { open: dialogOpen(), armed: timers.armed, fetches: calls.fetches }
            });
            """
        )
        self.assertEqual(result["up"], {"armed": 1, "fetches": 1})
        # Unfocused: up, still armed, and nothing disarmed or read for it.
        self.assertEqual(
            result["unfocused"],
            {"open": True, "armed": 1, "cleared": 0, "fetches": 1},
        )
        # Hidden: still up, disarmed, nothing read on the way out.
        self.assertTrue(result["away"]["open"])
        self.assertEqual(result["away"]["armed"], 1)
        self.assertGreater(result["away"]["cleared"], result["unfocused"]["cleared"])
        self.assertEqual(result["away"]["fetches"], 1)
        # Back: re-armed and read once, on the dialog that never went away.
        self.assertEqual(
            result["back"], {"open": True, "armed": 2, "fetches": 2}
        )


# The crew board's page: the slot the module writes the board into, parsed back
# into node and crew-head elements whose slots count every write, so an in-place
# update is told apart from a rebuilt node. The wire layer is recorded rather
# than run; `test_agent_crews.py` runs the real one.
CREW_BOARD_STUBS = r"""
const HANDED = '2026-10-02T10:00:00+00:00';
const HANDED_S = Date.parse(HANDED) / 1000;

/* One assignment, as `GET /api/dashboard` lists it. */
function link(requester, worker, extra) {
    return Object.assign({
        link_id: `${requester}-${worker}`,
        requester_session_id: requester,
        worker_session_id: worker,
        state: 'working',
        read: true,
        status: '',
        collected: false,
        handed_at: HANDED,
        reported_at: '',
        round: 1,
        reason: ''
    }, extra || {});
}

/* An orchestrator (`s1`), two agents it handed tasks to (`s2`, `s3`), one
   more agent (`s4`) and a pane with nothing to do with any of it (`s5`). */
function crewReading(links, extra) {
    const more = extra || {};
    return snapshot([group([
        pane({ waiting: more.waiting || '', activity: activity({ title: more.s1 || 'Plan the release', state: 'idle' }) }),
        pane({ session_id: 's2', index: 1, agent_selection: 'codex',
            activity: activity({ title: more.s2 || 'Review the parser' }) }),
        pane({ session_id: 's3', index: 2, activity: activity({ title: 'Write the tests' }) }),
        pane({ session_id: 's4', index: 3, activity: activity({ title: 'Fix the build' }) }),
        pane({ session_id: 's5', index: 4, activity: activity({ title: 'On its own' }) })
    ])], { links, generated_at: HANDED_S + (more.age || 30) });
}

let slotWrites = 0;
function countedSlot(property, value) {
    let held = value;
    return Object.defineProperty({}, property, {
        get() { return held; },
        set(next) { held = next; slotWrites += 1; },
        enumerable: true
    });
}

function attributesOf(source) {
    const attributes = {};
    const pattern = /([a-zA-Z-]+)="([^"]*)"/g;
    let found;
    while ((found = pattern.exec(source)) !== null) { attributes[found[1]] = found[2]; }
    return attributes;
}

function datasetOf(attributes) {
    const dataset = {};
    Object.keys(attributes).filter(name => name.startsWith('data-'))
        .forEach(name => { dataset[camel(name.slice(5))] = attributes[name]; });
    return dataset;
}

function segments(html, opener) {
    const starts = [];
    let found;
    while ((found = opener.exec(html)) !== null) { starts.push({ at: found.index, match: found }); }
    return starts.map((start, index) => ({
        match: start.match,
        html: html.slice(start.at, index + 1 < starts.length ? starts[index + 1].at : html.length)
    }));
}

function first(pattern, html) {
    const found = pattern.exec(html);
    return found ? found[1] : '';
}

let boardWidth = 470;
function parseBoard(slot) {
    const html = slot.html;
    slot.board = /class="dash-crews-board"/.test(html) ? { scrollLeft: 0, clientWidth: boardWidth } : null;
    slot.nodes = segments(html, /<(button|div)\b([^>]*\bdata-crew-node="[^"]*"[^>]*)>/g).map(part => {
        const attributes = attributesOf(part.match[2]);
        const slots = {
            '.dash-crew-name': countedSlot('textContent',
                first(/<span class="dash-crew-name">([^<]*)<\/span>/, part.html)),
            '.dash-crew-line': countedSlot('textContent',
                first(/<span class="dash-crew-line">([^<]*)<\/span>/, part.html)),
            '.dash-crew-reading': countedSlot('innerHTML',
                first(/<span class="dash-crew-reading">([\s\S]*?)<\/span>\s*<span class="dash-agent-icon"/, part.html)),
            '.dash-crew-pill-slot': countedSlot('innerHTML',
                first(/<span class="dash-crew-pill-slot">((?:<span[^>]*>[^<]*<\/span>)?)<\/span>/, part.html)),
            '.dash-crew-round': countedSlot('textContent',
                first(/<span class="dash-crew-round">([^<]*)<\/span>/, part.html)),
            '.dash-crew-age': countedSlot('innerHTML',
                first(/<span class="dash-crew-age">((?:<span[^>]*>[^<]*<\/span>)?)<\/span>/, part.html))
        };
        return {
            tag: part.match[1],
            attributes,
            dataset: datasetOf(attributes),
            title: attributes.title || '',
            html: part.html,
            classList: fakeClassList(),
            closest(selector) { return selector === '[data-crew-node]' || selector === '[data-dashboard-action]' ? this : null; },
            slots,
            querySelector: selector => slots[selector] || null
        };
    });
    slot.heads = segments(html, /<div class="dash-crew" data-crew-root="([^"]*)"[^>]*>/g).map(part => {
        const slots = {
            '.dash-crew-title-line': countedSlot('textContent',
                first(/<span class="dash-crew-title-line">([^<]*)<\/span>/, part.html)),
            '.dash-crew-meta': countedSlot('textContent',
                first(/<span class="dash-crew-meta">([^<]*)<\/span>/, part.html)),
            '.dash-crew-segments': countedSlot('innerHTML',
                first(/<span class="dash-crew-segments" aria-hidden="true">([\s\S]*?)<\/span>/, part.html))
        };
        return { dataset: { crewRoot: part.match[1], dashboardKey: 'crew-frame:' + part.match[1] },
            classList: fakeClassList(), slots,
            closest(selector) { return selector === '[data-crew-root]' ? this : null; },
            querySelector: selector => slots[selector] || null };
    });
}

/* The slot the module writes the board into. A full render makes a new one; a
   board-only rebuild writes this one's `innerHTML`. */
function makeCrewSlot(html) {
    const slot = { html, rebuilds: 0, scrollTop: 0, classList: fakeClassList(), contains: () => false };
    let populated = Boolean(html.trim());
    Object.defineProperty(slot, 'innerHTML', {
        get() { return this.html; },
        set(next) { if (populated) this.rebuilds += 1; populated = true; this.html = next; parseBoard(this); }
    });
    slot.querySelector = selector => (selector === '.dash-crews-board' ? slot.board : null);
    slot.querySelectorAll = selector => ({
        '[data-crew-node]': slot.nodes,
        '[data-crew-root]': slot.heads
    }[selector] || []);
    parseBoard(slot);
    return slot;
}

const crewDom = { bodyHtml: null, slot: null };
function crewSlot() {
    const html = body().innerHTML;
    if (crewDom.bodyHtml !== html) {
        crewDom.bodyHtml = html;
        const open = '<div class="dash-crews-slot" data-dashboard-crews hidden>';
        const start = html.indexOf(open);
        if (start < 0) {
            crewDom.slot = null;
        } else {
            const rest = html.slice(start + open.length);
            /* The board is the body's final slot, after the permanent list. */
            crewDom.slot = makeCrewSlot(rest.replace(/<\/div>\s*$/, ''));
        }
    }
    return crewDom.slot;
}
function node(id) { return (crewSlot()?.nodes || []).find(entry => entry.dataset.crewNode === id) || null; }
function nodes() { return (crewSlot()?.nodes || []).map(entry => entry.dataset.crewNode); }
function head(root) { return (crewSlot()?.heads || []).find(entry => entry.dataset.crewRoot === root) || null; }
function cell(id) {
    const found = new RegExp(`<div class="dash-crew-cell" style="([^"]*)">\\s*<(?:button|div)\\b[^>]*data-crew-node="${id}"`)
        .exec(crewSlot().html);
    const value = name => Number(new RegExp(`--dash-crew-${name}:(\\d+)`).exec(found[1])[1]);
    return { col: value('col'), row: value('row'), span: value('span') };
}

body().querySelectorAll = () => [];
body().querySelector = selector => {
    if (selector === '[data-dashboard-crews]') return crewSlot();
    if (selector === '.dash-crews-board') return crewSlot()?.board || null;
    return null;
};

/* The wire layer, recorded. */
const wires = { made: [], paints: 0, paused: [], disposed: 0 };
const listWires = { paused: [], paints: 0 };
GridVibeAgentCrews.createWireLayer = options => {
    if (options.card === undefined) return {
        paint() { listWires.paints++; }, highlight() {},
        setPaused(value) { listWires.paused.push(value); }, dispose() {}
    };
    const made = { mode: options.mode, card: options.card, container: options.container };
    wires.made.push(made);
    return {
        paint() { wires.paints += 1; },
        highlight() {},
        setPaused(on) { wires.paused.push(on); },
        dispose() { wires.disposed += 1; }
    };
};
"""


class DashboardCrewBoardTestCase(DashboardDialogTestCase):
    """The crew board above the session list.

    It is drawn from the same reading as the list, against its own structure
    key: the shape of each crew (who sits where, and whether its pane is still
    open) rebuilds the board alone, and everything a node says -- its title,
    reading, phase pill, round and age -- is written in place, so a report or a
    follow-up round never replaces a node element. The list below is never
    touched by any of it."""

    def test_a_task_label_is_board_only_safe_text_and_updates_without_replacing_nodes(self):
        result = self._run_crew(r"""
            const firstLabel = '<img src=x onerror=evil()> & "review"';
            fetchAnswer = crewReading([link('s1', 's2', { label: firstLabel })]);
            fetchAnswer.workspaces[0].groups[0].panes[1].title = '<b>Parser worker</b>';
            await refreshAgentDashboard();
            const before = node('s2');
            const initialName = before.slots['.dash-crew-name'].textContent;
            const rootName = node('s1').slots['.dash-crew-name'].textContent;
            const initial = before.slots['.dash-crew-line'].textContent;
            const initialHtml = crewSlot().html;
            const nodeHead = first(/<span class="dash-crew-node-head">([\s\S]*?)<\/span>\s*<span class="dash-crew-line">/, before.html);
            const listHtml = body().innerHTML;
            const header = head('s1').slots['.dash-crew-title-line'].textContent;
            fetchAnswer = crewReading([link('s1', 's2', {
                label: '<script>evil()</script>', round: 2, link_id: 'round-two'
            })]);
            fetchAnswer.workspaces[0].groups[0].panes[1].title = '<b>Parser worker</b>';
            await refreshAgentDashboard();
            const second = node('s2').slots['.dash-crew-line'].textContent;
            const secondName = node('s2').slots['.dash-crew-name'].textContent;
            fetchAnswer = crewReading([link('s1', 's2', { round: 3, link_id: 'round-three' })]);
            fetchAnswer.workspaces[0].groups[0].panes[1].title = '<b>Parser worker</b>';
            await refreshAgentDashboard();
            const fallback = node('s2').slots['.dash-crew-line'].textContent;
            const fallbackName = node('s2').slots['.dash-crew-name'].textContent;
            const listUnchanged = body().innerHTML === listHtml;
            fetchAnswer.workspaces[0].groups[0].panes[1].title = '<script>Renamed worker</script>';
            await refreshAgentDashboard();
            report({ initial, initialHtml, second, initialName, rootName, secondName, fallbackName,
                nodeHead, fallback,
                renamed: node('s2').slots['.dash-crew-name'].textContent,
                fallbackAfterRename: node('s2').slots['.dash-crew-line'].textContent,
                same: before === node('s2'), rebuilds: crewSlot().rebuilds,
                listUnchanged,
                headerUnchanged: head('s1').slots['.dash-crew-title-line'].textContent === header,
                listHasLabel: listHtml.includes(firstLabel) || listHtml.includes('&lt;img')
            });
        """)
        # The small HTML parser keeps entities; a browser decodes them into text.
        self.assertEqual(result["initial"], '&lt;img src=x onerror=evil()&gt; &amp; &quot;review&quot;')
        self.assertNotIn('<img src=x', result["initialHtml"])
        self.assertIn('&lt;img src=x onerror=evil()&gt;', result["initialHtml"])
        self.assertEqual(result["initialName"], '&lt;b&gt;Parser worker&lt;/b&gt;')
        self.assertEqual(result["rootName"], "Claude Code")
        self.assertIn('class="dash-agent-icon"', result["nodeHead"])
        self.assertIn('class="dash-crew-name"', result["nodeHead"])
        self.assertNotIn('dash-crew-line', result["nodeHead"])
        self.assertNotIn('&lt;img', result["nodeHead"])
        self.assertNotIn('<b>Parser worker</b>', result["initialHtml"])
        self.assertEqual(result["secondName"], result["initialName"])
        self.assertEqual(result["fallbackName"], result["initialName"])
        self.assertEqual(result["renamed"], '<script>Renamed worker</script>')
        self.assertEqual(result["second"], '<script>evil()</script>')
        self.assertEqual(result["fallback"], "Review the parser")
        self.assertEqual(result["fallbackAfterRename"], result["fallback"])
        self.assertTrue(result["same"])
        self.assertEqual(result["rebuilds"], 0)
        self.assertTrue(result["listUnchanged"])
        self.assertTrue(result["headerUnchanged"])
        self.assertFalse(result["listHasLabel"])

    def _run_crew(self, body: str):
        return self._run_node(CREW_BOARD_STUBS + r"""
dashboardShown();
const readSelectedBoard = refreshAgentDashboard;
let boardSelectionInitialized = false;
refreshAgentDashboard = async () => {
    if (!boardSelectionInitialized && fetchAnswer?.links?.length) {
        for (const root of dashboardCrewContext(fetchAnswer).crews.roots) _agentDashboardSelectedCrews.add(root);
        boardSelectionInitialized = true;
    }
    return readSelectedBoard();
};
""" + body)

    def test_no_links_draw_no_board(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([]);
            await refreshAgentDashboard();
            const empty = body().innerHTML;
            fetchAnswer = snapshot([group([pane()])]);
            await refreshAgentDashboard();
            report({
                section: empty.includes('class="dash-crews"'),
                slot: crewSlot().html,
                agents: sectionCounts().agents,
                noLinksKey: body().innerHTML.includes('dash-crews"'),
                made: wires.made.length
            });
            """
        )
        self.assertFalse(result["section"])
        self.assertEqual(result["slot"], "")
        self.assertEqual(result["agents"], 1)
        self.assertFalse(result["noLinksKey"])
        self.assertEqual(result["made"], 0)

    def test_a_nested_crew_is_laid_out_by_depth_with_parents_spanning(self):
        """`s1` handed tasks to `s2` and `s3`; `s2` handed one on to `s4`.
        Depth-first in list order; a parent spans its leaves' rows; the list
        beside it is the list it always was."""
        result = self._run_crew(
            """
            fetchAnswer = crewReading([
                link('s1', 's2'), link('s2', 's4'), link('s1', 's3')
            ]);
            await refreshAgentDashboard();
            report({
                order: nodes(),
                cells: Object.fromEntries(nodes().map(id => [id, cell(id)])),
                depths: /--dash-crew-depths:(\\d+)/.exec(crewSlot().html)[1],
                crews: crewSlot().heads.map(entry => entry.dataset.crewRoot),
                meta: head('s1').slots['.dash-crew-meta'].textContent,
                segments: (head('s1').slots['.dash-crew-segments'].innerHTML.match(/dash-crew-segment/g) || []).length,
                title: head('s1').slots['.dash-crew-title-line'].textContent,
                agents: sectionCounts().agents,
                rootClass: node('s1').attributes.class
            });
            """
        )
        self.assertEqual(result["order"], ["s1", "s2", "s4", "s3"])
        self.assertEqual(result["cells"], {
            "s1": {"col": 1, "row": 1, "span": 2},
            "s2": {"col": 2, "row": 1, "span": 1},
            "s4": {"col": 3, "row": 1, "span": 1},
            "s3": {"col": 2, "row": 2, "span": 1},
        })
        self.assertEqual(result["depths"], "3")
        self.assertEqual(result["crews"], ["s1"])
        self.assertEqual(result["meta"], "0 of 3 reported")
        self.assertEqual(result["segments"], 3)
        self.assertEqual(result["title"], "Plan the release")
        # Every live session is still listed, crew or not.
        self.assertEqual(result["agents"], 5)
        self.assertIn("is-root", result["rootClass"])

    def test_a_node_carries_its_rows_target(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            const live = node('s2');
            report({
                live: { tag: live.tag, dataset: live.dataset },
                row: rowFor('pane:s2').dataset,
                agent: live.dataset.agent,
                line: live.slots['.dash-crew-line'].textContent
            });
            """
        )
        live = result["live"]
        self.assertEqual(live["tag"], "button")
        for name in ("dashboardAction", "workspaceId", "groupId", "sessionId"):
            with self.subTest(attribute=name):
                self.assertEqual(live["dataset"][name], result["row"][name])
        self.assertEqual(live["dataset"]["dashboardKey"], "crew:s2")
        self.assertEqual(result["agent"], "codex")

    def test_a_pane_that_is_not_listed_is_not_on_the_board(self):
        """The server stops publishing a closed pane's links, and the board
        draws only panes it can land on, so even a reading that still carried
        one would draw no ghost of it: no node, no pill saying the pane
        closed, and no dashed box."""
        result = self._run_crew(
            """
            fetchAnswer = crewReading([
                link('s1', 's2'),
                link('s1', 's9', { state: 'reported', status: 'done', reported_at: HANDED })
            ]);
            await refreshAgentDashboard();
            report({
                order: nodes(),
                html: crewSlot().html,
                meta: head('s1').slots['.dash-crew-meta'].textContent
            });
            """
        )
        self.assertEqual(result["order"], ["s1", "s2"])
        self.assertNotIn("pane closed", result["html"].lower())
        self.assertNotIn("is-ghost", result["html"])
        self.assertNotIn('data-crew-node="s9"', result["html"])
        self.assertEqual(result["meta"], "0 of 1 reported")

    def test_every_report_wears_its_status_and_collecting_is_only_in_words(self):
        result = self._run_crew(
            """
            const reported = (status, collected) => ({
                state: 'reported', status, collected, reported_at: HANDED
            });
            fetchAnswer = crewReading([
                link('s1', 's2', reported('done', false)),
                link('s1', 's3', reported('blocked', true)),
                link('s1', 's4', reported('failed', true))
            ]);
            await refreshAgentDashboard();
            fetchAnswer = crewReading([
                link('s1', 's2', reported('done', true)),
                link('s1', 's3', reported('blocked', true)),
                link('s1', 's4', reported('failed', true))
            ]);
            await refreshAgentDashboard();
            report({
                pills: ['s2', 's3', 's4'].map(id => node(id).slots['.dash-crew-pill-slot'].innerHTML),
                segments: head('s1').slots['.dash-crew-segments'].innerHTML,
                meta: head('s1').slots['.dash-crew-meta'].textContent
            });
            """
        )
        done, blocked, failed = result["pills"]
        self.assertIn('class="dash-crew-pill is-done"', done)
        self.assertIn(">done<", done)
        self.assertIn("has collected the report", done)
        self.assertIn('class="dash-crew-pill is-blocked"', blocked)
        self.assertIn('class="dash-crew-pill is-failed"', failed)
        # A report is never a pill of its own called "collected".
        for pill in result["pills"]:
            self.assertNotIn("is-collected", pill)
            self.assertNotIn(">collected<", pill)
        self.assertNotIn("is-collected", result["segments"])
        self.assertEqual(result["meta"], "3 of 3 reported")

    def test_an_uncollected_report_says_so_in_its_hover(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([link('s1', 's2', {
                state: 'reported', status: 'done', collected: false, reported_at: HANDED
            })]);
            await refreshAgentDashboard();
            report({ pill: node('s2').slots['.dash-crew-pill-slot'].innerHTML });
            """
        )
        self.assertIn("Reported done; its orchestrator has not collected the report yet", result["pill"])

    def test_the_dialog_is_big_while_a_crew_is_on_it_and_a_column_otherwise(self):
        result = self._run_crew(
            """
            const dialogEl = { on: false, classList: { toggle(name, force) {
                if (name === 'has-crews') dialogEl.on = Boolean(force);
            } } };
            body().parentElement = dialogEl;
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            const withCrew = dialogEl.on;
            fetchAnswer = crewReading([]);
            await refreshAgentDashboard();
            report({ withCrew, without: dialogEl.on });
            """
        )
        self.assertTrue(result["withCrew"])
        self.assertFalse(result["without"])

    def test_both_panes_keep_their_own_scroll_across_a_render(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            crewSlot().scrollTop = 120;
            listBody.scrollTop = 340;
            const before = crewSlot();
            fetchAnswer = crewReading([link('s1', 's2')]);
            fetchAnswer.workspaces[0].groups[0].panes[4].agent_selection = 'codex';
            await refreshAgentDashboard();
            report({ crews: crewSlot().scrollTop, sessions: listBody.scrollTop, same: before === crewSlot() });
        """)
        self.assertEqual(result["crews"], 120)
        self.assertEqual(result["sessions"], 340)
        self.assertTrue(result["same"])

    def test_a_report_keeps_every_node_and_writes_only_what_changed(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([link('s1', 's2'), link('s1', 's3', { read: false })]);
            await refreshAgentDashboard();
            const listHtml = body().innerHTML;
            const before = { s1: node('s1'), s2: node('s2'), s3: node('s3') };
            const firstPill = node('s2').slots['.dash-crew-pill-slot'].innerHTML;
            const handed = node('s3').slots['.dash-crew-pill-slot'].innerHTML;
            slotWrites = 0;
            fetchAnswer = crewReading([link('s1', 's2'), link('s1', 's3', { read: false })]);
            await refreshAgentDashboard();
            const idleWrites = slotWrites;
            fetchAnswer = crewReading([
                link('s1', 's2', { state: 'reported', status: 'done', reported_at: HANDED }),
                link('s1', 's3', { read: false })
            ], { age: 120 });
            await refreshAgentDashboard();
            report({
                sameNodes: ['s1', 's2', 's3'].every(id => node(id) === before[id]),
                rebuilds: crewSlot().rebuilds,
                sameList: body().innerHTML === listHtml,
                firstPill,
                handed,
                pill: node('s2').slots['.dash-crew-pill-slot'].innerHTML,
                untouched: node('s3').slots['.dash-crew-pill-slot'].innerHTML,
                age: node('s2').slots['.dash-crew-age'].innerHTML,
                meta: head('s1').slots['.dash-crew-meta'].textContent,
                segments: head('s1').slots['.dash-crew-segments'].innerHTML,
                idleWrites,
                made: wires.made.length,
                paints: wires.paints
            });
            """
        )
        self.assertTrue(result["sameNodes"])
        self.assertEqual(result["rebuilds"], 0)
        self.assertTrue(result["sameList"])
        self.assertIn("is-working", result["firstPill"])
        self.assertIn('class="dash-crew-pill is-handed"', result["handed"])
        self.assertIn('class="dash-crew-pill is-done"', result["pill"])
        self.assertIn(">done<", result["pill"])
        self.assertEqual(result["untouched"], result["handed"])
        self.assertIn(">2m<", result["age"])
        self.assertIn("Reported 2m ago", result["age"])
        self.assertEqual(result["meta"], "1 of 2 reported")
        self.assertIn("is-done", result["segments"])
        # An unchanged poll writes nothing at all.
        self.assertEqual(result["idleWrites"], 0)
        # One layer, painted after every reading.
        self.assertEqual(result["made"], 1)
        self.assertEqual(result["paints"], 3)

    def test_a_follow_up_round_keeps_the_node_and_counts_from_round_two(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([link('s1', 's2', {
                link_id: 'a', state: 'reported', status: 'done', collected: true, reported_at: HANDED
            })]);
            await refreshAgentDashboard();
            const before = node('s2');
            const roundOne = node('s2').slots['.dash-crew-round'].textContent;
            const markup = crewSlot().html;
            fetchAnswer = crewReading([link('s1', 's2', { link_id: 'b', round: 2, read: false })]);
            await refreshAgentDashboard();
            report({
                roundOne,
                roundOneInMarkup: /round \\d/.test(markup),
                same: node('s2') === before,
                rebuilds: crewSlot().rebuilds,
                round: node('s2').slots['.dash-crew-round'].textContent,
                pill: node('s2').slots['.dash-crew-pill-slot'].innerHTML,
                hover: node('s2').title,
                linkIdInMarkup: crewSlot().html.includes('data-link') || crewSlot().html.includes('"b"')
            });
            """
        )
        self.assertEqual(result["roundOne"], "")
        self.assertFalse(result["roundOneInMarkup"])
        self.assertTrue(result["same"])
        self.assertEqual(result["rebuilds"], 0)
        self.assertEqual(result["round"], "round 2")
        self.assertIn("is-handed", result["pill"])
        self.assertIn("· round 2", result["hover"])
        self.assertFalse(result["linkIdInMarkup"])

    def test_a_new_worker_rebuilds_the_board_and_leaves_the_list(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            const listHtml = body().innerHTML;
            const slot = crewSlot();
            fetchAnswer = crewReading([link('s1', 's2'), link('s1', 's3')]);
            await refreshAgentDashboard();
            report({
                sameSlot: crewSlot() === slot,
                rebuilds: slot.rebuilds,
                order: nodes(),
                sameList: body().innerHTML === listHtml,
                made: wires.made.length,
                disposed: wires.disposed
            });
            """
        )
        self.assertTrue(result["sameSlot"])
        self.assertEqual(result["rebuilds"], 1)
        self.assertEqual(result["order"], ["s1", "s2", "s3"])
        self.assertTrue(result["sameList"])
        # The board element was replaced, so its wires are made again on it.
        self.assertEqual(result["made"], 2)
        self.assertEqual(result["disposed"], 1)

    def test_a_list_change_keeps_the_boards_sideways_scroll(self):
        """A change elsewhere in the list re-renders the body, board included;
        a deep crew the reader scrolled sideways stays where it was."""
        result = self._run_crew(
            """
            fetchAnswer = crewReading([link('s1', 's2'), link('s2', 's4')]);
            await refreshAgentDashboard();
            const before = crewSlot().board;
            before.scrollLeft = 190;
            const reading = crewReading([link('s1', 's2'), link('s2', 's4')]);
            reading.workspaces[0].groups[0].panes[4].agent_mcp = true;
            fetchAnswer = reading;
            await refreshAgentDashboard();
            report({
                replaced: crewSlot().board !== before,
                scrollLeft: crewSlot().board.scrollLeft,
                order: nodes()
            });
            """
        )
        self.assertFalse(result["replaced"])
        self.assertEqual(result["scrollLeft"], 190)
        self.assertEqual(result["order"], ["s1", "s2", "s4"])

    def test_the_last_link_gone_takes_the_board_and_its_wires(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            fetchAnswer = crewReading([]);
            await refreshAgentDashboard();
            report({ slot: crewSlot().html, disposed: wires.disposed });
            """
        )
        self.assertEqual(result["slot"], "")
        self.assertEqual(result["disposed"], 1)

    def test_an_ended_pill_names_why_it_ended(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([
                link('s1', 's2', { state: 'ended', reason: 'agent exited' }),
                link('s1', 's3', { state: 'ended', reason: 'something new' })
            ]);
            await refreshAgentDashboard();
            report({
                exited: node('s2').slots['.dash-crew-pill-slot'].innerHTML,
                other: node('s3').slots['.dash-crew-pill-slot'].innerHTML,
                meta: head('s1').slots['.dash-crew-meta'].textContent
            });
            """
        )
        self.assertIn('title="Ended: Its agent exited before it reported"', result["exited"])
        self.assertIn(">ended<", result["exited"])
        self.assertIn('title="Ended before it reported"', result["other"])
        self.assertEqual(result["meta"], "0 of 2 reported")

    def test_a_restored_link_reads_as_history_and_never_as_an_uncollected_report(self):
        """A link restored after a restart is drawn by its phase like any
        other, but its report's text was not kept: the hover says so instead
        of claiming the orchestrator has (or has not) collected it. One that
        was in flight says GridVibe restarted before it reported."""
        result = self._run_crew(
            """
            fetchAnswer = crewReading([
                link('s1', 's2', {
                    state: 'reported', status: 'done', collected: false,
                    reported_at: HANDED, restored: true
                }),
                link('s1', 's3', {
                    state: 'reported', status: 'blocked', collected: true,
                    reported_at: HANDED, restored: true
                }),
                link('s1', 's4', { state: 'ended', reason: 'restarted', restored: true })
            ]);
            await refreshAgentDashboard();
            report({
                pills: ['s2', 's3', 's4'].map(id => node(id).slots['.dash-crew-pill-slot'].innerHTML),
                meta: head('s1').slots['.dash-crew-meta'].textContent
            });
            """
        )
        done, blocked, ended = result["pills"]
        self.assertIn('class="dash-crew-pill is-done"', done)
        self.assertIn(
            'title="Reported done; reported before GridVibe restarted; the report was not kept"',
            done,
        )
        self.assertIn('class="dash-crew-pill is-blocked"', blocked)
        self.assertIn("the report was not kept", blocked)
        for pill in (done, blocked):
            self.assertNotIn("collected", pill)
        self.assertIn('title="Ended: GridVibe restarted before it reported"', ended)
        self.assertIn(">ended<", ended)
        self.assertEqual(result["meta"], "2 of 3 reported")

    def test_the_waiting_orchestrator_says_how_many_it_waits_on(self):
        result = self._run_crew(
            """
            fetchAnswer = crewReading([
                link('s1', 's2'),
                link('s1', 's3', { read: false }),
                link('s1', 's4', { state: 'reported', status: 'blocked', reported_at: HANDED })
            ], { waiting: 'crew' });
            await refreshAgentDashboard();
            const waiting = node('s1').slots['.dash-crew-pill-slot'].innerHTML;
            fetchAnswer = crewReading([link('s1', 's2'), link('s1', 's3', { read: false }),
                link('s1', 's4', { state: 'reported', status: 'blocked', reported_at: HANDED })]);
            await refreshAgentDashboard();
            report({ waiting, after: node('s1').slots['.dash-crew-pill-slot'].innerHTML,
                blocked: node('s4').slots['.dash-crew-pill-slot'].innerHTML });
            """
        )
        self.assertIn('class="dash-crew-pill is-waiting"', result["waiting"])
        self.assertIn(">waiting on 2<", result["waiting"])
        self.assertEqual(result["after"], "")
        self.assertIn('class="dash-crew-pill is-blocked"', result["blocked"])

    def test_a_narrow_board_draws_lanes_and_a_wide_one_fans(self):
        result = self._run_crew(
            """
            boardWidth = 300;
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            const narrow = wires.made.map(entry => [entry.mode, entry.card]);
            crewSlot().board.clientWidth = 470;
            fetchAnswer = crewReading([link('s1', 's2', { read: false })]);
            await refreshAgentDashboard();
            report({
                narrow,
                modes: wires.made.map(entry => entry.mode),
                disposed: wires.disposed,
                container: wires.made[1].container === crewSlot().board
            });
            """
        )
        self.assertEqual(result["narrow"], [["lane", ""]])
        self.assertEqual(result["modes"], ["lane", "fan"])
        self.assertEqual(result["disposed"], 1)
        self.assertTrue(result["container"])

    def test_the_wires_stop_with_the_poll(self):
        result = self._run_crew(
            """
            shell().classList.remove('visible');
            fetchAnswer = crewReading([link('s1', 's2')]);
            showDashboard();
            await settle();
            const made = wires.paused.slice();
            document.hidden = true;
            document.fire('visibilitychange');
            const hidden = wires.paused[wires.paused.length - 1];
            document.hidden = false;
            document.fire('visibilitychange');
            await settle();
            const back = wires.paused[wires.paused.length - 1];
            closeAgentDashboardDialog();
            report({ made, hidden, back, shut: wires.paused[wires.paused.length - 1] });
            """
        )
        self.assertEqual(result["made"], [False])
        self.assertTrue(result["hidden"])
        self.assertFalse(result["back"])
        self.assertTrue(result["shut"])

    def test_a_node_press_opens_the_pane_it_names(self):
        result = self._run_crew(
            """
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            body().fire('click', {
                target: { closest: () => ({ dataset: node('s2').dataset }) },
                preventDefault() {}
            });
            await settle();
            report({ opened: calls.openWorkspaceWindow, targets: calls.focusTargets });
            """
        )
        self.assertEqual(
            result["opened"], [{"workspaceId": "default", "options": {"groupId": "g1"}}]
        )
        self.assertEqual(result["targets"][0]["options"], {"groupId": "g1", "sessionId": "s2"})
        self.assertEqual(result["targets"][0]["openedSoFar"], 0)


class DashboardSelectedCrewsTestCase(DashboardDialogTestCase):
    """The shipped dialog: one shared list and boards selected by the reader."""

    def _run_crew(self, body: str):
        return self._run_node(CREW_BOARD_STUBS + "\ndashboardShown();\n" + body)

    def test_opening_a_crew_clears_the_sidebar_hover_and_focus_highlight(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            listBody.fire('pointerover', { target: listRow('s2') });
            listBody.fire('focusin', { target: listRow('s2') });
            const before = GridVibeAgentCrews.highlightState.get();
            listBody.fire('contextmenu', { target: listRow('s2'), preventDefault() {} });
            const opened = { state: GridVibeAgentCrews.highlightState.get(),
                frame: head('s1').classList.contains('is-crew-member'),
                list: listRow('s2').classList.contains('is-crew-member') };
            fetchAnswer = crewReading([link('s1', 's2', { round: 2 })]);
            await refreshAgentDashboard();
            const refreshed = GridVibeAgentCrews.highlightState.get();
            body().fire('click', { target: head('s1'), preventDefault() {} });
            report({ before, opened, refreshed, clicked: GridVibeAgentCrews.highlightState.get() });
        """)
        self.assertEqual(result["before"], "s1")
        self.assertEqual(result["opened"], {"state": "", "frame": False, "list": False})
        self.assertEqual(result["refreshed"], "")
        self.assertEqual(result["clicked"], "s1")

    def test_dialog_width_tracks_the_deepest_open_crew(self):
        result = self._run_crew("""
            const values = {};
            body().parentElement = { classList: fakeClassList(),
                style: { setProperty(name, value) { values[name] = value; } } };
            const depth = () => values['--dash-crew-depths'];
            fetchAnswer = crewReading([link('s1', 's2'), link('s2', 's5'), link('s3', 's4')]);
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s3');
            const shallow = depth();
            toggleAgentDashboardCrew('s1');
            const deep = depth();
            toggleAgentDashboardCrew('s1');
            const afterHide = depth();
            toggleAgentDashboardCrew('s1');
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            report({ shallow, deep, afterHide, afterRemoval: depth() });
        """)
        self.assertEqual(result, {"shallow": "2", "deep": "3", "afterHide": "2", "afterRemoval": "2"})

    def test_opening_another_graph_clears_the_existing_highlight_and_keeps_both_open(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard(); toggleAgentDashboardCrew('s1');
            body().fire('click', { target: head('s1'), preventDefault() {} });
            const before = GridVibeAgentCrews.highlightState.get();
            listBody.fire('contextmenu', { target: listRow('s4'), preventDefault() {} });
            const cleared = { state: GridVibeAgentCrews.highlightState.get(),
                highlighted: crewSlot().heads.filter(frame => frame.classList.contains('is-crew-member')).map(frame => frame.dataset.crewRoot),
                members: listBody.querySelectorAll().filter(row => row.classList.contains('is-crew-member')).map(row => row.dataset.sessionId) };
            await refreshAgentDashboard();
            listBody.fire('pointerleave', {});
            report({ before, cleared, roots: crewSlot().heads.map(frame => frame.dataset.crewRoot),
                afterPoll: GridVibeAgentCrews.highlightState.get() });
        """)
        self.assertEqual(result["before"], "s1")
        self.assertEqual(result["cleared"], {"state": "", "highlighted": [], "members": []})
        self.assertEqual(result["roots"], ["s1", "s3"])
        self.assertEqual(result["afterPoll"], "")

    def test_removing_the_deepest_branch_shrinks_the_diagram_depth(self):
        result = self._run_crew(r"""
            fetchAnswer = crewReading([link('s1', 's2'), link('s2', 's3')]);
            await refreshAgentDashboard(); toggleAgentDashboardCrew('s1');
            const depth = () => Number(/--dash-crew-depths:(\d+)/.exec(crewSlot().html)[1]);
            const nested = depth();
            fetchAnswer = crewReading([link('s1', 's2')]);
            fetchAnswer.workspaces[0].groups[0].panes = fetchAnswer.workspaces[0].groups[0].panes.filter(pane => pane.session_id !== 's3');
            await refreshAgentDashboard();
            report({ nested, compact: depth(), nodes: nodes(), heads: crewSlot().heads.length,
                barsBelowTitle: crewSlot().html.indexOf('dash-crew-segments') > crewSlot().html.indexOf('dash-crew-title-line'),
                controls: [...crewSlot().html.matchAll(/data-dashboard-action="([^"]*)"/g)].map(match => match[1]) });
        """)
        self.assertEqual(result["nested"], 3)
        self.assertEqual(result["compact"], 2)
        self.assertEqual(result["nodes"], ["s1", "s2"])
        self.assertEqual(result["heads"], 1)
        self.assertTrue(result["barsBelowTitle"])
        self.assertEqual(result["controls"], ["hide-crew", "pane", "pane"])

    def test_an_unshown_crew_does_not_dim_every_board_and_list_navigation_clears_click(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard(); toggleAgentDashboardCrew('s3');
            listBody.fire('pointerover', { target: listRow('s2') });
            const unshown = crewSlot().classList.contains('is-crew-highlight');
            listBody.fire('pointerleave', {});
            body().fire('click', { target: head('s3'), preventDefault() {} });
            await settle();
            const clicked = GridVibeAgentCrews.highlightState.get();
            listBody.fire('click', { target: listRow('s2'), preventDefault() {} });
            await settle();
            listBody.fire('pointerleave', {});
            report({ unshown, clicked, cleared: GridVibeAgentCrews.highlightState.get() });
        """)
        self.assertFalse(result["unshown"])
        self.assertEqual(result["clicked"], "s3")
        self.assertEqual(result["cleared"], "")

    def test_refocusing_a_row_after_rebuild_does_not_select_its_crew(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([]);
            await refreshAgentDashboard();
            focusIsInside = true;
            focusTarget = listRow('s2');
            focusTarget.focus = function () { document.activeElement = this; };
            document.activeElement = focusTarget;
            fetchAnswer = crewReading([link('s1', 's2')]);
            fetchAnswer.workspaces[0].groups[0].name = 'Renamed session';
            await refreshAgentDashboard();
            report({ state: GridVibeAgentCrews.highlightState.get(), focused: listRow('s2').classList.contains('is-crew-member') });
        """)
        self.assertEqual(result["state"], "")
        self.assertFalse(result["focused"])

    def test_frame_click_selects_the_crew_and_tile_hover_keeps_that_selection(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s1'); toggleAgentDashboardCrew('s3');
            const members = () => listBody.querySelectorAll().filter(row => row.classList.contains('is-crew-member')).map(row => row.dataset.sessionId);
            listBody.fire('pointerover', { target: listRow('s2') });
            const listToBoard = head('s1').classList.contains('is-crew-member');
            listBody.fire('pointerleave', {});
            body().fire('pointerover', { target: node('s4') });
            const beforeClick = members();
            body().fire('click', { target: head('s3'), preventDefault() {} });
            const boardToList = members();
            for (const id of ['s1', 's2', 's3', 's4']) {
                body().fire('pointerover', { target: node(id) });
                body().fire('focusin', { target: node(id) });
                body().fire('pointerout', { target: node(id), relatedTarget: null });
            }
            const afterMoving = members();
            body().fire('click', { target: head('s3'), preventDefault() {} });
            report({ listToBoard, beforeClick, boardToList, afterMoving, toggled: members(), opened: calls.openWorkspaceWindow });
        """)
        self.assertTrue(result["listToBoard"])
        self.assertEqual(result["beforeClick"], [])
        self.assertEqual(result["boardToList"], ["s3", "s4"])
        self.assertEqual(result["afterMoving"], ["s3", "s4"])
        self.assertEqual(result["toggled"], [])
        self.assertEqual(result["opened"], [])

    def test_frame_highlight_survives_dialog_dismissal_and_clears_when_crew_disappears(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard(); toggleAgentDashboardCrew('s1');
            body().fire('click', { target: head('s1'), preventDefault() {} });
            closeAgentDashboardDialog();
            const clicked = GridVibeAgentCrews.highlightState.get();
            dashboardShown();
            fetchAnswer = crewReading([]);
            await refreshAgentDashboard();
            report({ clicked, closed: GridVibeAgentCrews.highlightState.get(), nodes: nodes() });
        """)
        self.assertEqual(result["clicked"], "s1")
        self.assertEqual(result["closed"], "")
        self.assertEqual(result["nodes"], [])

    def test_list_follows_frame_clicks_after_back_forward_cache_but_not_after_unload(self):
        """A cached page returns with the same list controller, so pagehide only
        suspends it; a real unload disposes it and its highlight subscription."""
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s1');
            const member = () => listRow('s2').classList.contains('is-crew-member');
            const clickFrame = () => body().fire('click', { target: head('s1'), preventDefault() {} });
            fireWindow('pagehide', { persisted: true });
            const cachedPaused = listWires.paused.at(-1);
            fireWindow('pageshow', { persisted: true });
            await settle();
            const restoredPaused = listWires.paused.at(-1);
            clickFrame();
            const restored = member();
            GridVibeAgentCrews.highlightState.clearClicks();
            fireWindow('pagehide', { persisted: false });
            clickFrame();
            report({ cachedPaused, restoredPaused, restored,
                state: GridVibeAgentCrews.highlightState.get(), unloaded: member() });
        """)
        self.assertTrue(result["restored"])
        self.assertTrue(result["cachedPaused"])
        self.assertFalse(result["restoredPaused"])
        self.assertEqual(result["state"], "s1")
        self.assertFalse(result["unloaded"])

    def test_tile_click_still_navigates_without_selecting_its_crew(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s1'); toggleAgentDashboardCrew('s3');
            body().fire('click', { target: head('s3'), preventDefault() {} });
            body().fire('click', { target: node('s2'), preventDefault() {} });
            await settle();
            report({ selected: GridVibeAgentCrews.highlightState.get(), targets: calls.focusTargets });
        """)
        self.assertEqual(result["selected"], "s3")
        self.assertEqual(result["targets"][0]["options"], {"groupId": "g1", "sessionId": "s2"})

    def test_board_close_hides_only_its_crew_without_closing_agents_or_navigating(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s1'); toggleAgentDashboardCrew('s3');
            body().fire('click', { target: head('s1'), preventDefault() {} });
            const match = /<button[^>]*class="dash-session-close dash-crew-close"([^>]*data-crew-id="s1"[^>]*)>/.exec(crewSlot().html);
            const attrs = attributesOf(match[1]);
            const close = { dataset: datasetOf(attrs) };
            close.closest = selector => selector === '[data-dashboard-action]' ? close : null;
            const ran = [];
            window.GridVibeDashboardClose = { handles: () => true, run: (...args) => ran.push(args) };
            body().fire('click', { target: close, preventDefault() {} });
            await settle();
            const afterOne = { nodes: nodes(), state: GridVibeAgentCrews.highlightState.get(), agents: sectionCounts().agents };
            body().fire('click', { target: { closest: () => ({ dataset: { dashboardAction: 'hide-crew', crewId: 's3' } }) }, preventDefault() {} });
            const empty = crewSlot().hidden;
            toggleAgentDashboardCrew('s1');
            report({ ran, opened: calls.openWorkspaceWindow, up: dialogOpen(), label: attrs['aria-label'], afterOne, empty, reopened: nodes() });
        """)
        self.assertEqual(result["ran"], [])
        self.assertEqual(result["opened"], [])
        self.assertTrue(result["up"])
        self.assertEqual(result["label"], "Hide this crew diagram")
        self.assertEqual(result["afterOne"], {"nodes": ["s3", "s4"], "state": "", "agents": 5})
        self.assertTrue(result["empty"])
        self.assertEqual(result["reopened"], ["s1", "s2"])

    def test_closing_a_focused_graph_clears_its_sidebar_highlight(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s1'); toggleAgentDashboardCrew('s3');
            const members = () => listBody.querySelectorAll().filter(row => row.classList.contains('is-crew-member')).map(row => row.dataset.sessionId);
            body().fire('click', { target: head('s1'), preventDefault() {} });
            const before = members();
            const row = listRow('s1');
            row.focus = function () {
                document.activeElement = this;
                listBody.fire('focusin', { target: this });
            };
            const close = { dataset: { dashboardAction: 'hide-crew', crewId: 's1',
                sessionId: 's1', dashboardKey: 'hide-crew:s1' } };
            close.closest = selector => selector === '[data-dashboard-action]' ? close : null;
            crewSlot().contains = element => element === close;
            document.activeElement = close;
            body().fire('click', { target: close, preventDefault() {} });
            const afterClose = members();
            const focusReturned = document.activeElement === row;
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            const afterPoll = members();
            listBody.fire('pointerover', { target: row });
            const hovered = members();
            listBody.fire('pointerleave', {});
            report({ before, afterClose, focusReturned, afterPoll, hovered, left: members() });
        """)
        self.assertEqual(result["before"], ["s1", "s2"])
        self.assertTrue(result["focusReturned"])
        self.assertEqual(result["afterClose"], [])
        self.assertEqual(result["afterPoll"], [])
        self.assertEqual(result["hovered"], ["s1", "s2"])
        self.assertEqual(result["left"], [])

    def test_crew_frame_keyboard_selects_without_activating_nested_tiles(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard(); toggleAgentDashboardCrew('s1');
            let prevented = 0;
            body().fire('keydown', { target: head('s1'), key: 'Enter', preventDefault() { prevented++; } });
            const selected = GridVibeAgentCrews.highlightState.get();
            body().fire('keydown', { target: node('s2'), key: 'Enter', preventDefault() { prevented++; } });
            body().fire('keydown', { target: head('s1'), key: ' ', preventDefault() { prevented++; } });
            report({ selected, cleared: GridVibeAgentCrews.highlightState.get(), prevented });
        """)
        self.assertEqual(result, {"selected": "s1", "cleared": "", "prevented": 2})

    def test_list_click_routes_once_and_list_wires_pause_with_the_dialog(self):
        result = self._run_crew("""
            wireAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            const event = { target: listRow('s2'), preventDefault() {} };
            listBody.fire('click', event);
            body().fire('click', event); // the same event bubbling to the body
            await settle();
            document.hidden = true;
            scheduleAgentDashboardRefresh();
            const hidden = listWires.paused.at(-1);
            document.hidden = false;
            scheduleAgentDashboardRefresh();
            const visible = listWires.paused.at(-1);
            closeAgentDashboardDialog();
            report({ opened: calls.openWorkspaceWindow, hidden, visible, shut: listWires.paused.at(-1) });
        """)
        self.assertEqual(result["opened"], [{"workspaceId": "default", "options": {"groupId": "g1"}}])
        self.assertTrue(result["hidden"])
        self.assertFalse(result["visible"])
        self.assertTrue(result["shut"])

    def test_list_navigation_keeps_the_target_resolvers_retry_notice(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            workspaceOpens = false;
            listBody.fire('click', { target: listRow('s2'), preventDefault() {} });
            await settle();
            report({ text: notice().textContent, up: dialogOpen() });
        """)
        self.assertEqual(result["text"], WORKSPACE_TAB_BLOCKED_HINT)
        self.assertTrue(result["up"])

    def test_a_removed_board_returns_focus_to_its_member_in_the_list(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s1');
            crewSlot().contains = () => true;
            document.activeElement = node('s2');
            toggleAgentDashboardCrew('s1');
            report({ focused: Boolean(listRow('s2').focused), hidden: crewSlot().hidden });
        """)
        self.assertTrue(result["focused"])
        self.assertTrue(result["hidden"])

    def test_the_list_uses_the_sidebar_renderer_and_boards_start_hidden(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            const runtime = { esc: escHtml, activity: dashboardActivityHtml, progress: dashboardProgressHtml,
                glyph: dashboardAgentGlyphHtml, glyphKey: dashboardAgentGlyphKey, line: dashboardPaneLine,
                hover: dashboardPaneHover, agentName: dashboardAgentName, workspaceLabel: dashboardWorkspaceLabel,
                sessionColourStyle: dashboardSessionColourStyle };
            report({ same: listBody.innerHTML === GridVibeDashboardSidebar.policy.bodyHtml(
                GridVibeDashboardSidebar.policy.withoutWaiting(fetchAnswer), runtime, dashboardCloseActions()),
                agents: sectionCounts().agents, hidden: crewSlot().hidden, nodes: nodes(),
                hint: listRow('s2').title, words: listRow('s2').slots['.dash-agent-selection'].textContent });
        """)
        self.assertTrue(result["same"])
        self.assertEqual(result["agents"], 5)
        self.assertTrue(result["hidden"])
        self.assertEqual(result["nodes"], [])
        self.assertIn("Right-click to show this crew", result["hint"])
        self.assertIn("Shift+F10", result["words"])

    def test_mouse_and_keyboard_toggle_crews_in_selection_order_and_ignore_plain_rows(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            let prevented = 0;
            const toggle = (id, type = 'contextmenu', extra = {}) => listBody.fire(type, {
                target: listRow(id), preventDefault() { prevented++; }, ...extra });
            toggle('s5');
            const plain = { prevented, nodes: nodes() };
            toggle('s4');
            toggle('s2', 'keydown', { key: 'ContextMenu' });
            const both = { roots: crewSlot().heads.map(h => h.dataset.crewRoot), hidden: crewSlot().hidden,
                selected: listRow('s1').classList.contains('is-crew-selected'), title: listRow('s1').title };
            toggle('s1', 'keydown', { key: 'F10', shiftKey: true });
            const one = nodes();
            toggle('s3');
            report({ plain, both, one, none: nodes(), hidden: crewSlot().hidden, prevented,
                selection: listRow('s3').slots['.dash-agent-selection'].textContent });
        """)
        self.assertEqual(result["plain"], {"prevented": 0, "nodes": []})
        self.assertEqual(result["both"]["roots"], ["s3", "s1"])
        self.assertFalse(result["both"]["hidden"])
        self.assertTrue(result["both"]["selected"])
        self.assertIn("Crew shown", result["both"]["title"])
        self.assertEqual(result["one"], ["s3", "s4"])
        self.assertEqual(result["none"], [])
        self.assertTrue(result["hidden"])
        self.assertEqual(result["prevented"], 4)
        self.assertIn("show this crew", result["selection"])

    def test_context_menu_key_claims_both_defaults_and_toggles_once(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            const prevented = [];
            for (const type of ['keydown', 'keyup']) {
                let claimed = false;
                listBody.fire(type, { target: listRow('s2'), key: 'ContextMenu',
                    preventDefault() { claimed = true; } });
                prevented.push(claimed);
                if (!claimed) listBody.fire('contextmenu', { target: listRow('s2'), preventDefault() {} });
            }
            let plainClaimed = false;
            listBody.fire('keyup', { target: listRow('s5'), key: 'ContextMenu',
                preventDefault() { plainClaimed = true; } });
            report({ prevented, nodes: nodes(), plainClaimed });
        """)
        self.assertEqual(result["prevented"], [True, True])
        self.assertEqual(result["nodes"], ["s1", "s2"])
        self.assertFalse(result["plainClaimed"])

    def test_selection_survives_close_and_an_ended_crew_leaves_automatically(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s1');
            toggleAgentDashboardCrew('s3');
            closeAgentDashboardDialog();
            showDashboard();
            await settle();
            const kept = nodes();
            fetchAnswer = crewReading([link('s3', 's4')]);
            await refreshAgentDashboard();
            const remaining = nodes();
            fetchAnswer = crewReading([]);
            await refreshAgentDashboard();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            report({ kept, remaining, none: nodes(), hidden: crewSlot().hidden,
                selected: [..._agentDashboardSelectedCrews] });
        """)
        self.assertEqual(result["kept"], ["s1", "s2", "s3", "s4"])
        self.assertEqual(result["remaining"], ["s3", "s4"])
        self.assertEqual(result["none"], [])
        self.assertTrue(result["hidden"])
        self.assertEqual(result["selected"], [])

    def test_marks_waiting_and_selection_are_decorations_on_the_shared_rows(self):
        result = self._run_crew("""
            fetchAnswer = crewReading([link('s1', 's2')]);
            await refreshAgentDashboard();
            const before = listRow('s1');
            const rebuilds = listBody.rebuilds;
            listBody.scrollTop = 137;
            fetchAnswer.workspaces[0].groups[0].panes[0] = { ...fetchAnswer.workspaces[0].groups[0].panes[0],
                agent_mcp: true, agent_mcp_override: true, agent_auto_mode: true, waiting: 'crew' };
            await refreshAgentDashboard();
            toggleAgentDashboardCrew('s1');
            report({ same: before === listRow('s1'), rebuilds: listBody.rebuilds - rebuilds,
                scroll: listBody.scrollTop, mark: before.icon.dataset, title: before.icon.title,
                reading: before.slots['.dash-agent-reading'].innerHTML,
                flags: before.slots['.dash-agent-flags'].textContent,
                selected: before.classList.contains('is-crew-selected') });
        """)
        self.assertTrue(result["same"])
        self.assertEqual(result["rebuilds"], 0)
        self.assertEqual(result["scroll"], 137)
        self.assertEqual(result["mark"], {"mcp": "on", "mcpOverride": "on", "auto": "on"})
        self.assertIn("override", result["title"])
        self.assertIn("auto-approval", result["flags"])
        self.assertIn("dash-state-waiting", result["reading"])
        self.assertTrue(result["selected"])


class DashboardCrewBoardStylingTestCase(unittest.TestCase):
    """The board's stylesheet hooks: the narrow breakpoint is the module's own
    constant, and the board's colours are the status tokens."""

    CSS = REPO_ROOT / "web" / "static" / "css" / "agent-dashboard.css"

    def test_the_narrow_rule_is_the_modules_breakpoint(self):
        css = self.CSS.read_text(encoding="utf-8")
        script = DASHBOARD_DIALOG_JS.read_text(encoding="utf-8")
        width = script.split("const DASHBOARD_CREW_NARROW_PX = ", 1)[1].split(";", 1)[0]
        self.assertIn(f"@container dash-crews (max-width: {width}px)", css)
        self.assertIn("container: dash-crews / inline-size;", css)

    def test_the_dialog_grows_to_its_window_only_while_a_crew_is_on_it(self):
        css = self.CSS.read_text(encoding="utf-8")
        rule = re.search(r"\.dash-dialog\.has-crews \{([^}]*)\}", css)
        self.assertIsNotNone(rule)
        # Three quarters of the window it opened in, in each direction, and no
        # more: the dialog never covers the page behind it.
        self.assertIn("width: 75vw;", rule.group(1))
        self.assertIn("height: 75vh;", rule.group(1))
        # The column it always was is the unqualified rule, under the same cap.
        base = re.search(r"\n\.dash-dialog \{([^}]*)\}", css)
        self.assertIsNotNone(base)
        self.assertIn("width: min(380px, 75vw);", base.group(1))
        self.assertIn("height: min(75vh, 820px);", base.group(1))
        # No narrow-window override puts it back to the whole window.
        self.assertNotIn(".dash-dialog.has-crews { height: 100%; }", css)

    def test_the_board_and_the_list_sit_side_by_side_only_when_there_is_room(self):
        css = self.CSS.read_text(encoding="utf-8")
        wide = re.search(r"@media \(min-width: (\d+)px\) \{(.*?)\n\}\n", css, flags=re.S)
        self.assertIsNotNone(wide)
        block = wide.group(2)
        self.assertIn("width: min(75vw, calc(", block)
        self.assertIn("var(--dash-crew-depths, 2)", block)
        self.assertIn("var(--dash-crew-column)", block)
        self.assertIn("var(--dash-crew-column-gap)", block)
        # The list stays on the left; the selected boards take the remainder.
        self.assertIn(".dash-dialog.has-crews > .dash-body", block)
        self.assertIn("grid-template-columns:", block)
        # Each pane scrolls on its own inside a body that does not.
        self.assertIn(".dash-dialog.has-crews .dash-crews-slot", block)
        self.assertIn("minmax(240px, 320px) minmax(0, 1fr)", block)
        self.assertIn("overflow: hidden;", css)
        self.assertIn("overflow-y: auto;", block)
        # Below that width nothing is declared for the panes: they stack in
        # the one scroller, as they did.
        self.assertIn("grid-template-rows: minmax(0, 1fr) minmax(0, 1fr)", css[:wide.start()])


    def test_the_narrow_board_reads_its_gutter_from_the_wire_layer(self):
        css = self.CSS.read_text(encoding="utf-8")
        narrow = css[css.index("@container dash-crews (max-width:"):]
        self.assertIn("padding-left: var(--dash-wire-gutter, 16px);", narrow)

    def test_no_stylesheet_draws_a_closed_pane(self):
        css = self.CSS.read_text(encoding="utf-8")
        self.assertNotIn("is-ghost", css)
        self.assertNotIn("is-closed", css)

    def test_the_board_wears_only_tokens(self):
        css = self.CSS.read_text(encoding="utf-8")
        board = css[css.index("/* The permanent list and optional crew window"):]
        self.assertNotIn("#", board.replace("/* ──", ""))
        self.assertNotIn("rgb(", board)
        self.assertNotIn("rgba(", board)
        for token in ("--gv-accent", "--gv-success", "--gv-warning", "--gv-danger", "--gv-dialog-muted"):
            with self.subTest(token=token):
                self.assertIn(f"var({token})", board)


class OverrideModeStylingTestCase(unittest.TestCase):
    """Override mode's red is a theme token, defined for every theme and read
    by both the pane header's frame and the dashboard chip -- never a literal
    in either rule. Source assertions for the stylesheet hooks only; what the
    hooks are attached to is executed above and in test_dashboard_targeting."""

    STATIC_CSS = REPO_ROOT / "web" / "static" / "css"
    TOKENS = ("--gv-mcp-override", "--gv-mcp-override-soft")

    def _css(self, name):
        return (self.STATIC_CSS / name).read_text(encoding="utf-8")

    @staticmethod
    def _block(css, opener):
        start = css.index(opener)
        return css[start: css.index("}", start)]

    def test_the_tokens_are_defined_for_every_theme(self):
        tokens = self._css("tokens.css")
        for opener in (":root {", '[data-theme="light"] {'):
            block = self._block(tokens, opener)
            for token in self.TOKENS:
                with self.subTest(theme=opener, token=token):
                    self.assertIn(f"{token}:", block)

    def test_the_header_frame_and_the_chip_read_the_tokens(self):
        frame = self._block(
            self._css("terminals.css"),
            ".terminal-agent-icon[data-mcp][data-mcp-override] {",
        )
        self.assertIn("var(--gv-mcp-override)", frame)
        frame = self._block(self._css("agent-dashboard-sidebar.css"),
                            ".agent-sidebar-list .dash-agent-icon[data-mcp][data-mcp-override] {")
        self.assertIn("outline-color: var(--gv-mcp-override);", frame)
        for rule in (frame,):
            self.assertNotIn("#", rule)
            self.assertNotIn("rgb", rule)


if __name__ == "__main__":
    unittest.main()
