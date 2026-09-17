"""The agent dashboard, docked to the workspace.

`dashboard-sidebar.js` is a markup builder, a poll and a delegated click, so it
is exercised by *running* it: the real module, the real `dashboard-dialog.js`
whose per-field renderers it is handed, and the real `agent-identity.js`,
`agent-glyphs.js` and `session-colour.js` behind them, loaded in Node against a
stub page and driven through the controller the browser wires.

What is pinned is what makes this a *panel* and not a second dialog:

- **The row draws no agent name, and does not lose it.** At a sixth of a window
  the mark already answers "which agent" — every one GridVibe draws has its own
  artwork in its own colour — so the name leaves the line and stays in the row's
  accessible name, exactly as the state word did when the dot replaced it. What
  is drawn is a dot, a mark and the chat title, in that order.
- **Every other field is the dialog's answer, not a second one.** The dot, the
  bar, the title, the hover, the workspace label and the session's hue are all
  asked for by name, so the same pane cannot read one way here and another way
  there.
- **It is chrome, so it does not dismiss itself.** The dialog goes away when the
  reader leaves the window; this stays, and it is the *poll* that stands down —
  while the panel is shut, and while the document is hidden. Both conditions,
  because "open" no longer implies "being looked at".
- **Its state is the workspace's, and durable.** Toggling writes the local cache
  and reports one ordered workspace-presentation transaction; a value that came
  *from* the server is applied without being sent straight back. It rides into
  the workspace snapshot beside `topbar_visible`, so a restored workspace comes
  back with the panel it was saved with — and a slot written before this feature
  existed restores with it shut rather than failing to restore at all.
- **A row lands where the dialog's row lands**, through the dialog's own
  resolver, and says so on its own notice line when it cannot.
- **A repaint that changes nothing is not performed**, because this panel is on
  screen while the reader works.
- **Values reach the markup escaped**, so an agent that announces markup in its
  window title cannot rewrite the column.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sessions.manager import SessionManager
from web import api
from web import runtime_state as web_runtime_state
from web.lifecycle import LifecycleValidationError, normalize_workspace_metadata
from web.session_presentation import (
    PresentationValidationError,
    normalize_workspace_presentation,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
STATIC_CSS = REPO_ROOT / "web" / "static" / "css"
TEMPLATES = REPO_ROOT / "templates"
AGENT_IDENTITY_JS = STATIC_JS / "agent-identity.js"
AGENT_GLYPHS_JS = STATIC_JS / "agent-glyphs.js"
SESSION_COLOUR_JS = STATIC_JS / "session-colour.js"
DASHBOARD_DIALOG_JS = STATIC_JS / "dashboard-dialog.js"
DASHBOARD_SIDEBAR_JS = STATIC_JS / "dashboard-sidebar.js"
DASHBOARD_CLOSE_JS = STATIC_JS / "dashboard-close.js"

NODE = shutil.which("node")


HARNESS_STUBS = r"""
/* shared.js's own escaper, copied rather than neutered: every value reaches the
   parser below through it, so a stub that did not escape would let an announced
   title containing markup rewrite the rows the assertions read. */
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

function workspaceDisplayLabel(workspace, index) {
    return String(workspace && workspace.label ? workspace.label : '')
        || (workspace && workspace.workspace_id === 'default'
            ? 'Main workspace'
            : `Workspace ${index + 1}`);
}

/* dashboard-dialog.js is loaded whole, for its renderers. Nothing below opens
   its dialog, so only the names it reaches for at *render* time are stubbed. */
const CURRENT_WORKSPACE_ID = 'default';
function normalizeWorkspaceId(value) { return String(value || 'default'); }

function fakeListeners() {
    const handlers = new Map();
    return {
        addEventListener(type, handler) {
            if (!handlers.has(type)) { handlers.set(type, new Set()); }
            handlers.get(type).add(handler);
        },
        removeEventListener(type, handler) { handlers.get(type)?.delete(handler); },
        listenerCount(type) { return handlers.get(type)?.size || 0; },
        fire(type, event) { [...(handlers.get(type) || [])].forEach(h => h(event)); }
    };
}

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

/* The scroller answers the two questions a render asks it — "is the caret in
   me" and "where is this row" — so the focus-retention path is executable
   rather than a no-op. Both default to "no", the ordinary case. */
let focusIsInside = false;
let focusTarget = null;

function fakeElement(id) {
    return {
        id,
        innerHTML: '',
        textContent: '',
        title: '',
        hidden: false,
        scrollTop: 0,
        dataset: {},
        style: { setProperty(name, value) { this[name] = value; } },
        getBoundingClientRect: () => ({ width: 300 * sidebar.getScale() / 100 }),
        setPointerCapture(id) { this.captured = id; },
        releasePointerCapture(id) { this.released = id; },
        attributes: {},
        classList: fakeClassList(),
        setAttribute(name, value) { this.attributes[name] = value; },
        getAttribute(name) { return this.attributes[name]; },
        focus() { this.focused = true; },
        contains: () => focusIsInside,
        querySelector() { return focusTarget; },
        ...fakeListeners()
    };
}

const byId = new Map();
[
    'agentSidebar',
    'agentSidebarBody',
    'agentSidebarTotals',
    'agentSidebarNotice',
    'agentSidebarRefreshBtn',
    'agentSidebarCloseBtn',
    'agentSidebarToggleBtn',
    'agentSidebarToggleIcon',
    'agentSidebarResizer',
    /* dashboard-dialog.js's own ids: the sidebar never touches them, and a
       missing one would make an unrelated dialog call throw mid-assertion. */
    'agentDashboardShell',
    'agentDashboardBody',
    'agentDashboardNotice'
].forEach(id => byId.set(id, fakeElement(id)));

const bodyClassList = fakeClassList();
let documentIsHidden = false;
const visibilityListeners = fakeListeners();

/* The page's ledger: what the controller asked the page to do, so the two
   halves of an apply (cache vs. durable transaction) can be told apart. */
const calls = {
    fetches: 0,
    stored: [],
    reports: 0,
    layouts: 0,
    targets: []
};

let storedSidebar = null;
let openTargetAnswer = true;

const timers = { armed: 0, cleared: 0 };
const aborts = { created: 0, aborted: 0 };

let fetchAnswer = null;
let fetchDelay = 0;

const windowListeners = fakeListeners();
const closeCalls = { requests: [], prompts: 0, notices: [], refreshes: 0 };
let bridge = null;
let closeDecision = 'close';
let workspaceDecision = 'close';
let workspaceCloseResult = { ok: true, step: 'close' };
let closeSaveOk = true;
let holdPrompt = null;
let holdDelete = null;
const closeActions = dashboardClose.create({
    getBridge: () => bridge,
    fetchJson: async (url, options = {}) => {
        const method = options.method || 'GET';
        closeCalls.requests.push([method, url]);
        if (method === 'GET') return { ok: true, data: { sessions: [{ status: 'connected' }] } };
        if (method === 'POST') return { ok: closeSaveOk, data: { error: 'Save failed' } };
        if (holdDelete) await holdDelete;
        return { ok: true, data: {} };
    },
    confirmCloseSession: async () => {
        closeCalls.prompts += 1;
        if (holdPrompt) await holdPrompt;
        return closeDecision;
    },
    skipDecision: () => null,
    connectedCount: sessions => sessions.length,
    decisions: { cancel: 'cancel', saveAndClose: 'save-and-close' },
    confirmCloseWorkspace: async () => { closeCalls.prompts += 1; return workspaceDecision; },
    closeWorkspaceDecided: async (id, decision) => {
        closeCalls.requests.push(['WORKSPACE', id, decision]);
        return workspaceCloseResult;
    },
    notice: message => closeCalls.notices.push(message),
    refresh: () => { closeCalls.refreshes += 1; }
});
const sidebar = GridVibeDashboardSidebar.create({
    getCloseActions: () => closeActions,
    onBridgeReady: handler => windowListeners.addEventListener('pywebviewready', handler),
    addWindowListener: (type, handler) => windowListeners.addEventListener(type, handler),
    removeWindowListener: (type, handler) => windowListeners.removeEventListener(type, handler),
    getElement: id => byId.get(id) || null,
    setBodyClass: (name, on) => bodyClassList.toggle(name, on),
    activeElement: () => focusTarget,
    onVisibilityChange: handler => visibilityListeners.addEventListener('v', handler),
    documentHidden: () => documentIsHidden,
    setInterval: () => { timers.armed += 1; return timers.armed; },
    clearInterval: () => { timers.cleared += 1; },
    setTimeout: () => 0,
    clearTimeout: () => {},
    createAbortController: () => {
        aborts.created += 1;
        return { signal: {}, abort() { aborts.aborted += 1; } };
    },
    fetchJson: async () => {
        calls.fetches += 1;
        if (fetchDelay) {
            const wait = fetchDelay;
            await new Promise(resolve => setTimeout(resolve, wait));
        }
        if (fetchAnswer === null) { throw new Error('HTTP 500'); }
        return typeof fetchAnswer === 'function' ? fetchAnswer() : fetchAnswer;
    },
    /* The real renderers, off the real dialog module: these are what makes the
       row this panel draws the same reading as the row the dialog draws. */
    render: {
        esc: escHtml,
        activity: dashboardActivityHtml,
        progress: dashboardProgressHtml,
        glyph: dashboardAgentGlyphHtml,
        glyphKey: dashboardAgentGlyphKey,
        line: dashboardPaneLine,
        hover: dashboardPaneHover,
        agentName: dashboardAgentName,
        mcp: dashboardMcpTagHtml,
        workspaceLabel: dashboardWorkspaceLabel,
        sessionColourStyle: dashboardSessionColourStyle,
        totals: dashboardTotalsText
    },
    openTarget: async target => {
        calls.targets.push(target);
        return openTargetAnswer;
    },
    readStored: () => storedSidebar,
    writeStored: open => { calls.stored.push(open); },
    report: () => { calls.reports += 1; },
    onLayoutChanged: () => { calls.layouts += 1; },
    logError: () => {}
});

function shell() { return byId.get('agentSidebar'); }
function body() { return byId.get('agentSidebarBody'); }
function notice() { return byId.get('agentSidebarNotice'); }
function totals() { return byId.get('agentSidebarTotals'); }
function toggleButton() { return byId.get('agentSidebarToggleBtn'); }
function toggleIcon() { return byId.get('agentSidebarToggleIcon'); }

function settle() { return new Promise(resolve => setTimeout(resolve, 0)); }

function camel(name) {
    return name.replace(/-([a-z])/g, (_m, letter) => letter.toUpperCase());
}

/* How many of each section the reader is looking at: the nesting is the point,
   so it is counted rather than inferred from the row list. */
function sectionCounts() {
    const count = pattern => (body().innerHTML.match(pattern) || []).length;
    return {
        workspaces: count(/<section class="dash-workspace[ "]/g),
        sessions: count(/<section class="dash-session[ "]/g),
        agents: count(/class="dash-agent"/g)
    };
}

/* One agent row, read back as the reader meets it. `drawn` is the row with its
   out-of-flow spans removed — what is actually on the line. */
function parseAgentRows() {
    const rows = [];
    const pattern = /<button\b([^>]*)class="dash-agent"([^>]*)>([\s\S]*?)<\/button>/g;
    let found;
    while ((found = pattern.exec(body().innerHTML)) !== null) {
        const attributeText = `${found[1]} ${found[2]}`;
        const attributes = {};
        const dataset = {};
        const attributePattern = /([a-zA-Z-]+)="([^"]*)"/g;
        let attribute;
        while ((attribute = attributePattern.exec(attributeText)) !== null) {
            attributes[attribute[1]] = attribute[2];
            if (attribute[1].startsWith('data-')) {
                dataset[camel(attribute[1].slice(5))] = attribute[2];
            }
        }
        const inner = found[3];
        const grab = className => {
            const match = new RegExp(
                `<span class="${className}">([\\s\\S]*?)</span>`
            ).exec(inner);
            return match ? match[1].trim() : null;
        };
        const glyph = /(?:<svg class="dash-agent-glyph"[\s\S]*?<\/svg>|<img class="dash-agent-glyph"[^>]*>)/
            .exec(inner);
        rows.push({
            dataset,
            hover: attributes['title'] || '',
            agent: attributes['data-agent'] || '',
            key: attributes['data-dashboard-key'] || '',
            /* What is *drawn*: the name lives in `dash-agent-who`, which is out
               of flow, and the state word in `dash-state-word`, which always
               was. The two column classes the dialog draws are asked about by
               name so their absence is an assertion rather than an omission. */
            name: grab('dash-agent-name'),
            who: grab('dash-agent-who'),
            line: grab('dash-agent-line'),
            state: /class="dash-activity dash-state-([a-z]+)"/.exec(inner)?.[1] || '',
            tags: [...inner.matchAll(/<span class="dash-tag[^"]*"[^>]*>([\s\S]*?)<\/span>/g)]
                .map(match => match[1].trim()),
            word: grab('dash-state-word'),
            glyph: glyph ? glyph[0] : '',
            hasBar: inner.includes('dash-progress-fill'),
            percent: /<span class="dash-progress-value">(\d+)%<\/span>/.exec(inner)
                ? Number(/<span class="dash-progress-value">(\d+)%<\/span>/.exec(inner)[1])
                : null,
            /* The column order, as the reader's eye runs along it. The row's
               own spans only -- what the indicator nests inside its column is
               the dialog's business and is asserted against the dialog. */
            columns: [...inner.matchAll(/<span class="(dash-agent-[a-z-]+)"/g)].map(m => m[1]),
            html: inner
        });
    }
    return rows;
}

function parseRows() {
    const rows = [];
    const pattern = /<button\b([^>]*)>([\s\S]*?)<\/button>/g;
    let found;
    while ((found = pattern.exec(body().innerHTML)) !== null) {
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
        const label = /<span class="dash-(?:agent-line|session-name|workspace-name)">([\s\S]*?)<\/span>/
            .exec(found[2]);
        rows.push({
            kind: attributes['data-dashboard-action'] || '',
            key: attributes['data-dashboard-key'] || '',
            dataset,
            label: label ? label[1].trim() : ''
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

/* Up, with none of the wiring: the cases about the *reading* do not want a
   cached-state apply and a fetch spent before they have set one up. */
function sidebarShown() {
    shell().classList.add('visible');
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard sidebar tests")
class DashboardSidebarNodeTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            f"const dashboardClose = require({json.dumps(str(DASHBOARD_CLOSE_JS))});\n"
            + "const window = globalThis;\n"
            + AGENT_IDENTITY_JS.read_text(encoding="utf-8")
            + AGENT_GLYPHS_JS.read_text(encoding="utf-8")
            + SESSION_COLOUR_JS.read_text(encoding="utf-8")
            + DASHBOARD_DIALOG_JS.read_text(encoding="utf-8")
            + DASHBOARD_SIDEBAR_JS.read_text(encoding="utf-8")
            + HARNESS_STUBS
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


class DashboardSidebarRowTestCase(DashboardSidebarNodeTestCase):
    def test_the_row_draws_a_dot_a_mark_and_a_title_and_no_agent_name(self):
        """The one deliberate difference from the dialog's row. The mark already
        says which agent this is — every agent GridVibe draws has its own
        artwork in its own colour — so at a sixth of a window the name stops
        being a column and the title takes the width. It is not lost: it stays
        in the row's accessible name, out of flow, the way the state word the
        dot replaced did."""
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'Fix the parser', state: 'working' })
            })])]);
            await sidebar.refresh();
            report(parseAgentRows());
            """
        )
        self.assertEqual(len(result), 1)
        row = result[0]
        # Drawn: the dot, the mark, the title.
        self.assertEqual(row["line"], "Fix the parser")
        self.assertIn("/docs/images/agent/claude-code.svg", row["glyph"])
        self.assertEqual(row["state"], "working")
        # Not drawn, and the dialog's own column class is the one that is gone.
        self.assertIsNone(row["name"])
        self.assertNotIn("dash-agent-name", row["html"])
        # Kept, out of flow, so the row still names its agent when it is heard.
        self.assertEqual(row["who"], "Claude Code")
        self.assertEqual(row["word"], "Working")
        # And the order the eye runs along: the reading first, then the mark,
        # then the name it does not draw, then the title.
        self.assertEqual(
            row["columns"][:4],
            [
                "dash-agent-reading",
                "dash-agent-icon",
                "dash-agent-who",
                "dash-agent-line",
            ],
        )

    def test_the_pane_running_with_gridvibe_tools_says_so_here_too(self):
        """The one chip this row keeps, and the reason it is not the name.

        The mark already answers which agent a row is; nothing else on it
        answers whether that agent can create workspaces, launch panes and
        split the grid — which is what a reader picking a pane to instruct is
        deciding, and picking one *while* working is what this panel is for.
        """
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([
                pane({ agent_mcp: true }),
                pane({ session_id: 's2', index: 1 }),
                pane({
                    session_id: 's3', index: 2, mode: 'wsl', use_powershell: true,
                    host: 'PowerShell', agent_mcp: true
                }),
                pane({
                    session_id: 's4', index: 3, startup_mode: 'terminal',
                    agent_selection: '', agent_mcp: true
                })
            ])]);
            await sidebar.refresh();
            report(parseAgentRows().map(row => ({
                key: row.key, tags: row.tags, columns: row.columns
            })));
            """
        )
        tags = {row["key"]: row["tags"] for row in result}
        self.assertEqual(tags["pane:s1"], ["MCP"])
        self.assertEqual(tags["pane:s2"], [])
        # A remote pane's tools ride its own transport home, so it wears the
        # chip exactly as a local one does.
        self.assertEqual(tags["pane:s3"], ["MCP"])
        # And a flag left behind on a pane that is no longer running an agent
        # paints nothing, the same rule the pane header and the dialog apply.
        self.assertEqual(tags["pane:s4"], [])
        # The chip sits after the title and before the bar, so the four columns
        # the eye runs along are still in the order they were.
        self.assertEqual(
            [row["columns"] for row in result][0][:4],
            [
                "dash-agent-reading",
                "dash-agent-icon",
                "dash-agent-who",
                "dash-agent-line",
            ],
        )

    def test_every_other_field_is_the_dialogs_own_answer(self):
        """The naming rule, the transport tag, the state word, the hue and the
        bar all come from the modules the dialog reads, asked for by name. A
        second copy of any of them here is how one pane comes to read two ways
        on two surfaces, so each is checked against the dialog's own output for
        the same pane."""
        result = self._run_node(
            """
            sidebarShown();
            const reading = pane({
                activity: activity({
                    title: 'Ship the release', state: 'working',
                    progress_state: 'normal', progress_value: 42
                })
            });
            fetchAnswer = snapshot([group([reading])]);
            await sidebar.refresh();
            const row = parseAgentRows()[0];
            report({
                row,
                dialog: {
                    line: dashboardPaneLine(reading),
                    hover: dashboardPaneHover(reading),
                    name: dashboardAgentName(reading),
                    glyphKey: dashboardAgentGlyphKey(reading),
                    activity: dashboardActivityHtml(reading),
                    progress: dashboardProgressHtml(reading),
                    mcp: dashboardMcpTagHtml(reading)
                },
                totals: totals().textContent
            });
            """
        )
        row, dialog = result["row"], result["dialog"]
        self.assertEqual(row["line"], dialog["line"])
        self.assertEqual(row["hover"], dialog["hover"])
        self.assertEqual(row["who"], dialog["name"])
        self.assertEqual(row["agent"], dialog["glyphKey"])
        self.assertIn(dialog["activity"].strip(), row["html"])
        self.assertIn(dialog["progress"].strip(), row["html"])
        self.assertEqual(row["percent"], 42)
        # The chip is the dialog's builder too, so a pane without the tools
        # draws exactly what the dialog draws for it: nothing.
        self.assertEqual(dialog["mcp"], "")
        self.assertEqual(row["tags"], [])
        # The shell the pane runs on left the line in the dialog too; it is the
        # last line of the hover on both surfaces.
        self.assertTrue(row["hover"].endswith("SSH"))
        self.assertEqual(result["totals"], "1 agent · 1 session · 1 workspace")

    def test_a_session_card_wears_its_own_tabs_colour(self):
        """The hue is `session-colour.js`'s answer keyed by group id — the same
        one the workspace tab strip paints — so a card is matched to its tab by
        colour before its name is read."""
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([pane()])]);
            await sidebar.refresh();
            const style = /<section class="dash-session" style="([^"]*)"/
                .exec(body().innerHTML);
            report({
                style: style ? style[1] : '',
                expected: window.GridVibeSessionColour.sessionColour('g1')
            });
            """
        )
        self.assertIn(
            f"--dash-session-color:{result['expected']}", result["style"]
        )
        self.assertIn("--dash-session-color-soft:", result["style"])

    def test_three_levels_and_a_session_with_no_agent_still_listed(self):
        """A workspace is a band, a session is a card, an agent is a row — the
        same nesting the dialog draws, because it is the same tree. A session
        holding no agent keeps its card and says so in one muted line, so the
        panel is still the fastest way to reach any tab."""
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([
                group([pane(), pane({ session_id: 's2', index: 1 })]),
                group([], {
                    group_id: 'g9', name: 'Notes', is_active: false,
                    pane_count: 3, agent_count: 0
                })
            ]);
            await sidebar.refresh();
            report({
                counts: sectionCounts(),
                rows: parseRows().map(row => ({ kind: row.kind, key: row.key })),
                quiet: body().innerHTML.includes('No active agents'),
                meta: [...body().innerHTML.matchAll(
                    /<span class="dash-session-meta">([^<]*)<\\/span>/g
                )].map(match => match[1])
            });
            """
        )
        self.assertEqual(
            result["counts"], {"workspaces": 1, "sessions": 2, "agents": 2}
        )
        self.assertTrue(result["quiet"])
        self.assertEqual(
            [row["kind"] for row in result["rows"]],
            ["workspace", "close-workspace", "session", "close-session",
             "pane", "pane", "session", "close-session"],
        )
        # Shorter than the dialog's wording: "· 1 other" rather than
        # "· 1 other pane", because the column has the width the title needs.
        self.assertEqual(result["meta"], ["2 agents", "3 panes"])

    def test_the_shared_close_verbs_reach_the_column(self):
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot();
            await sidebar.refresh();
            report({
                actions: [...new Set(parseRows().map(row => row.kind))],
                html: body().innerHTML
            });
            """
        )
        self.assertEqual(sorted(result["actions"]), [
            "close-session", "close-workspace", "pane", "session", "workspace"
        ])
        for present in (
            "close-session",
            "close-workspace",
            "dash-session-close",
            "dash-workspace-actions",
        ):
            self.assertIn(present, result["html"])

    def test_an_announced_title_carrying_markup_cannot_rewrite_the_column(self):
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: '<img src=x onerror=alert(1)>' })
            })], { name: '</section><script>bad()</script>' })]);
            await sidebar.refresh();
            report({
                counts: sectionCounts(),
                html: body().innerHTML,
                line: parseAgentRows()[0].line
            });
            """
        )
        self.assertEqual(result["counts"]["sessions"], 1)
        # Neither value opens a tag: both reach the markup through the page's
        # own escaper, in the line and in the hover the line is shortened into.
        self.assertNotIn("<script>", result["html"])
        self.assertNotIn("<img src=x", result["html"])
        self.assertNotIn("</section><", result["html"])
        self.assertIn("&lt;img src=x", result["line"])


class DashboardSidebarSurfaceTestCase(DashboardSidebarNodeTestCase):
    def test_the_poll_stands_down_shut_and_while_the_window_is_behind(self):
        """Two conditions, unlike the dialog's one. The dialog dismisses itself
        when the reader leaves the window, so *open* implies *being looked at*;
        this is chrome and survives that, so the hidden document has to be asked
        about separately — otherwise a workspace left in the background would
        recompose the whole tree every four seconds forever."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            const shut = sidebar.schedule();
            sidebar.apply(true);
            await settle();
            const open = { armed: timers.armed, fetches: calls.fetches };
            documentIsHidden = true;
            const hidden = sidebar.schedule();
            const afterHidden = { cleared: timers.cleared };
            /* A hidden document's tick is not merely skipped: an in-flight read
               is abandoned and a refresh asked for now answers nothing. */
            const refusedWhileShut = await (async () => {
                sidebar.apply(false);
                const before = calls.fetches;
                await sidebar.refresh();
                return calls.fetches === before;
            })();
            documentIsHidden = false;
            visibilityListeners.fire('v');
            await settle();
            report({
                shut, open, hidden, afterHidden, refusedWhileShut,
                rearmed: timers.armed
            });
            """
        )
        self.assertFalse(result["shut"])
        self.assertEqual(result["open"]["armed"], 1)
        self.assertEqual(result["open"]["fetches"], 1)
        self.assertFalse(result["hidden"])
        # The armed tick is taken away rather than left to fire and be skipped.
        self.assertGreaterEqual(result["afterHidden"]["cleared"], 1)
        self.assertTrue(result["refusedWhileShut"])
        # Coming back to the front re-arms it; the panel itself never moved.
        self.assertEqual(result["rearmed"], 1)

    def test_toggling_writes_the_cache_and_one_ordered_transaction(self):
        """The two halves of the state are separate flags, because the boot
        path and the server's own read both apply a value they have just been
        told and must not send it straight back."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            /* What the page does on boot with a cached value. */
            storedSidebar = true;
            sidebar.wire();
            await settle();
            const booted = {
                open: sidebar.isOpen(),
                stored: calls.stored.slice(),
                reports: calls.reports,
                layouts: calls.layouts
            };
            /* What the server's own read does. */
            sidebar.apply(false, { persist: true });
            const fromServer = { stored: calls.stored.slice(), reports: calls.reports };
            /* And what a press does. */
            sidebar.toggle();
            await settle();
            report({
                booted, fromServer,
                pressed: {
                    open: sidebar.isOpen(),
                    stored: calls.stored.slice(),
                    reports: calls.reports,
                    layouts: calls.layouts,
                    bodyClass: bodyClassList.contains('agent-sidebar-open')
                }
            });
            """
        )
        # Booting from the cache paints the panel and writes nothing durable.
        self.assertTrue(result["booted"]["open"])
        self.assertEqual(result["booted"]["stored"], [])
        self.assertEqual(result["booted"]["reports"], 0)
        self.assertEqual(result["booted"]["layouts"], 1)
        # The server's answer refreshes the cache and is not echoed back.
        self.assertEqual(result["fromServer"]["stored"], [False])
        self.assertEqual(result["fromServer"]["reports"], 0)
        # A press is the only thing that writes the workspace record.
        self.assertTrue(result["pressed"]["open"])
        self.assertEqual(result["pressed"]["stored"], [False, True])
        self.assertEqual(result["pressed"]["reports"], 1)
        self.assertTrue(result["pressed"]["bodyClass"])

    def test_the_one_control_states_what_the_press_will_do(self):
        """One button and two supplied marks, repainted from the state rather
        than flipped, so the icon and the panel cannot fall out of step — and so
        the first paint, which has no press to derive anything from, is right."""
        faces = self._run_node(
            """
            const read = () => ({
                icon: toggleIcon().getAttribute('src'),
                label: toggleButton().getAttribute('aria-label'),
                pressed: toggleButton().getAttribute('aria-pressed'),
                expanded: toggleButton().getAttribute('aria-expanded')
            });
            fetchAnswer = snapshot();
            sidebar.apply(false);
            const shut = read();
            sidebar.apply(true);
            await settle();
            const open = read();
            report({ shut, open, policy: GridVibeDashboardSidebar.policy.toggleFace(true) });
            """
        )
        self.assertEqual(faces["shut"]["icon"], "/docs/images/show_sidebar.ico")
        self.assertEqual(faces["shut"]["label"], "Show the agent dashboard")
        self.assertEqual(faces["shut"]["pressed"], "false")
        self.assertEqual(faces["open"]["icon"], "/docs/images/hide_sidebar.ico")
        self.assertEqual(faces["open"]["label"], "Hide the agent dashboard")
        self.assertEqual(faces["open"]["pressed"], "true")
        self.assertEqual(faces["open"]["expanded"], "true")
        self.assertEqual(faces["policy"]["icon"], "/docs/images/hide_sidebar.ico")

    def test_only_a_real_change_refits_the_panes_beside_it(self):
        """Opening the column changes how wide every pane is, so every attached
        terminal refits — which is the one expensive thing this control does.
        Applying the state it is already in must therefore cost nothing."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            sidebar.apply(true);
            sidebar.apply(true);
            const afterSame = calls.layouts;
            sidebar.apply(false);
            await settle();
            report({ afterSame, afterChange: calls.layouts });
            """
        )
        self.assertEqual(result["afterSame"], 1)
        self.assertEqual(result["afterChange"], 2)

    def test_an_unchanged_reading_is_not_repainted_and_a_changed_one_keeps_place(self):
        """This panel is on screen while the reader works, so a repaint that
        says nothing new would drop a selection they are in the middle of. A
        reading that *did* change puts the caret back on the row it was on and
        the scroller back where it was."""
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'First' })
            })])]);
            await sidebar.refresh();
            const first = body().innerHTML;
            await sidebar.refresh();
            const unchanged = body().innerHTML === first;
            focusIsInside = true;
            focusTarget = { dataset: { dashboardKey: 'pane:s1' }, focus() { this.focused = true; } };
            body().querySelector = () => focusTarget;
            body().scrollTop = 88;
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'Second' })
            })])]);
            await sidebar.refresh();
            report({
                unchanged,
                line: parseAgentRows()[0].line,
                scroll: body().scrollTop,
                refocused: Boolean(focusTarget.focused)
            });
            """
        )
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["line"], "Second")
        self.assertEqual(result["scroll"], 88)
        self.assertTrue(result["refocused"])

    def test_a_failed_read_keeps_the_last_tree_behind_a_stated_notice(self):
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'Still here' })
            })])]);
            await sidebar.refresh();
            fetchAnswer = null;
            await sidebar.refresh();
            const failed = {
                notice: notice().textContent,
                hidden: notice().hidden,
                line: parseAgentRows()[0].line
            };
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'Still here' })
            })])]);
            await sidebar.refresh();
            report({ failed, recovered: notice().textContent });
            """
        )
        self.assertIn("Showing the last reading", result["failed"]["notice"])
        self.assertFalse(result["failed"]["hidden"])
        self.assertEqual(result["failed"]["line"], "Still here")
        self.assertEqual(result["recovered"], "")

    def test_a_slow_answer_never_repaints_over_a_newer_one(self):
        result = self._run_node(
            """
            sidebarShown();
            fetchDelay = 20;
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'Old' })
            })])]);
            const slow = sidebar.refresh();
            fetchDelay = 0;
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'New' })
            })])]);
            await sidebar.refresh();
            const afterNew = parseAgentRows()[0].line;
            await slow;
            report({ afterNew, afterSlow: parseAgentRows()[0].line });
            """
        )
        self.assertEqual(result["afterNew"], "New")
        self.assertEqual(result["afterSlow"], "New")

    def test_a_row_lands_through_the_dialogs_own_resolver_and_reports_a_refusal(self):
        """Where a dashboard row goes is one answer, in `dashboard-dialog.js`:
        it is the half that knows a row naming *this* window has to be applied
        directly, because raising an already-raised window fires no `focus`
        event for the stored target to be claimed on. What this panel owns is
        saying so on its own line when the row could not be reached — the
        dialog reports into a notice nobody can see from here."""
        result = self._run_node(
            """
            sidebar.wire();
            sidebar.apply(true);
            fetchAnswer = snapshot([group([pane({ session_id: 's7', group_id: 'g3' })], {
                group_id: 'g3'
            })]);
            await sidebar.refresh();
            clickRow('pane:s7');
            await settle();
            const landed = { targets: calls.targets.slice(), notice: notice().textContent };
            openTargetAnswer = false;
            clickRow('workspace:default');
            await settle();
            report({ landed, refused: { targets: calls.targets.slice(), notice: notice().textContent } });
            """
        )
        self.assertEqual(
            result["landed"]["targets"],
            [{"workspaceId": "default", "groupId": "g3", "sessionId": "s7"}],
        )
        self.assertEqual(result["landed"]["notice"], "")
        self.assertEqual(
            result["refused"]["targets"][-1],
            {"workspaceId": "default", "groupId": "", "sessionId": ""},
        )
        self.assertIn("Could not open", result["refused"]["notice"])

    def test_the_panel_is_not_a_dialog_and_claims_nothing(self):
        """It is chrome: it does not dismiss itself on `blur`, it publishes no
        cross-window claim, and it never touches the dialog's shell — so having
        both up at once is two surfaces, not two halves of one."""
        source = DASHBOARD_SIDEBAR_JS.read_text(encoding="utf-8")
        for absent in (
            "modal-shell",
            "BroadcastChannel",
            "'blur'",
            "Escape",
            "agentDashboardShell",
            "closeAgentDashboardDialog",
        ):
            self.assertNotIn(absent, source)


PAGE_WIRING_STUBS = r"""
/* The page, as `dashboard-sidebar.js` finds it when it loads itself. Loaded
   *before* the module, because the browser half of the UMD wrapper wires a
   controller the moment `root.document` exists. */
const byId = new Map();
function element(id) {
    const names = new Set();
    return {
        id,
        innerHTML: '', textContent: '', title: '', hidden: false, scrollTop: 0,
        dataset: {}, attributes: {},
        classList: {
            add: name => names.add(name),
            remove: name => names.delete(name),
            contains: name => names.has(name),
            toggle: (name, force) => {
                const on = force === undefined ? !names.has(name) : Boolean(force);
                if (on) { names.add(name); } else { names.delete(name); }
                return on;
            }
        },
        setAttribute(name, value) { this.attributes[name] = value; },
        getAttribute(name) { return this.attributes[name]; },
        addEventListener() {},
        focus() {},
        contains: () => false,
        querySelector: () => null
    };
}
[
    'agentSidebar', 'agentSidebarBody', 'agentSidebarTotals', 'agentSidebarNotice',
    'agentSidebarRefreshBtn', 'agentSidebarCloseBtn',
    'agentSidebarToggleBtn', 'agentSidebarToggleIcon'
].forEach(id => byId.set(id, element(id)));

const document = {
    body: element('body'),
    hidden: false,
    activeElement: null,
    getElementById: id => byId.get(id) || null,
    addEventListener() {}
};
const window = globalThis;
window.document = document;
window.fetch = async () => ({
    ok: true,
    json: async () => ({ workspaces: [], totals: { workspaces: 0, sessions: 0, agents: 0 } })
});

/* The panel's poll, counted rather than run: the module takes `setInterval`
   off the page at load, and a real four-second tick would keep this process
   alive long after the assertions have finished. */
const ticks = { armed: 0, cleared: 0 };
window.setInterval = () => { ticks.armed += 1; return ticks.armed; };
window.clearInterval = () => { ticks.cleared += 1; };

/* The page's own constant. A top-level `const` in a classic script is a
   *lexical* global and deliberately not a property of `window`, which is the
   whole point of this case: a module that reached for it through `window`
   would read `undefined` and file every workspace's panel under one key. */
const CURRENT_WORKSPACE_ID = 'ws-7';

/* shared.js's two cache helpers and terminals.js's transaction hook, as names
   on `window` -- which is what a top-level `function` declaration in a classic
   script actually is. */
const ledger = { reads: [], writes: [], reports: 0, refits: 0 };
window.getStoredWorkspaceAgentSidebarOpen = id => { ledger.reads.push(id); return null; };
window.storeWorkspaceAgentSidebarOpen = (id, open) => { ledger.writes.push([id, open]); };
window.noteWorkspacePresentationChanged = () => { ledger.reports += 1; };
window.refitAttachedTerminalsForSurfaceMode = () => { ledger.refits += 1; };
window.escHtml = value => String(value === null || value === undefined ? '' : value);
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard sidebar tests")
class DashboardSidebarPageWiringTestCase(unittest.TestCase):
    """The half the module wires for itself when it loads on a real page.

    Every other case here drives `create(runtime)` directly, which is exactly
    the seam that cannot see a mistake in how the browser half reaches the
    page's own names. This one loads the module the way a `<script>` tag does
    and reads back what it asked the page for."""

    def _run_node(self, body: str):
        script = (
            PAGE_WIRING_STUBS
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

    def test_the_cache_is_keyed_by_the_workspace_the_page_says_it_is(self):
        """The state is per workspace, so the key has to be. The page states its
        id as a lexical `const`, so it is reachable by name and not through
        `window` — read the wrong way, every workspace would share one key and
        opening the panel in one would open it in all of them."""
        result = self._run_node(
            """
            wireAgentDashboardSidebar();
            toggleAgentDashboardSidebar();
            await new Promise(resolve => setTimeout(resolve, 0));
            process.stdout.write(JSON.stringify({
                reads: ledger.reads,
                writes: ledger.writes,
                reports: ledger.reports,
                refits: ledger.refits,
                armed: ticks.armed,
                open: agentDashboardSidebarOpen()
            }));
            """
        )
        self.assertEqual(result["reads"], ["ws-7"])
        self.assertEqual(result["writes"], [["ws-7", True]])
        self.assertEqual(result["reports"], 1)
        self.assertEqual(result["refits"], 1)
        self.assertTrue(result["open"])
        self.assertEqual(result["armed"], 1)

    def test_the_names_the_page_exposes_are_the_ones_the_page_calls(self):
        """The button's `onclick`, the boot call and the read-back in
        `loadSessionGroups` are three names in `templates/terminals.html` and
        `terminals.js`; they are asserted here as *callable*, because a rename
        on either side is silent until a reader presses the control."""
        result = self._run_node(
            """
            const named = [
                'wireAgentDashboardSidebar',
                'toggleAgentDashboardSidebar',
                'applyAgentDashboardSidebar',
                'agentDashboardSidebarOpen',
                'refreshAgentDashboardSidebar',
                /* The side is a fourth name the page calls — at boot, off its
                   own constant, and again on every app-config delivery. */
                'applyAgentDashboardSidebarSide',
                'agentDashboardSidebarSide'
            ];
            wireAgentDashboardSidebar();
            applyAgentDashboardSidebar(true, { persist: false });
            applyAgentDashboardSidebarSide('right');
            process.stdout.write(JSON.stringify({
                callable: named.filter(name => typeof window[name] === 'function'),
                applied: agentDashboardSidebarOpen(),
                side: agentDashboardSidebarSide(),
                /* An apply that was told the value writes nothing durable, and
                   neither does a side that came from the same server. */
                writes: ledger.writes,
                reports: ledger.reports
            }));
            """
        )
        self.assertEqual(len(result["callable"]), 7)
        self.assertTrue(result["applied"])
        self.assertEqual(result["side"], "right")
        self.assertEqual(result["writes"], [])
        self.assertEqual(result["reports"], 0)


class DashboardSidebarActionsAndResizeTestCase(DashboardSidebarNodeTestCase):
    def test_workspace_payload_carries_only_a_stated_integer_scale(self):
        persistence = json.dumps(str(STATIC_JS / "session-persistence.js"))
        result = self._run_node(f"""
            const {{ buildWorkspacePresentationPayload: build }} = require({persistence});
            const descriptor = {{ workspaceId: 'default', revision: 2, topbarVisible: true,
                mdPreset: 'default', mdFont: 'system', sourceFont: 'default' }};
            report({{
                older: build(descriptor),
                scaled: build({{ ...descriptor, agentSidebarScale: 175 }}),
                fraction: build({{ ...descriptor, agentSidebarScale: 175.5 }})
            }});
        """)
        self.assertNotIn("agent_sidebar_scale", result["older"])
        self.assertNotIn("agent_sidebar_scale", result["fraction"])
        self.assertEqual(result["scaled"]["agent_sidebar_scale"], 175)
        self.assertNotIn("agent_sidebar_open", result["scaled"])

    def test_close_reports_here_survives_polls_and_refreshes_here(self):
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            sidebarShown();
            const target = { dashboardAction: 'close-session', workspaceId: 'default',
                groupId: 'g1', sessionName: 'Work' };
            closeDecision = 'save-and-close';
            closeSaveOk = false;
            const failed = await sidebar.handleRow(target);
            await sidebar.refresh();
            const failure = notice().textContent;
            const failedRequests = closeCalls.requests.slice();
            closeSaveOk = true;
            const reads = calls.fetches;
            const closed = await sidebar.handleRow(target);
            report({ failed, failure, failedRequests, closed,
                requests: closeCalls.requests, reads: calls.fetches - reads,
                defaults: closeCalls, open: sidebar.isOpen(), targets: calls.targets });
        """)
        self.assertFalse(result["failed"])
        self.assertEqual(result["failure"], "Save failed")
        self.assertEqual([r[0] for r in result["failedRequests"]], ["GET", "POST"])
        self.assertTrue(result["closed"])
        self.assertEqual([r[0] for r in result["requests"]],
                         ["GET", "POST", "GET", "POST", "DELETE"])
        self.assertEqual(result["reads"], 1)
        self.assertEqual(result["defaults"]["notices"], [])
        self.assertEqual(result["defaults"]["refreshes"], 0)
        self.assertTrue(result["open"])
        self.assertEqual(result["targets"], [])

    def test_one_shared_guard_covers_the_prompt_and_repainted_buttons(self):
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            sidebarShown();
            let releasePrompt, releaseDelete;
            holdPrompt = new Promise(resolve => { releasePrompt = resolve; });
            holdDelete = new Promise(resolve => { releaseDelete = resolve; });
            const dataset = { dashboardAction: 'close-session', workspaceId: 'default', groupId: 'g1' };
            const pressed = fakeElement('old-row');
            const first = sidebar.handleRow(dataset, pressed);
            const target = { workspaceId: 'default', groupId: 'g1' };
            const immediate = await closeActions.run('close-session', target, fakeElement('dialog-row'));
            await settle();
            releasePrompt();
            await settle();
            await sidebar.refresh();
            const second = await sidebar.handleRow(dataset, fakeElement('new-row'));
            releaseDelete();
            const completed = await first;
            report({ immediate, second, completed, prompts: closeCalls.prompts,
                requests: closeCalls.requests, busy: pressed.classList.contains('is-busy') });
        """)
        self.assertFalse(result["immediate"])
        self.assertFalse(result["second"])
        self.assertTrue(result["completed"])
        self.assertEqual(result["prompts"], 1)
        self.assertEqual([r[0] for r in result["requests"]], ["GET", "DELETE"])
        self.assertFalse(result["busy"])

    def test_window_verb_arrives_with_the_bridge_and_closes_without_navigation(self):
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            sidebarShown();
            await sidebar.refresh();
            const before = parseRows().map(row => row.kind);
            const windows = [];
            bridge = { close_workspace_window: async id => { windows.push(id); return { ok: true }; } };
            windowListeners.fire('pywebviewready', {});
            await settle();
            const after = parseRows().map(row => row.kind);
            const row = { dataset: { dashboardAction: 'close-workspace-window', workspaceId: 'ws-2',
                workspaceLabel: 'Docs' }, classList: fakeClassList() };
            body().fire('click', { target: { closest: () => row }, preventDefault() {} });
            await settle();
            const windowNotice = notice().textContent;
            const reads = calls.fetches;
            const closed = await sidebar.handleRow({ dashboardAction: 'close-workspace',
                workspaceId: 'ws-2', groupCount: '2', workspaceLabel: 'Docs' });
            report({ before, after, windows, windowNotice, closed, reads: calls.fetches - reads,
                open: sidebar.isOpen(), targets: calls.targets, defaults: closeCalls });
        """)
        self.assertNotIn("close-workspace-window", result["before"])
        self.assertIn("close-workspace-window", result["after"])
        self.assertEqual(result["windows"], ["ws-2"])
        self.assertIn("Its sessions keep running", result["windowNotice"])
        self.assertTrue(result["closed"])
        self.assertEqual(result["reads"], 1)
        self.assertTrue(result["open"])
        self.assertEqual(result["targets"], [])
        self.assertEqual(result["defaults"]["notices"], [])

    def test_drag_measures_the_base_clamps_and_reports_once_on_release(self):
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            sidebar.apply(true, { scale: 150 });
            const layouts = calls.layouts;
            const handle = byId.get('agentSidebarResizer');
            const event = x => ({ button: 0, pointerId: 7, clientX: x, preventDefault() {} });
            handle.fire('pointerdown', event(450));
            windowListeners.fire('pointermove', event(525));
            const halfway = sidebar.getScale();
            windowListeners.fire('pointermove', event(9999));
            const maximum = sidebar.getScale();
            windowListeners.fire('pointermove', event(-9999));
            const minimum = sidebar.getScale();
            windowListeners.fire('pointermove', event(525));
            const during = { layouts: calls.layouts - layouts, reports: calls.reports,
                css: shell().style['--agent-sidebar-scale'] };
            windowListeners.fire('pointerup', event(525));
            const committed = { layouts: calls.layouts - layouts, reports: calls.reports,
                scale: sidebar.getScale(), capture: handle.captured, release: handle.released,
                listeners: ['pointermove', 'pointerup', 'pointercancel'].map(t => windowListeners.listenerCount(t)) };
            // A different viewport changes the measured base, not the stored scale.
            shell().getBoundingClientRect = () => ({ width: 400 * sidebar.getScale() / 100 });
            handle.fire('pointerdown', event(700));
            windowListeners.fire('pointerup', event(800));
            report({ halfway, maximum, minimum, during, committed, resized: sidebar.getScale() });
        """)
        self.assertEqual(result["halfway"], 175)
        self.assertEqual(result["maximum"], 200)
        self.assertEqual(result["minimum"], 100)
        self.assertEqual(result["during"], {"layouts": 0, "reports": 0, "css": "1.75"})
        self.assertEqual(result["committed"], {"layouts": 1, "reports": 1, "scale": 175,
                                             "capture": 7, "release": 7, "listeners": [0, 0, 0]})
        self.assertEqual(result["resized"], 200)

    def test_cancel_restores_width_and_apply_refits_only_a_changed_width(self):
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            sidebar.apply(true, { scale: 125 });
            const layouts = calls.layouts;
            sidebar.apply(true, { scale: 175 });
            sidebar.apply(true, { scale: 175 });
            sidebar.apply(true);
            const applied = { scale: sidebar.getScale(), layouts: calls.layouts - layouts };
            const handle = byId.get('agentSidebarResizer');
            const event = { button: 0, pointerId: 1, clientX: 0, preventDefault() {} };
            handle.fire('pointerdown', event);
            windowListeners.fire('pointermove', { pointerId: 2, clientX: 900 });
            const unrelated = sidebar.getScale();
            windowListeners.fire('pointermove', { pointerId: 1, clientX: 75 });
            windowListeners.fire('pointercancel', { pointerId: 1 });
            report({ applied, unrelated, scale: sidebar.getScale(), reports: calls.reports,
                css: shell().style['--agent-sidebar-scale'],
                listeners: windowListeners.listenerCount('pointermove') });
        """)
        self.assertEqual(result["applied"], {"scale": 175, "layouts": 1})
        self.assertEqual(result["unrelated"], 175)
        self.assertEqual(result["scale"], 175)
        self.assertEqual(result["css"], "1.75")
        self.assertEqual(result["reports"], 0)
        self.assertEqual(result["listeners"], 0)


class DashboardSidebarSideTestCase(DashboardSidebarNodeTestCase):
    """Which edge the column lives on.

    A *global* App Setting (`workspace.agent_sidebar_side`) and not the
    workspace's own, so it follows the surface mode's rules rather than the
    panel's: every window reads the current value live, a save reaches them all
    at once, and no workspace snapshot ever carries it. The panel itself is
    unchanged by the swap — same markup, same one toggle wearing the same two
    marks, same open/shut state and the same width — so what is pinned here is
    the one class that moves it and the one gesture that has to read the other
    way round.
    """

    def test_the_side_is_one_body_class_and_anything_unstated_is_the_left_one(self):
        result = self._run_node("""
            const read = () => ({
                side: sidebar.getSide(),
                right: bodyClassList.contains('agent-sidebar-right')
            });
            const start = read();
            sidebar.setSide('right');
            const right = read();
            sidebar.setSide('left');
            const left = read();
            // Whatever the page hands over, the column is on a real edge.
            const odd = [null, undefined, '', 'top', 0, 'RIGHT', ' right '].map(value => {
                sidebar.setSide('left');
                return sidebar.setSide(value);
            });
            report({ start, right, left, odd });
        """)
        self.assertEqual(result["start"], {"side": "left", "right": False})
        self.assertEqual(result["right"], {"side": "right", "right": True})
        self.assertEqual(result["left"], {"side": "left", "right": False})
        self.assertEqual(
            result["odd"],
            ["left", "left", "left", "left", "left", "right", "right"],
        )

    def test_swapping_sides_changes_nothing_the_workspace_owns(self):
        """The two facts that *are* the workspace's — up or down, and how wide —
        ride their own transaction, so moving the column must not touch either,
        must not write the local cache, and must not report a presentation
        change. The one toggle keeps the mark it was wearing."""
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            sidebar.apply(true, { persist: true, report: true, scale: 150 });
            const read = () => ({
                open: sidebar.isOpen(), scale: sidebar.getScale(),
                icon: toggleIcon().getAttribute('src'),
                pressed: toggleButton().getAttribute('aria-pressed'),
                label: toggleButton().getAttribute('aria-label'),
                stored: calls.stored.slice(), reports: calls.reports
            });
            const before = read();
            sidebar.setSide('right');
            const after = read();
            report({ before, after });
        """)
        self.assertEqual(result["before"], result["after"])
        self.assertTrue(result["after"]["open"])
        self.assertEqual(result["after"]["scale"], 150)
        self.assertIn("hide_sidebar.ico", result["after"]["icon"])

    def test_only_a_real_move_of_an_open_column_refits_the_panes_beside_it(self):
        """The expensive thing this control does. A shut column occupies no
        width on either edge, and a side it is already on is not a move."""
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            const shut = calls.layouts;
            sidebar.setSide('right');
            const whileShut = calls.layouts - shut;
            sidebar.apply(true);
            const open = calls.layouts;
            sidebar.setSide('left');
            const moved = calls.layouts - open;
            sidebar.setSide('left');
            sidebar.setSide('nonsense');
            const again = calls.layouts - open;
            report({ whileShut, moved, again });
        """)
        self.assertEqual(result["whileShut"], 0)
        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["again"], 1)

    def test_the_handle_always_widens_away_from_the_grid(self):
        """The handle is on the column's inner edge either way, so on the right
        it is the panel's *left* end: the same gesture — dragging away from the
        grid — has to widen it on both sides, which is the one thing about the
        drag that is not symmetric."""
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            const handle = byId.get('agentSidebarResizer');
            const event = x => ({ button: 0, pointerId: 3, clientX: x, preventDefault() {} });
            const drag = (side, from, to) => {
                sidebar.setSide(side);
                sidebar.apply(true, { scale: 150 });
                handle.fire('pointerdown', event(from));
                windowListeners.fire('pointermove', event(to));
                const during = sidebar.getScale();
                windowListeners.fire('pointerup', event(to));
                return { during, committed: sidebar.getScale() };
            };
            report({
                leftOut: drag('left', 450, 525),
                leftIn: drag('left', 450, 375),
                rightOut: drag('right', 450, 375),
                rightIn: drag('right', 450, 525)
            });
        """)
        # 75px against a 400px base is a quarter of the clamp, either way round.
        self.assertEqual(result["leftOut"], {"during": 175, "committed": 175})
        self.assertEqual(result["rightOut"], {"during": 175, "committed": 175})
        self.assertEqual(result["leftIn"], {"during": 125, "committed": 125})
        self.assertEqual(result["rightIn"], {"during": 125, "committed": 125})

    def test_a_side_change_under_the_pointer_abandons_the_drag(self):
        """The handle the gesture started on is not on that edge any more, so
        the drag is given up rather than finished against the other one — and an
        abandoned drag restores the width and reports nothing, exactly as a
        cancelled one does."""
        result = self._run_node("""
            fetchAnswer = snapshot();
            sidebar.wire();
            sidebar.apply(true, { scale: 150 });
            const handle = byId.get('agentSidebarResizer');
            const event = x => ({ button: 0, pointerId: 4, clientX: x, preventDefault() {} });
            handle.fire('pointerdown', event(450));
            windowListeners.fire('pointermove', event(525));
            const mid = sidebar.getScale();
            sidebar.setSide('right');
            const abandoned = { scale: sidebar.getScale(), reports: calls.reports,
                listeners: windowListeners.listenerCount('pointermove'),
                css: shell().style['--agent-sidebar-scale'] };
            // The pointer is still down as far as the window knows; nothing it
            // says afterwards may move a column that is no longer being dragged.
            windowListeners.fire('pointermove', event(700));
            windowListeners.fire('pointerup', event(700));
            report({ mid, abandoned, after: sidebar.getScale(), reports: calls.reports });
        """)
        self.assertEqual(result["mid"], 175)
        self.assertEqual(
            result["abandoned"],
            {"scale": 150, "reports": 0, "listeners": 0, "css": "1.5"},
        )
        self.assertEqual(result["after"], 150)
        self.assertEqual(result["reports"], 0)


class DashboardSidebarSideSettingTestCase(unittest.TestCase):
    """The setting behind the side: global, read live, and never a workspace's.

    It is saved from App Settings the way the surface mode is, so the same three
    guarantees are asserted here — the route normalizes and persists it, every
    open window is told at once, and a window that missed the telling reconciles
    off its next session read — plus the one that keeps the workspace save and
    restore rules exactly as they were: no durable workspace record carries it.
    """

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        config = api.load_config()
        saved_workspace = json.loads(json.dumps(config.get("workspace", {})))
        self.addCleanup(self._restore_workspace_config, saved_workspace)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore_workspace_config(self, saved_workspace):
        config = api.load_config()
        config["workspace"] = saved_workspace
        api.save_config(config)
        api._refresh_runtime_config()

    def _save_side(self, side):
        with patch.object(api.socketio, "emit") as emit:
            response = self.client.post(
                "/api/app-config", json={"workspace": {"agent_sidebar_side": side}}
            )
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json(), emit

    def _launch(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Files",
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["group_id"]

    def test_the_setting_is_two_values_and_an_unknown_one_keeps_what_is_set(self):
        """Normalized at the boundary, so a hand-edited config.json and a save
        from the dialog land on the same value."""
        payload, _ = self._save_side("right")
        self.assertEqual(payload["workspace"]["agent_sidebar_side"], "right")
        self.assertEqual(api.load_config()["workspace"]["agent_sidebar_side"], "right")
        self.assertEqual(api.runtime_config.agent_sidebar_side, "right")

        for rubbish in ("sideways", "", None, 3, ["right"], {"side": "left"}):
            with self.subTest(rubbish=rubbish):
                payload, _ = self._save_side(rubbish)
                self.assertEqual(payload["workspace"]["agent_sidebar_side"], "right")
                self.assertEqual(api.runtime_config.agent_sidebar_side, "right")

        payload, _ = self._save_side("left")
        self.assertEqual(payload["workspace"]["agent_sidebar_side"], "left")
        self.assertEqual(api.runtime_config.agent_sidebar_side, "left")

    def test_a_save_reaches_every_open_window_and_the_page_it_next_serves(self):
        """Applied instantly: the broadcast carries it beside the surface mode,
        and a window opened afterwards is served the same value as its own boot
        constant rather than a default it would have to correct."""
        _, emit = self._save_side("right")

        emit.assert_called_once()
        event, message = emit.call_args[0]
        self.assertEqual(event, "app_config_updated")
        self.assertEqual(message["workspace"]["agent_sidebar_side"], "right")

        page = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn('const AGENT_SIDEBAR_SIDE = "right";', page)

        terminals = self.client.get("/static/js/terminals.js").get_data(as_text=True)
        self.assertIn("applyAppConfigAgentSidebarSide(message);", terminals)
        self.assertIn("applyAgentDashboardSidebarSide(side);", terminals)

        self.assertEqual(
            self.client.get("/api/app-config").get_json()["workspace"][
                "agent_sidebar_side"
            ],
            "right",
        )

    def test_a_window_that_missed_the_broadcast_reconciles_on_its_next_read(self):
        """The same rule the surface mode follows: every session read reports
        the *current* global value, so a hidden window or a dropped socket costs
        a refresh rather than a reload."""
        group_id = self._launch()
        self._save_side("right")

        for url in (
            "/api/sessions",
            f"/api/sessions?group={group_id}",
            "/api/sessions?workspace_id=default",
        ):
            with self.subTest(url=url):
                payload = self.client.get(url).get_json()
                self.assertEqual(payload["agent_sidebar_side"], "right")

        self._save_side("left")
        self.assertEqual(
            self.client.get(f"/api/sessions?group={group_id}").get_json()[
                "agent_sidebar_side"
            ],
            "left",
        )

    def test_no_workspace_record_carries_the_side(self):
        """The save/restore rules are untouched: which edge the column is on is
        not a fact about a workspace, so the presentation transaction, the
        runtime-state slot and the restore that reads it back all stay exactly
        the shape they were — a workspace saved on one side comes back with the
        panel it had, on whichever edge the setting says now."""
        group_id = self._launch()
        self._save_side("right")

        # The transaction does not merely drop it — it refuses the request, so
        # a client that thought the side was the workspace's is told so.
        refused = self.client.post(
            "/api/workspace-presentation",
            json={
                "workspace_id": "default",
                "expected_revision": 0,
                "topbar_visible": True,
                "agent_sidebar_open": True,
                "agent_sidebar_side": "left",
            },
        )
        self.assertEqual(refused.status_code, 400, refused.get_json())
        self.assertIn("agent_sidebar_side", refused.get_json()["error"])

        accepted = self.client.post(
            "/api/workspace-presentation",
            json={
                "workspace_id": "default",
                "expected_revision": 0,
                "topbar_visible": True,
                "agent_sidebar_open": True,
            },
        )
        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        self.assertNotIn("agent_sidebar_side", accepted.get_json())

        saved = self.client.post(
            "/api/runtime-state/save",
            json={
                "workspace_id": "default",
                "active_group_id": group_id,
                "agent_sidebar_open": True,
                "agent_sidebar_scale": 175,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        stored = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertNotIn("agent_sidebar_side", json.dumps(stored["workspaces"]))

        # The setting moves; what the workspace saved does not.
        self._save_side("left")
        api.session_manager.reset_sessions()
        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        workspace = restored.get_json()["workspaces"][0]
        self.assertTrue(workspace["restored"], workspace)
        self.assertTrue(workspace["agent_sidebar_open"])
        self.assertEqual(workspace["agent_sidebar_scale"], 175)
        self.assertNotIn("agent_sidebar_side", workspace)
        groups = self.client.get("/api/session-groups").get_json()
        self.assertTrue(groups["agent_sidebar_open"])
        self.assertNotIn("agent_sidebar_side", groups)

    def test_the_dialog_offers_the_two_sides_and_carries_the_choice(self):
        """One select beside the surface mode, and the same delivery contract:
        the collected form states it and the broadcast every open window reads
        carries it."""
        page = self.client.get("/terminals").get_data(as_text=True)
        self.assertIn('id="appAgentSidebarSide"', page)
        self.assertIn('<option value="left">Left side</option>', page)
        self.assertIn('<option value="right">Right side</option>', page)

        app_settings = self.client.get("/static/js/app-settings.js").get_data(
            as_text=True
        )
        collect = app_settings[
            app_settings.index("function collectWorkspaceSettingsForm()"):
            app_settings.index("function syncAutosaveIntervalLabel()")
        ]
        self.assertIn("appAgentSidebarSide", collect)
        notify = app_settings[
            app_settings.index("function notifyAppConfigUpdated(appSettings"):
            app_settings.index("async function loadAppSettings()")
        ]
        self.assertIn("agent_sidebar_side", notify)

    def test_the_stylesheet_moves_the_column_and_flips_only_its_two_edges(self):
        """One class, and nothing else: the panel is ordered past the grid
        rather than the row being reversed (the empty state shares that row),
        and the frame's border and the handle swap ends with it. The markup is
        the same either way, which is why the page still serves the panel before
        the grid."""
        css = self.client.get(
            "/static/css/agent-dashboard-sidebar.css"
        ).get_data(as_text=True)
        panel = re.search(
            r"body\.agent-sidebar-right \.agent-sidebar \{([^}]*)\}", css
        )
        handle = re.search(
            r"body\.agent-sidebar-right \.agent-sidebar-resizer \{([^}]*)\}", css
        )
        self.assertIsNotNone(panel)
        self.assertIsNotNone(handle)
        self.assertIn("order: 1", panel.group(1))
        self.assertIn("border-right: 0", panel.group(1))
        self.assertIn("border-left: 1px solid", panel.group(1))
        self.assertNotIn("flex-direction: row-reverse", css)
        self.assertIn("inset: 0 auto 0 0", handle.group(1))
        self.assertIn("border-right: 1px solid", handle.group(1))

        page = self.client.get("/terminals").get_data(as_text=True)
        self.assertLess(page.index('id="agentSidebar"'), page.index('id="terminalsGrid"'))
        # The handle keeps its one box and its two marks on either side.
        self.assertIn('src="/docs/images/show_sidebar.ico"', page)
        self.assertEqual(page.count('id="agentSidebarToggleBtn"'), 1)


class DashboardSidebarStateTestCase(unittest.TestCase):
    """The per-workspace half: one boolean that survives everything the
    workspace does — an ordered transaction, a manual save, a restart and a
    restore — beside the top bar's own."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _launch(self):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "session_name": "Files",
                "sessions": [
                    {
                        "directory": str(self.repo_dir),
                        "title": "Files",
                        "startup_mode": "explorer",
                    }
                ],
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["group_id"]

    def test_scale_is_optional_bounded_and_an_integer_at_each_live_boundary(self):
        self._launch()
        payload = {"workspace_id": "default", "expected_revision": 0, "topbar_visible": True}
        self.assertNotIn("agent_sidebar_scale", normalize_workspace_presentation(payload))
        for invalid in (None, True, False, 1.5, 150.0, "150", [], {}, 99, 201):
            with self.subTest(invalid=invalid):
                stated = {**payload, "agent_sidebar_scale": invalid}
                with self.assertRaises(PresentationValidationError):
                    normalize_workspace_presentation(stated)
                self.assertEqual(self.client.post("/api/workspace-presentation", json=stated).status_code, 400)
                self.assertEqual(self.client.post("/api/runtime-state/save", json={
                    "workspace_id": "default", "topbar_visible": False,
                    "agent_sidebar_scale": invalid,
                }).status_code, 400)
                with self.assertRaises(LifecycleValidationError):
                    normalize_workspace_metadata(
                        {"default": [{"agent_sidebar_scale": invalid}]},
                        {"default": {"groups": [{"group_id": "g1"}]}},
                    )
        self.assertTrue(api.session_manager.get_topbar_visible("default"))
        self.assertEqual(api.session_manager.get_agent_sidebar_scale("default"), 100)
        for value in (100, 150, 200):
            self.assertEqual(normalize_workspace_presentation({
                **payload, "agent_sidebar_scale": value,
            })["agent_sidebar_scale"], value)

    def test_scale_transaction_and_older_save_preserve_unstated_dimensions(self):
        self._launch()
        accepted = self.client.post("/api/workspace-presentation", json={
            "workspace_id": "default", "expected_revision": 0,
            "topbar_visible": True, "agent_sidebar_scale": 180,
        })
        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        self.assertEqual(accepted.get_json()["agent_sidebar_scale"], 180)
        stale = self.client.post("/api/workspace-presentation", json={
            "workspace_id": "default", "expected_revision": 0,
            "topbar_visible": True, "agent_sidebar_scale": 120,
        })
        self.assertEqual(stale.status_code, 409)
        silent = self.client.post("/api/workspace-presentation", json={
            "workspace_id": "default", "expected_revision": 1,
            "topbar_visible": False,
        })
        self.assertEqual(silent.get_json()["agent_sidebar_scale"], 180)
        saved = self.client.post("/api/runtime-state/save", json={"workspace_id": "default"})
        self.assertEqual(saved.get_json()["agent_sidebar_scale"], 180)
        self.assertEqual(self.client.get("/api/session-groups").get_json()["agent_sidebar_scale"], 180)
        self.assertEqual(api.session_manager.get_workspace("default").to_dict()["agent_sidebar_scale"], 180)

    def test_autosave_and_exit_capture_scale_with_newest_window_metadata(self):
        self._launch()
        manager = api.session_manager
        manager.set_agent_sidebar_scale("default", 125)
        # Setter is idempotent and each workspace owns its scale.
        revision = manager.get_workspace_presentation("default")["presentation_revision"]
        manager.set_agent_sidebar_scale("default", 125)
        self.assertEqual(manager.get_workspace_presentation("default")["presentation_revision"], revision)
        self.assertEqual(manager.get_agent_sidebar_scale("absent000000"), 100)
        web_runtime_state.capture_live_workspaces(manager)
        self.assertEqual(web_runtime_state.load_restorable_workspace("default")["agent_sidebar_scale"], 125)
        metadata = normalize_workspace_metadata({"default": [
            {"agent_sidebar_scale": 140}, {"agent_sidebar_scale": 190},
            {"topbar_visible": False},
        ]}, manager.snapshot_live_workspaces())
        self.assertEqual(metadata["default"]["agent_sidebar_scale"], 190)
        web_runtime_state.capture_live_workspaces(manager, origin="manual", workspace_metadata=metadata)
        self.assertEqual(web_runtime_state.load_restorable_workspace("default")["agent_sidebar_scale"], 190)

    def test_stored_scale_defaults_when_absent_or_invalid_without_losing_shape(self):
        self._launch()
        self.client.post("/api/runtime-state/save", json={"workspace_id": "default"})
        stored = json.loads(self.state_path.read_text(encoding="utf-8"))
        for value in (None, True, "150", 99, 201, 150.5):
            stored["workspaces"]["default"]["agent_sidebar_scale"] = value
            self.state_path.write_text(json.dumps(stored), encoding="utf-8")
            slot = web_runtime_state.load_restorable_workspace("default")
            self.assertEqual(slot["agent_sidebar_scale"], 100)
            self.assertTrue(slot["groups"])
        del stored["workspaces"]["default"]["agent_sidebar_scale"]
        self.state_path.write_text(json.dumps(stored), encoding="utf-8")
        self.assertEqual(web_runtime_state.load_restorable_workspace("default")["agent_sidebar_scale"], 100)

    def test_launcher_save_captures_scale_acknowledged_by_the_window(self):
        from web import lifecycle

        self._launch()
        api.session_manager.set_agent_sidebar_scale("default", 125)
        with patch.object(lifecycle, "lifecycle_coordinator") as coordinator:
            coordinator.connected_window_count.return_value = 1
            coordinator.request_flush.return_value = {
                "ok": True,
                "metadata": {"default": [{"agent_sidebar_scale": 160}]},
            }
            result, status = lifecycle.prepare_workspace_save(
                api.session_manager, "default", lambda *_args: None
            )
        self.assertEqual(status, 200, result)
        self.assertEqual(result["agent_sidebar_scale"], 160)
        self.assertEqual(web_runtime_state.load_restorable_workspace("default")["agent_sidebar_scale"], 160)

    def test_an_unstated_panel_is_left_alone_and_a_non_boolean_is_refused(self):
        """The dimension postdates this transaction, so a client that says
        nothing about it is one that has no panel — and leaving what a payload
        does not state is the same rule the pane-relaunch route follows. A
        payload that states it *wrongly* is a broken client and is refused."""
        stated = normalize_workspace_presentation({
            "workspace_id": "default",
            "expected_revision": 0,
            "topbar_visible": True,
            "agent_sidebar_open": True,
        })
        silent = normalize_workspace_presentation({
            "workspace_id": "default",
            "expected_revision": 0,
            "topbar_visible": True,
        })

        self.assertTrue(stated["agent_sidebar_open"])
        self.assertNotIn("agent_sidebar_open", silent)
        with self.assertRaises(PresentationValidationError):
            normalize_workspace_presentation({
                "workspace_id": "default",
                "expected_revision": 0,
                "topbar_visible": True,
                "agent_sidebar_open": "yes",
            })

    def test_the_transaction_carries_it_and_an_older_client_cannot_reset_it(self):
        manager = SessionManager()

        opened = manager.apply_workspace_presentation(
            workspace_id="default",
            expected_revision=0,
            topbar_visible=True,
            agent_sidebar_open=True,
        )
        # A window that predates the panel states the bar and nothing else.
        silent = manager.apply_workspace_presentation(
            workspace_id="default",
            expected_revision=1,
            topbar_visible=False,
        )

        self.assertTrue(opened["agent_sidebar_open"])
        self.assertTrue(silent["agent_sidebar_open"])
        self.assertFalse(silent["topbar_visible"])
        self.assertEqual(silent["presentation_revision"], 2)
        self.assertTrue(manager.get_agent_sidebar_open("default"))

    def test_the_route_publishes_it_with_the_rest_of_the_window_chrome(self):
        self._launch()

        accepted = self.client.post(
            "/api/workspace-presentation",
            json={
                "workspace_id": "default",
                "expected_revision": 0,
                "topbar_visible": True,
                "agent_sidebar_open": True,
            },
        )
        groups = self.client.get("/api/session-groups").get_json()
        rejected = self.client.post(
            "/api/workspace-presentation",
            json={
                "workspace_id": "default",
                "expected_revision": 1,
                "topbar_visible": True,
                "agent_sidebar_open": 1,
            },
        )

        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        self.assertTrue(accepted.get_json()["agent_sidebar_open"])
        self.assertTrue(groups["agent_sidebar_open"])
        self.assertEqual(rejected.status_code, 400, rejected.get_json())
        self.assertTrue(api.session_manager.get_agent_sidebar_open("default"))

    def test_a_saved_workspace_comes_back_with_the_panel_it_was_saved_with(self):
        """The whole point of the state being the workspace's. The save carries
        it, the slot stores it, the restore installs it, and the window that
        reopens reads it back off `/api/session-groups`."""
        group_id = self._launch()

        saved = self.client.post(
            "/api/runtime-state/save",
            json={
                "workspace_id": "default",
                "active_group_id": group_id,
                "topbar_visible": False,
                "agent_sidebar_open": True,
                "agent_sidebar_scale": 175,
            },
        )
        offered = self.client.get("/api/runtime-state?workspace_id=default").get_json()
        stored = json.loads(self.state_path.read_text(encoding="utf-8"))

        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertTrue(saved.get_json()["agent_sidebar_open"])
        self.assertEqual(saved.get_json()["agent_sidebar_scale"], 175)
        self.assertEqual(offered["agent_sidebar_scale"], 175)
        self.assertEqual(stored["workspaces"]["default"]["agent_sidebar_scale"], 175)
        self.assertTrue(offered["agent_sidebar_open"])
        self.assertTrue(stored["workspaces"]["default"]["agent_sidebar_open"])

        # A restart: the live workspace is gone and the slot is all there is.
        api.session_manager.reset_sessions()
        restored = self.client.post(
            "/api/runtime-state/restore", json={"workspace_ids": ["default"]}
        )
        self.assertEqual(restored.status_code, 200, restored.get_json())
        workspace = restored.get_json()["workspaces"][0]
        self.assertTrue(workspace["restored"], workspace)
        self.assertTrue(workspace["agent_sidebar_open"])
        self.assertEqual(workspace["agent_sidebar_scale"], 175)
        self.assertEqual(
            self.client.get("/api/session-groups").get_json()["agent_sidebar_scale"], 175
        )
        self.assertTrue(
            self.client.get("/api/session-groups").get_json()["agent_sidebar_open"]
        )

    def test_a_slot_written_before_the_panel_existed_restores_with_it_shut(self):
        """Window chrome degrades; launchable shape fails. A slot from an older
        GridVibe carries no such field, and the workspace in it must still
        restore — with the panel it had, which is none."""
        group_id = self._launch()
        self.client.post(
            "/api/runtime-state/save",
            json={"workspace_id": "default", "active_group_id": group_id},
        )
        stored = json.loads(self.state_path.read_text(encoding="utf-8"))
        del stored["workspaces"]["default"]["agent_sidebar_open"]
        stored["workspaces"]["default"]["agent_sidebar_open"] = None
        self.state_path.write_text(json.dumps(stored), encoding="utf-8")

        slot = web_runtime_state.load_restorable_workspace("default")

        self.assertIsNotNone(slot)
        self.assertIs(slot["agent_sidebar_open"], False)
        self.assertTrue(slot["groups"])

    def test_the_save_refuses_a_non_boolean_before_it_writes_anything(self):
        group_id = self._launch()

        refused = self.client.post(
            "/api/runtime-state/save",
            json={
                "workspace_id": "default",
                "active_group_id": group_id,
                "agent_sidebar_open": "open",
            },
        )

        self.assertEqual(refused.status_code, 400, refused.get_json())
        self.assertFalse(self.state_path.exists())

    def test_the_exit_save_takes_the_newest_windows_answer_and_rejects_a_bad_one(self):
        """Two windows on one workspace both describe its chrome; the newest to
        have joined owns each field. A malformed *type* is a broken client and
        still raises, exactly as a non-boolean top bar does."""
        live = {"default": {"groups": [{"group_id": "g1"}]}}

        resolved = normalize_workspace_metadata(
            {"default": [
                {"agent_sidebar_open": False},
                {"agent_sidebar_open": True},
            ]},
            live,
        )

        self.assertTrue(resolved["default"]["agent_sidebar_open"])
        with self.assertRaises(LifecycleValidationError):
            normalize_workspace_metadata(
                {"default": [{"agent_sidebar_open": "open"}]}, live
            )


class DashboardSidebarPageTestCase(unittest.TestCase):
    """Where the panel and its handle sit on the workspace page."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def _page(self):
        response = self.client.get("/terminals")
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True)

    def _static(self, name):
        return self.client.get(f"/static/{name}").get_data(as_text=True)

    def test_the_handle_heads_the_session_tab_line_over_the_column_it_opens(self):
        """The panel is the leftmost surface on the page, so its control is the
        leftmost control — ahead of the three whose scope narrows from every
        window to this one."""
        html = self._page()

        bar = html.index('<div class="session-bar">')
        toggle = html.index('id="agentSidebarToggleBtn"')
        launcher = html.index('aria-label="Open launcher"')
        dashboard = html.index('id="dashboardBtn"')
        menu = html.index('id="sessionMenuRoot"')
        tabs = html.index('id="sessionTabs"')

        self.assertLess(bar, toggle)
        self.assertLess(toggle, launcher)
        self.assertLess(launcher, dashboard)
        self.assertLess(dashboard, menu)
        self.assertLess(menu, tabs)
        # Both supplied marks are reachable, and the shut state is what the
        # page is served in.
        self.assertIn('src="/docs/images/show_sidebar.ico"', html)
        self.assertIn("/docs/images/hide_sidebar.ico", self._static("js/dashboard-sidebar.js"))
        self.assertEqual(self.client.get("/docs/images/hide_sidebar.ico").status_code, 200)
        self.assertEqual(self.client.get("/docs/images/show_sidebar.ico").status_code, 200)

    def test_the_column_sits_beside_the_grid_rather_than_over_it(self):
        """It is chrome and not a dialog, so it is inside the flow: one row
        under the two bars, with the panel on the left and the grid taking what
        is left. No `.modal-shell`, so nothing about it is ever a scrim."""
        html = self._page()

        row = html.index('<div class="workspace-body"')
        panel = html.index('id="agentSidebar"')
        grid = html.index('id="terminalsGrid"')
        empty = html.index('id="emptyState"')

        self.assertLess(row, panel)
        self.assertLess(panel, grid)
        self.assertLess(grid, empty)
        aside = html[panel - 200:panel + 200]
        self.assertNotIn("modal-shell", aside)

        css = self._static("css/agent-dashboard-sidebar.css")
        self.assertIn(".workspace-body", css)
        # About a sixth of the window, clamped at both ends.
        width = re.search(r"--agent-sidebar-width:\s*([^;]+);", css)
        self.assertIsNotNone(width)
        self.assertIn("15%", width.group(1))
        self.assertIn("clamp(", width.group(1))

    def test_the_column_states_no_palette_of_its_own(self):
        """Guardrail 7: every colour is a shared token, the same ones the dialog
        reads, so the panel follows the page's light/dark preference without
        this stylesheet knowing which page it is on."""
        css = self._static("css/agent-dashboard-sidebar.css")

        for literal in re.findall(r"#[0-9a-fA-F]{3,8}\b", css):
            self.fail(f"palette literal in agent-dashboard-sidebar.css: {literal}")
        self.assertNotIn("rgb(", css)

    def test_the_module_is_loaded_after_the_dialog_whose_renderers_it_uses(self):
        html = self._page()

        dialog = html.index("js/dashboard-dialog.js")
        panel = html.index("js/dashboard-sidebar.js")
        page = html.index("js/terminals.js")

        self.assertLess(dialog, panel)
        self.assertLess(panel, page)
        self.assertIn("css/agent-dashboard-sidebar.css", html)

    def test_the_page_reports_and_saves_the_panel_with_the_rest_of_the_chrome(self):
        """The workspace descriptor, the explicit save and the exit-save
        metadata all carry it, so the three surfaces that can write a workspace
        snapshot agree about what it looked like."""
        terminals = self._static("js/terminals.js")

        self.assertIn("function agentSidebarIsOpen()", terminals)
        self.assertIn("agentSidebarOpen: agentSidebarIsOpen()", terminals)
        self.assertIn("agent_sidebar_open: agentSidebarIsOpen()", terminals)
        self.assertIn("wireAgentDashboardSidebar();", terminals)
        self.assertIn("scale: data.agent_sidebar_scale", terminals)
        self.assertIn("agentSidebarScale: agentSidebarScale()", terminals)
        self.assertIn("agent_sidebar_scale: agentSidebarScale()", terminals)
