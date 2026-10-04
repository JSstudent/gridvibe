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
from web import config as web_config
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
AGENT_CREWS_JS = STATIC_JS / "agent-crews.js"
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

/* The page's answer to "which session is being typed into". A plain value for
   the cases about the column; the page-half cases below hand it the real
   `focusedTerminalSessionId` lifted out of `terminals.js`. */
let inputTargetAnswer = '';
let inputTargetSource = () => inputTargetAnswer;

/* The crew wire layer, recorded rather than drawn: `agent-crews.js` is
   exercised against a DOM stub of its own (`test_agent_crews.py`), and what is
   pinned here is when the column asks it to do what. */
const wireCalls = { created: [], paints: [], highlights: [], paused: [] };

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
    crewHighlight: GridVibeAgentCrews.highlightState,
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
        mark: dashboardAgentMarkState,
        crewContext: dashboardCrewContext,
        crewChip: dashboardCrewChipHtml,
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
    inputTarget: () => inputTargetSource(),
    createWireLayer: options => {
        wireCalls.created.push({ mode: options.mode, container: options.container === body() });
        return {
            paint: reading => { wireCalls.paints.push(reading); },
            highlight: crew => { wireCalls.highlights.push(crew); },
            setPaused: on => { wireCalls.paused.push(on); },
            dispose() {}
        };
    },
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
            /* Accessible agent and state words accompany the visible mark. */
            who: grab('dash-agent-who'),
            line: grab('dash-agent-line'),
            state: /class="dash-activity dash-state-([a-z]+)"/.exec(inner)?.[1] || '',
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

/* The drawn rows as elements: one object per row, kept for as long as the
   markup that drew it is -- so a decoration survives a reading that repaints
   nothing and goes with its row on one that does, as it would on a page. Each
   carries its title from the markup and its (empty) crew chip slot, whose
   writes are counted. */
let drawnRows = { html: null, rows: [] };
function fakeSlot() {
    let html = '';
    const slot = { writes: 0 };
    Object.defineProperty(slot, 'innerHTML', {
        get: () => html,
        set: value => { html = String(value); slot.writes += 1; }
    });
    return slot;
}
function drawnAgentRows() {
    const html = body().innerHTML;
    if (drawnRows.html !== html) {
        drawnRows = {
            html,
            rows: parseAgentRows().map(row => {
                const element = fakeElement('');
                element.dataset = row.dataset;
                element.state = row.state;
                element.title = row.hover;
                element.crewSlot = /<span class="dash-agent-crew"><\/span>/.test(row.html)
                    ? fakeSlot() : null;
                /* The reading and the bar, as the markup drew them, so an
                   in-place write is told apart from a rebuilt row. */
                element.readingSlot = fakeSlot();
                element.progressSlot = fakeSlot();
                element.readingSlot.innerHTML = dashboardActivityHtml({
                    ...fetchAnswer.workspaces.flatMap(w => w.groups).flatMap(g => g.panes)
                        .find(p => p.session_id === row.dataset.sessionId), waiting: '' });
                element.progressSlot.innerHTML = dashboardProgressHtml({
                    ...fetchAnswer.workspaces.flatMap(w => w.groups).flatMap(g => g.panes)
                        .find(p => p.session_id === row.dataset.sessionId), waiting: '' });
                element.readingSlot.writes = 0;
                element.progressSlot.writes = 0;
                /* The agent's mark and the words kept beside it, as the markup
                   drew them: empty of flags, with every write counted, so a
                   frame turned on in place is told apart from a rebuilt row. */
                element.icon = {
                    dataset: {},
                    title: '',
                    writes: 0,
                    removeAttribute(name) { if (name === 'title') this.title = ''; this.writes += 1; }
                };
                let iconTitle = '';
                Object.defineProperty(element.icon, 'title', {
                    get: () => iconTitle,
                    set: value => { iconTitle = String(value); element.icon.writes += 1; },
                    enumerable: true
                });
                element.flagsSlot = { writes: 0 };
                let flagWords = '';
                Object.defineProperty(element.flagsSlot, 'textContent', {
                    get: () => flagWords,
                    set: value => { flagWords = String(value); element.flagsSlot.writes += 1; }
                });
                element.querySelector = selector => ({
                    '.dash-agent-crew': element.crewSlot,
                    '.dash-agent-reading': element.readingSlot,
                    '.dash-agent-progress': element.progressSlot,
                    '.dash-agent-icon': element.icon,
                    '.dash-agent-flags': element.flagsSlot
                }[selector] || null);
                element.closest = selector => (
                    selector === '.dash-agent[data-session-id]' ? element : null
                );
                element.removeAttribute = function (name) { delete this.attributes[name]; };
                return element;
            })
        };
    }
    return drawnRows.rows;
}

/* The drawn row for one session. */
function drawnRow(sessionId) {
    return drawnAgentRows().find(row => row.dataset.sessionId === sessionId) || null;
}

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
        round: 1,
        reason: ''
    }, extra || {});
}
body().querySelectorAll = selector => (
    selector === '.dash-agent[data-session-id]' ? drawnAgentRows() : []
);

/* Which rows wear the input-target mark, and what they say about it. */
function inputTargetMarks() {
    return drawnAgentRows()
        .filter(row => row.classList.contains('is-input-target') || row.attributes['aria-current'])
        .map(row => [row.dataset.sessionId, row.attributes['aria-current'] || null]);
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
            + AGENT_CREWS_JS.read_text(encoding="utf-8")
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

    def test_the_row_draws_no_mcp_chip_and_no_tag_at_all(self):
        """What a row may do is on its mark, not beside the title: the chip is
        gone from the markup whatever the pane has, so the title keeps the
        width."""
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([
                pane({ agent_mcp: true, agent_mcp_override: true, agent_auto_mode: true }),
                pane({ session_id: 's2', index: 1, agent_mcp: true })
            ])]);
            await sidebar.refresh();
            report({ html: body().innerHTML });
            """
        )
        self.assertNotIn("dash-tag", result["html"])
        self.assertNotIn(">MCP<", result["html"])

    def test_the_pane_running_with_gridvibe_tools_wears_the_headers_frame(self):
        """The mark of a pane with GridVibe's tools is framed, by the rule the
        pane header uses (`paneAgentMcpTag`): an agent, and the flag, whatever
        the transport -- and never a pane that is no longer an agent."""
        result = self._run_node(
            """
            sidebarShown();
            const panes = [
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
            ];
            fetchAnswer = snapshot([group(panes)]);
            await sidebar.refresh();
            report({
                marks: panes.map(entry => {
                    const icon = drawnRow(entry.session_id).icon;
                    return {
                        id: entry.session_id,
                        framed: 'mcp' in icon.dataset,
                        override: 'mcpOverride' in icon.dataset,
                        rule: Boolean(GridVibeAgentIdentity.paneAgentMcpTag(entry)),
                        title: icon.title,
                        headerTitle: GridVibeAgentIdentity.paneAgentMcpTagTitle(entry)
                    };
                })
            });
            """
        )
        by_id = {mark["id"]: mark for mark in result["marks"]}
        self.assertTrue(by_id["s1"]["framed"])
        self.assertFalse(by_id["s2"]["framed"])
        # A remote pane's tools ride its own transport home: framed alike.
        self.assertTrue(by_id["s3"]["framed"])
        # A flag left behind on a pane that is no longer an agent frames nothing.
        self.assertFalse(by_id["s4"]["framed"])
        for mark in result["marks"]:
            with self.subTest(pane=mark["id"]):
                self.assertEqual(mark["framed"], mark["rule"])
                self.assertFalse(mark["override"])
                # The hover is the header's own sentence, word for word.
                self.assertEqual(mark["title"], mark["headerTitle"])
        self.assertIn("GridVibe tools", by_id["s1"]["title"])

    def test_override_mode_turns_the_frame_red_and_the_hover_says_why(self):
        result = self._run_node(
            """
            sidebarShown();
            const red = pane({ agent_mcp: true, agent_mcp_override: true });
            const blue = pane({ session_id: 's2', index: 1, agent_mcp: true });
            const stray = pane({ session_id: 's3', index: 2, agent_mcp_override: true });
            fetchAnswer = snapshot([group([red, blue, stray])]);
            await sidebar.refresh();
            const state = id => {
                const icon = drawnRow(id).icon;
                return { framed: 'mcp' in icon.dataset, override: 'mcpOverride' in icon.dataset, title: icon.title };
            };
            report({
                red: state('s1'), blue: state('s2'), stray: state('s3'),
                headerRed: GridVibeAgentIdentity.paneAgentMcpTagTitle(red)
            });
            """
        )
        self.assertTrue(result["red"]["framed"])
        self.assertTrue(result["red"]["override"])
        self.assertEqual(result["red"]["title"], result["headerRed"])
        self.assertIn("override mode", result["red"]["title"])
        self.assertTrue(result["blue"]["framed"])
        self.assertFalse(result["blue"]["override"])
        # The grant means nothing without the tools, as in the header.
        self.assertFalse(result["stray"]["framed"])
        self.assertFalse(result["stray"]["override"])

    def test_auto_approval_is_a_pin_on_the_mark_and_says_so_in_words(self):
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([
                pane({ agent_auto_mode: true }),
                pane({ session_id: 's2', index: 1, agent_auto_mode: true, agent_mcp: true }),
                pane({ session_id: 's3', index: 2 })
            ])]);
            await sidebar.refresh();
            const state = id => {
                const row = drawnRow(id);
                return {
                    auto: 'auto' in row.icon.dataset,
                    framed: 'mcp' in row.icon.dataset,
                    title: row.icon.title,
                    words: row.flagsSlot.textContent
                };
            };
            report({ plain: state('s1'), both: state('s2'), none: state('s3') });
            """
        )
        self.assertTrue(result["plain"]["auto"])
        self.assertFalse(result["plain"]["framed"])
        self.assertIn("auto-approval", result["plain"]["title"])
        self.assertIn("auto-approval", result["plain"]["words"])
        # Both, on one mark: the frame and the pin, and a hover for each.
        self.assertTrue(result["both"]["auto"])
        self.assertTrue(result["both"]["framed"])
        self.assertIn("GridVibe tools", result["both"]["title"])
        self.assertIn("auto-approval", result["both"]["title"])
        self.assertIn("GridVibe tools", result["both"]["words"])
        self.assertIn("auto-approval", result["both"]["words"])
        # Neither: nothing drawn and nothing said.
        self.assertEqual(result["none"], {"auto": False, "framed": False, "title": "", "words": ""})

    def test_colour_is_never_the_only_statement_of_the_frame(self):
        """Blue and red are carried by the hover and the row's accessible name
        too: the words are on the row, out of flow, for a reader who is hearing
        it."""
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([
                pane({ agent_mcp: true, agent_mcp_override: true })
            ])]);
            await sidebar.refresh();
            report({ words: drawnRow('s1').flagsSlot.textContent, html: body().innerHTML });
            """
        )
        self.assertIn("override mode", result["words"])
        self.assertIn('<span class="dash-agent-flags"></span>', result["html"])

    def test_a_frame_or_pin_changing_is_written_in_place_and_never_rebuilds_the_row(self):
        result = self._run_node(
            """
            sidebarShown();
            fetchAnswer = snapshot([group([pane(), pane({ session_id: 's2', index: 1 })])]);
            await sidebar.refresh();
            const row = drawnRow('s1');
            const before = { html: body().innerHTML, writes: row.icon.writes, words: row.flagsSlot.writes };
            fetchAnswer = snapshot([group([
                pane({ agent_mcp: true, agent_auto_mode: true }), pane({ session_id: 's2', index: 1 })
            ])]);
            await sidebar.refresh();
            const turnedOn = {
                same: drawnRow('s1') === row,
                framed: 'mcp' in row.icon.dataset,
                auto: 'auto' in row.icon.dataset,
                writes: row.icon.writes,
                words: row.flagsSlot.writes
            };
            /* The same reading again writes nothing at all. */
            await sidebar.refresh();
            const quiet = { writes: row.icon.writes, words: row.flagsSlot.writes };
            fetchAnswer = snapshot([group([
                pane({ agent_mcp: true, agent_mcp_override: true }), pane({ session_id: 's2', index: 1 })
            ])]);
            await sidebar.refresh();
            report({
                before, turnedOn, quiet,
                red: 'mcpOverride' in row.icon.dataset,
                autoGone: 'auto' in row.icon.dataset,
                sameAfter: drawnRow('s1') === row
            });
            """
        )
        self.assertEqual(result["before"]["writes"], 0)
        self.assertTrue(result["turnedOn"]["same"])
        self.assertTrue(result["turnedOn"]["framed"])
        self.assertTrue(result["turnedOn"]["auto"])
        self.assertGreater(result["turnedOn"]["writes"], 0)
        self.assertEqual(result["quiet"], {"writes": result["turnedOn"]["writes"],
                                           "words": result["turnedOn"]["words"]})
        self.assertTrue(result["red"])
        self.assertFalse(result["autoGone"])
        self.assertTrue(result["sameAfter"])

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
                    mcp: dashboardAgentMarkState(reading).mcp
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
        self.assertFalse(dialog["mcp"])
        self.assertNotIn("dash-tag", row["html"])
        # The shell the pane runs on left the line in the dialog too; it is the
        # last line of the hover on both surfaces.
        self.assertTrue(row["hover"].endswith("SSH"))
        self.assertEqual(result["totals"], "1 agent · 1 session · 1 workspace")

    def test_a_resolved_conversation_name_paints_the_same_here(self):
        """Same pane, same answer: the name the dialog paints for a Codex row
        announcing only its id is the one this column paints, because both ask
        the same module. A second reading of `conversation_title` here is how
        one pane comes to read two ways on two surfaces."""
        result = self._run_node(
            """
            sidebarShown();
            const named = pane({
                agent_selection: 'codex',
                activity: activity({
                    title: '01a085f4-cc1c-7993-8ffc-2fd04d47c731',
                    conversation_title: 'Review OCR delegation'
                })
            });
            fetchAnswer = snapshot([group([named])]);
            await sidebar.refresh();
            const row = parseAgentRows()[0];
            report({ row, dialog: {
                line: dashboardPaneLine(named), hover: dashboardPaneHover(named)
            } });
            """
        )
        self.assertEqual(result["row"]["line"], "Review OCR delegation")
        self.assertEqual(result["row"]["line"], result["dialog"]["line"])
        self.assertEqual(result["row"]["hover"], result["dialog"]["hover"])
        self.assertNotIn("01a085f4", result["row"]["html"])

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
                agent_selection: 'codex', activity: activity({ title: 'Second' })
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


def _js_function_source(script: str, name: str) -> str:
    """One top-level JS function's source, brace-matched."""
    start = script.index(f"function {name}(")
    depth = 0
    for index in range(script.index("{", script.index(")", start)), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _js_listener_source(script: str, marker: str) -> str:
    """One `addEventListener(...)` statement whose handler body opens after
    `marker`, brace-matched and closed at its `);`."""
    start = script.index(marker)
    depth = 0
    for index in range(script.index("{", start), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:script.index(");", index) + 2]
    raise AssertionError(f"unbalanced braces after {marker}")


TERMINALS_JS = (STATIC_JS / "terminals.js").read_text(encoding="utf-8")

# The page half of the mark, lifted whole out of `terminals.js`: the focus
# lifecycle that decides which pane is the input target, and the tab switch that
# carries a grid out of the document.
TERMINAL_FOCUS_SOURCE = "\n".join(
    [
        _js_function_source(TERMINALS_JS, name)
        for name in (
            "isPlainTerminalCard",
            "terminalCardSlot",
            "explorerPaneIndexFromTarget",
            "paintActiveTerminalCard",
            "focusedTerminalSessionId",
            "paintDashboardInputTarget",
            "setFocusedTerminal",
            "clearActiveTerminalHighlight",
            "dropTerminalFocusForWindowSwitch",
            "resetFocusedTerminal",
            "focusPaneForArrival",
            "cacheVisibleGroupView",
            "gridLayoutClass",
            "replaceSessionPaneMode",
            "firstAttachedPlainTerminalIndex",
            "focusActiveOrDefaultTerminal",
            "setBroadcastInput",
            "toggleBroadcastInput",
            "wireBroadcastButton",
        )
    ]
    + [
        _js_listener_source(TERMINALS_JS, "document.addEventListener('focusin', event =>"),
        _js_listener_source(TERMINALS_JS, "document.addEventListener('focusout', event =>"),
    ]
)

# A workspace page with a grid of cards, each with the one element inside it
# that takes keyboard focus. Only what the lifted functions touch is modelled.
TERMINAL_FOCUS_PAGE = r"""
class HTMLElement {}
const documentListeners = fakeListeners();
let activeElement = null;
const outside = { closest: () => null };

class FakeCard extends HTMLElement {
    constructor(slot) {
        super();
        this.id = `tc-${slot}`;
        this.dataset = { slot: String(slot) };
        this.classList = fakeClassList();
        this.classList.add('terminal-container');
        this.attributes = {};
        this.input = new FakeInput(this);
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    contains(node) { return node === this || node === this.input; }
    closest(selector) { return selector === '.terminal-container' ? this : null; }
    scrollIntoView() {}
    focus() { moveFocus(this.input); }
}

class FakeInput extends HTMLElement {
    constructor(card) { super(); this.card = card; }
    closest(selector) { return this.card.closest(selector); }
    blur() {
        if (activeElement !== this) return;
        activeElement = null;
        documentListeners.fire('focusout', { target: this, relatedTarget: null });
    }
}

/* Focus moving the way the browser moves it: focusout from what had it, naming
   where it is going, then focusin on the new holder. */
function moveFocus(next) {
    const previous = activeElement;
    if (previous === next) return;
    activeElement = next;
    if (previous) documentListeners.fire('focusout', { target: previous, relatedTarget: next });
    documentListeners.fire('focusin', { target: next });
}

/* The top bar's Broadcast button, which a press would focus unless its
   mousedown default is prevented. */
const broadcastButton = {
    id: 'broadcastBtn',
    classList: fakeClassList(),
    attributes: {},
    title: '',
    setAttribute(name, value) { this.attributes[name] = value; },
    closest: () => null,
    ...fakeListeners()
};

/* A pointer press the way the browser runs one: mousedown, focus moving to the
   button unless that was prevented, then the button's inline onclick. */
function pressBroadcast() {
    const press = { defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    broadcastButton.fire('mousedown', press);
    if (!press.defaultPrevented) moveFocus(broadcastButton);
    toggleBroadcastInput();
}

const grid = {
    classList: fakeClassList(),
    className: '',
    style: { getPropertyValue: () => '', removeProperty() {} },
    children: [],
    get firstChild() { return this.children.find(Boolean) || null; }
};

const document = {
    get activeElement() { return activeElement; },
    getElementById: id => {
        if (id === 'terminalsGrid') return grid;
        if (id === 'broadcastBtn') return broadcastButton;
        /* Every mounted card has its body wrapper, `tw-N` beside `tc-N`. */
        const cardId = id.startsWith('tw-') ? `tc-${id.slice(3)}` : id;
        return grid.children.find(card => card && card.id === cardId) || null;
    },
    querySelectorAll: selector => (
        selector === '.terminal-container.terminal-active'
            ? grid.children.filter(card => card && card.classList.contains('terminal-active'))
            : []
    ),
    createDocumentFragment: () => ({
        nodes: [],
        appendChild(node) {
            grid.children = grid.children.filter(card => card !== node);
            this.nodes.push(node);
        }
    }),
    addEventListener: documentListeners.addEventListener
};

let terminals = [];
let sessionIds = [];
let gridBuilt = true;
let visibleGroupId = 'g1';
let _focusedTerminalIndex = -1;
let _activeExplorerIndex = -1;
let resizeObservers = [];
let cachedGroupViews = new Map();
let splitSlotRects = null;
let splitColumnWeights = null;
let splitRowWeights = null;
let originalSplitSlotCount = 0;
function _stopAllVoice() {}
function clearActiveGridResize() {}
function clearResizeHandles() {}
function captureCachedPaneUiState() {}
function noteGroupPresentationChanged() {}
function clearFitTimers() {}
function disconnectObservers() {}
function cloneSplitSlotRects() { return null; }
function cloneSplitTrackWeights() { return null; }
function isExplorerSession(session) { return session?.mode === 'explorer'; }
function isBrowserSession(session) { return session?.mode === 'browser'; }
function isExplorerPaneInstance(pane) { return pane?._paneType === 'explorer'; }
function explorerReleasePaneWork() {}
let broadcastInputActive = false;
let _broadcastIdleTimer = null;
function _noteBroadcastActivity() {}

/* A pane switched to Files: its xterm is disposed and its input leaves the
   document, and -- as a browser may -- no focusout says so. */
function replacePaneWithExplorer(index, session) {
    const card = grid.children[index];
    card.input = new FakeInput(card);
    card.classList.add('explorer-pane');
    terminals[index] = { _session: session, _paneType: 'explorer' };
    sessionIds[index] = session.session_id;
    return true;
}
function replacePaneWithBrowser() { return false; }
function replacePaneWithTerminal() { return false; }

/* The page's own names for the sidebar: the module's browser half binds these
   to the same controller the harness drives. */
function markAgentDashboardSidebarInputTarget() { return sidebar.markInputTarget(); }
inputTargetSource = () => focusedTerminalSessionId();

/* One pane in a slot: an agent terminal by default, or an explorer card. */
function mountPane(slot, sessionId, kind = '') {
    const card = new FakeCard(slot);
    if (kind) card.classList.add(kind);
    grid.children[slot] = card;
    terminals[slot] = {
        _session: { session_id: sessionId, mode: kind === 'explorer-pane' ? 'explorer' : 'ssh' },
        term: kind ? null : { focus: () => moveFocus(card.input) }
    };
    sessionIds[slot] = sessionId;
    return card;
}

function activeCards() {
    return grid.children
        .filter(card => card && card.classList.contains('terminal-active'))
        .map(card => card.id);
}
"""


class DashboardSidebarInputTargetTestCase(DashboardSidebarNodeTestCase):
    """The row of the pane the reader is typing into.

    The page answers which session holds keyboard focus; the column lays a
    mark on the row that names it. The mark is a decoration on rows already
    drawn and never part of the markup, so moving it rebuilds no button, and
    every reading puts it back on whatever row is the target *now*."""

    TWO_AGENTS = """
        fetchAnswer = snapshot([group([
            pane({ activity: activity({ title: 'First', state: 'working' }) }),
            pane({ session_id: 's2', index: 1, activity: activity({ state: 'idle' }) })
        ])]);
    """

    def test_the_row_of_the_pane_being_typed_into_is_marked_and_follows_it(self):
        result = self._run_node(
            "sidebarShown();\n" + self.TWO_AGENTS + """
            inputTargetAnswer = 's1';
            await sidebar.refresh();
            const first = inputTargetMarks();
            inputTargetAnswer = 's2';
            const named = sidebar.markInputTarget();
            const second = inputTargetMarks();
            inputTargetAnswer = '';
            sidebar.markInputTarget();
            report({
                first, named, second,
                none: inputTargetMarks(),
                inMarkup: /is-input-target|aria-current/.test(body().innerHTML)
            });
            """
        )
        self.assertEqual(result["first"], [["s1", "true"]])
        self.assertEqual(result["named"], "s2")
        self.assertEqual(result["second"], [["s2", "true"]])
        self.assertEqual(result["none"], [])
        # A decoration, not markup: the reading's own comparison never sees it.
        self.assertFalse(result["inMarkup"])

    def test_a_reading_keeps_the_mark_and_a_rebuilt_row_gets_it_back(self):
        result = self._run_node(
            "sidebarShown();\n" + self.TWO_AGENTS + """
            inputTargetAnswer = 's1';
            await sidebar.refresh();
            const before = drawnAgentRows()[0];
            await sidebar.refresh();
            const kept = drawnAgentRows()[0] === before
                && before.classList.contains('is-input-target');
            fetchAnswer = snapshot([group([
                pane({ agent_selection: 'codex', activity: activity({ title: 'Second' }) }),
                pane({ session_id: 's2', index: 1 })
            ])]);
            await sidebar.refresh();
            report({
                kept,
                rebuilt: drawnAgentRows()[0] !== before,
                marks: inputTargetMarks()
            });
            """
        )
        self.assertTrue(result["kept"])
        self.assertTrue(result["rebuilt"])
        self.assertEqual(result["marks"], [["s1", "true"]])

    def test_a_target_with_no_row_marks_nothing(self):
        """A plain shell, or an agent pane the reading has not listed yet: the
        page names it, and no row is it, so no row is marked — least of all
        the one that used to be."""
        result = self._run_node(
            "sidebarShown();\n" + self.TWO_AGENTS + """
            inputTargetAnswer = 's1';
            await sidebar.refresh();
            inputTargetAnswer = 's9';
            sidebar.markInputTarget();
            const unlisted = inputTargetMarks();
            inputTargetAnswer = 's2';
            sidebar.markInputTarget();
            fetchAnswer = snapshot([group([pane()])]);
            await sidebar.refresh();
            report({ unlisted, departed: inputTargetMarks() });
            """
        )
        self.assertEqual(result["unlisted"], [])
        self.assertEqual(result["departed"], [])

    def test_the_mark_leaves_the_activity_reading_alone(self):
        """Being typed into and working are two facts. The idle row that is the
        target stays idle, and the working one that is not stays working."""
        result = self._run_node(
            "sidebarShown();\n" + self.TWO_AGENTS + """
            await sidebar.refresh();
            const markup = body().innerHTML;
            const states = drawnAgentRows().map(row => row.state);
            inputTargetAnswer = 's2';
            sidebar.markInputTarget();
            report({
                states,
                after: parseAgentRows().map(row => row.state),
                unchanged: body().innerHTML === markup,
                marks: inputTargetMarks()
            });
            """
        )
        self.assertEqual(result["states"], ["working", "idle"])
        self.assertEqual(result["after"], ["working", "idle"])
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["marks"], [["s2", "true"]])


class DashboardSidebarCrewTestCase(DashboardSidebarNodeTestCase):
    """Which agent handed a task to which, on the column.

    Everything a crew adds is laid on rows already drawn: the lane gutter is a
    class on the panel, the orchestrator's chip is written into its row's own
    slot, a worker's hover gains one line, and hovering a crew's
    row highlights it. None of it is in the markup a reading is compared by, so
    a report, a phase change or a new round never rebuilds a row. Four panes:
    an orchestrator (`s1`), two agents (`s2`, `s3`) and one more (`s4`)."""

    READING = """
        function crewReading(links, titles) {
            const named = titles || {};
            return snapshot([group([
                pane({ activity: activity({ title: named.s1 || 'Plan the release', state: 'idle' }) }),
                pane({ session_id: 's2', index: 1, agent_selection: 'codex',
                    activity: activity({ title: named.s2 || 'Review the parser' }) }),
                pane({ session_id: 's3', index: 2,
                    activity: activity({ title: named.s3 || 'Write the tests' }) }),
                pane({ session_id: 's4', index: 3,
                    activity: activity({ title: named.s4 || 'On its own' }) })
            ])], { links });
        }
        function members() {
            return drawnAgentRows()
                .filter(row => row.classList.contains('is-crew-member'))
                .map(row => row.dataset.sessionId);
        }
    """

    def test_removed_hover_element_clears_even_when_its_crew_survives(self):
        result = self._run_node(self.READING + """
            sidebarShown(); sidebar.wireRows();
            fetchAnswer = crewReading([link('s1', 's2'), link('s1', 's3')]);
            await sidebar.refresh();
            body().fire('pointerover', { target: drawnRow('s2') });
            const before = members();
            fetchAnswer.workspaces[0].groups[0].panes.splice(1, 1);
            fetchAnswer.links.splice(0, 1);
            await sidebar.refresh(); // no pointerleave from the removed element
            const removed = members();
            fetchAnswer = crewReading([link('s1', 's2'), link('s1', 's3')]);
            await sidebar.refresh();
            report({ before, removed, returned: members() });
        """)
        self.assertEqual(result["before"], ["s1", "s2", "s3"])
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["returned"], [])

    def test_focus_does_not_highlight_a_crew_and_pointer_out_clears_it(self):
        result = self._run_node(self.READING + """
            sidebarShown(); sidebar.wireRows();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await sidebar.refresh();
            body().fire('focusin', { target: drawnRow('s2') });
            const before = members();
            focusIsInside = true;
            body().fire('focusout', { relatedTarget: { closest: () => null } });
            const heading = members();
            body().fire('pointerover', { target: drawnRow('s1') });
            body().fire('pointerout', { relatedTarget: null });
            report({ before, heading, out: members() });
        """)
        self.assertEqual(result["before"], [])
        self.assertEqual(result["heading"], [])
        self.assertEqual(result["out"], [])

    def test_shared_board_click_highlights_docked_rows_and_close_clears_it(self):
        result = self._run_node(self.READING + """
            sidebarShown(); sidebar.wireRows();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            await sidebar.refresh();
            GridVibeAgentCrews.highlightState.set('board-click', 's3', 'click', 's4');
            const click = members();
            body().fire('pointerover', { target: drawnRow('s2') });
            const hovered = GridVibeAgentCrews.highlightState.get();
            body().fire('pointerleave', {});
            const left = members();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await sidebar.refresh();
            report({ click, hovered, left, closed: members(), state: GridVibeAgentCrews.highlightState.get() });
        """)
        self.assertEqual(result["click"], ["s3", "s4"])
        self.assertEqual(result["hovered"], "s1")
        self.assertEqual(result["left"], ["s3", "s4"])
        self.assertEqual(result["closed"], [])
        self.assertEqual(result["state"], "")

    def test_new_wire_layer_inherits_an_existing_board_click(self):
        result = self._run_node(self.READING + """
            sidebarShown();
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            GridVibeAgentCrews.highlightState.set('board-click', 's3', 'click', 's4');
            await sidebar.refresh();
            report({ state: GridVibeAgentCrews.highlightState.get(), members: members(), wire: wireCalls.highlights.at(-1) });
        """)
        self.assertEqual(result["state"], "s3")
        self.assertEqual(result["members"], ["s3", "s4"])
        self.assertEqual(result["wire"], "s3")

    def test_the_gutter_opens_only_while_there_is_a_crew(self):
        result = self._run_node(
            self.READING + """
            sidebarShown();
            fetchAnswer = crewReading([]);
            await sidebar.refresh();
            const none = shell().classList.contains('has-crews');
            const markup = body().innerHTML;
            fetchAnswer = crewReading([link('s1', 's2')]);
            await sidebar.refresh();
            const some = shell().classList.contains('has-crews');
            const sameMarkup = body().innerHTML === markup;
            fetchAnswer = crewReading([]);
            await sidebar.refresh();
            report({ none, some, sameMarkup, gone: shell().classList.contains('has-crews') });
            """
        )
        self.assertFalse(result["none"])
        self.assertTrue(result["some"])
        # The gutter is the panel's class, not a row's markup.
        self.assertTrue(result["sameMarkup"])
        self.assertFalse(result["gone"])

    def test_the_orchestrators_chip_is_the_dialogs_own_builder(self):
        """`reported/total` per worker, a closed worker included, on the
        orchestrator's row only, and stated in words for a reader hearing the
        row."""
        result = self._run_node(
            self.READING + """
            sidebarShown();
            fetchAnswer = crewReading([
                link('s1', 's2', { state: 'reported', status: 'done' }),
                link('s1', 's3'),
                link('s1', 's9', { state: 'reported', status: 'failed', collected: true })
            ]);
            await sidebar.refresh();
            const reading = fetchAnswer;
            report({
                chip: drawnRow('s1').crewSlot.innerHTML,
                dialog: dashboardCrewChipHtml(
                    reading.workspaces[0].groups[0].panes[0],
                    GridVibeAgentCrews.indexCrews(reading)
                ),
                others: ['s2', 's3', 's4'].map(id => drawnRow(id).crewSlot.innerHTML),
                inMarkup: body().innerHTML.includes('dash-crew-chip'),
                columns: parseAgentRows()[0].columns
            });
            """
        )
        self.assertEqual(result["chip"], result["dialog"])
        self.assertIn('<span class="dash-crew-count" aria-hidden="true">2/3</span>', result["chip"])
        self.assertIn('title="Handed tasks to 3 agents; 2 reported"', result["chip"])
        self.assertIn(
            '<span class="dash-crew-word">Handed tasks to 3 agents; 2 reported</span>',
            result["chip"],
        )
        self.assertIn('class="dash-crew-glyph"', result["chip"])
        self.assertEqual(result["others"], ["", "", ""])
        self.assertFalse(result["inMarkup"])
        # The slot sits after the title, before the bar.
        columns = result["columns"]
        self.assertEqual(
            columns[columns.index("dash-agent-line") + 1], "dash-agent-crew"
        )

    def test_a_report_updates_the_chip_in_place(self):
        """A report changes a chip, and nothing else: the row element, the
        markup and the scroller all stay, and an unchanged poll writes
        nothing."""
        result = self._run_node(
            self.READING + """
            sidebarShown();
            fetchAnswer = crewReading([link('s1', 's2'), link('s1', 's3')]);
            await sidebar.refresh();
            const row = drawnRow('s1');
            const slot = row.crewSlot;
            const markup = body().innerHTML;
            const first = slot.innerHTML;
            body().scrollTop = 55;
            fetchAnswer = crewReading([
                link('s1', 's2', { state: 'reported', status: 'done' }),
                link('s1', 's3')
            ]);
            await sidebar.refresh();
            const reported = {
                sameRow: drawnRow('s1') === row,
                sameMarkup: body().innerHTML === markup,
                chip: slot.innerHTML,
                writes: slot.writes,
                scroll: body().scrollTop
            };
            await sidebar.refresh();
            report({ first, reported, idleWrites: slot.writes,
                workerWrites: drawnRow('s2').crewSlot.writes });
            """
        )
        self.assertIn(">0/2<", result["first"])
        self.assertTrue(result["reported"]["sameRow"])
        self.assertTrue(result["reported"]["sameMarkup"])
        self.assertIn(">1/2<", result["reported"]["chip"])
        self.assertEqual(result["reported"]["writes"], 2)
        self.assertEqual(result["reported"]["scroll"], 55)
        self.assertEqual(result["idleWrites"], 2)
        self.assertEqual(result["workerWrites"], 0)

    def test_a_worker_hover_names_its_orchestrator_and_the_round_from_two(self):
        result = self._run_node(
            self.READING + """
            sidebarShown();
            fetchAnswer = crewReading([link('s1', 's2')]);
            await sidebar.refresh();
            const worker = drawnRow('s2');
            const round1 = worker.title;
            const orchestrator = drawnRow('s1').title;
            const alone = drawnRow('s4').title;
            const plain = dashboardPaneHover(fetchAnswer.workspaces[0].groups[0].panes[3]);
            const markup = body().innerHTML;
            fetchAnswer = crewReading([link('s1', 's2', { link_id: 'next-round', round: 2 })]);
            await sidebar.refresh();
            const round2 = worker.title;
            const sameRow = drawnRow('s2') === worker && body().innerHTML === markup;
            fetchAnswer = crewReading([link('s1', 's2', { round: 2 })], { s1: 'Ship it' });
            await sidebar.refresh();
            report({ round1, round2, sameRow, orchestrator, alone, plain,
                renamed: drawnRow('s2').title });
            """
        )
        self.assertEqual(
            result["round1"].split("\n")[-2], "Working for Claude Code · Plan the release"
        )
        self.assertNotIn("round", result["round1"])
        self.assertEqual(
            result["round2"].split("\n")[-2],
            "Working for Claude Code · Plan the release · round 2",
        )
        # Under the chat line and its path, above the shell, which stays the
        # hover's last line.
        self.assertTrue(result["round2"].endswith("SSH"))
        self.assertTrue(result["sameRow"])
        self.assertNotIn("Working for", result["orchestrator"])
        self.assertEqual(result["alone"], result["plain"])
        # The orchestrator's own line is read again on every reading.
        self.assertIn("Working for Claude Code · Ship it · round 2", result["renamed"])

    def test_only_hover_highlights_a_crew_and_the_input_ring_stays(self):
        """Two crews. The highlighted one's rows are members, the panel says a
        crew is highlighted, the wires are told which, and the row being typed
        into keeps its ring whichever crew is lit. A reading that rebuilds the
        rows puts the highlight straight back."""
        result = self._run_node(
            self.READING + """
            storedSidebar = true;
            inputTargetAnswer = 's3';
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')]);
            sidebar.wire();
            await settle();
            body().fire('pointerover', { target: drawnRow('s2') });
            const hovered = {
                panel: shell().classList.contains('is-crew-highlight'),
                members: members(),
                wires: wireCalls.highlights[wireCalls.highlights.length - 1],
                ring: drawnRow('s3').classList.contains('is-input-target')
            };
            fetchAnswer = crewReading([link('s1', 's2'), link('s3', 's4')], { s2: 'Renamed' });
            await sidebar.refresh();
            const rebuilt = { members: members(), ring: drawnRow('s3').classList.contains('is-input-target') };
            body().fire('pointerleave', {});
            const left = { panel: shell().classList.contains('is-crew-highlight'), members: members(),
                wires: wireCalls.highlights[wireCalls.highlights.length - 1] };
            body().fire('focusin', { target: drawnRow('s4') });
            const focused = { members: members(), wires: wireCalls.highlights[wireCalls.highlights.length - 1] };
            body().fire('pointerover', { target: drawnRow('s1') });
            const pointerWins = members();
            body().fire('pointerover', { target: { closest: () => null } });
            const backToFocus = members();
            body().fire('focusout', { relatedTarget: null });
            report({ hovered, rebuilt, left, focused, pointerWins, backToFocus,
                cleared: { panel: shell().classList.contains('is-crew-highlight'), members: members() },
                inMarkup: /is-crew-member|is-crew-highlight/.test(body().innerHTML) });
            """
        )
        self.assertTrue(result["hovered"]["panel"])
        self.assertEqual(result["hovered"]["members"], ["s1", "s2"])
        self.assertEqual(result["hovered"]["wires"], "s1")
        self.assertTrue(result["hovered"]["ring"])
        self.assertEqual(result["rebuilt"]["members"], ["s1", "s2"])
        self.assertTrue(result["rebuilt"]["ring"])
        self.assertFalse(result["left"]["panel"])
        self.assertEqual(result["left"]["members"], [])
        self.assertEqual(result["left"]["wires"], "")
        self.assertEqual(result["focused"]["members"], [])
        self.assertEqual(result["focused"]["wires"], "")
        self.assertEqual(result["pointerWins"], ["s1", "s2"])
        self.assertEqual(result["backToFocus"], [])
        self.assertFalse(result["cleared"]["panel"])
        self.assertEqual(result["cleared"]["members"], [])
        self.assertFalse(result["inMarkup"])

    def test_the_wires_follow_every_reading_and_stand_down_with_the_poll(self):
        result = self._run_node(
            self.READING + """
            storedSidebar = true;
            fetchAnswer = crewReading([link('s1', 's2')]);
            sidebar.wire();
            await settle();
            await sidebar.refresh();
            const painted = {
                created: wireCalls.created,
                paints: wireCalls.paints.length,
                last: wireCalls.paints[wireCalls.paints.length - 1] === fetchAnswer
            };
            documentIsHidden = true;
            visibilityListeners.fire('v');
            documentIsHidden = false;
            visibilityListeners.fire('v');
            await settle();
            sidebar.apply(false);
            report({ painted, paused: wireCalls.paused, paints: wireCalls.paints.length });
            """
        )
        self.assertEqual(result["painted"]["created"], [{"mode": "lane", "container": True}])
        self.assertEqual(result["painted"]["paints"], 2)
        self.assertTrue(result["painted"]["last"])
        # Created running; held while the document is hidden; running again
        # when it is back; held when the panel is shut.
        self.assertEqual(result["paused"], [False, True, False, True])
        self.assertEqual(result["paints"], 3)

    def test_a_waiting_agent_wears_the_waiting_mark_and_says_why(self):
        """Waiting overrides the activity reading, after the transport: an
        orchestrator in `wait_for_results` and a worker standing by in
        `wait_for_task` wear the one mark, in their own words, and no bar. It
        is laid on the drawn row: the markup keeps the plain reading."""
        result = self._run_node(
            """
            sidebarShown();
            const panes = [
                pane({ waiting: 'crew', activity: activity({ progress_state: 'indeterminate' }) }),
                pane({ session_id: 's2', index: 1, waiting: 'task', activity: activity() }),
                pane({ session_id: 's3', index: 2, waiting: 'crew', status: 'disconnected' }),
                pane({ session_id: 's4', index: 3, waiting: '', activity: activity() })
            ];
            fetchAnswer = snapshot([group(panes)]);
            await sidebar.refresh();
            const shown = drawnAgentRows().map((row, index) => {
                const reading = row.readingSlot.writes
                    ? row.readingSlot.innerHTML : dashboardActivityHtml({ ...panes[index], waiting: '' });
                return [
                    /dash-state-([a-z]+)/.exec(reading)[1],
                    /<span class="dash-state-word">([^<]*)<\\/span>/.exec(reading)[1],
                    row.progressSlot.writes ? row.progressSlot.innerHTML.includes('dash-progress-fill') : null
                ];
            });
            report({ shown, inMarkup: /dash-state-waiting/.test(body().innerHTML) });
            """
        )
        self.assertEqual(result["shown"], [
            # The bar the plain reading drew is taken off.
            ["waiting", "Waiting on its crew", False],
            ["waiting", "Standing by for its next task", None],
            ["error", "Disconnected", None],
            ["working", "Working", None],
        ])
        self.assertFalse(result["inMarkup"])

    def test_entering_and_leaving_a_wait_rewrites_the_dot_not_the_row(self):
        """An orchestrator enters `wait_for_results` and leaves it again many
        times a task, so each flip rewrites its reading slot and its bar and
        keeps the row, the markup and the scroller."""
        result = self._run_node(
            self.READING + """
            sidebarShown();
            const reading = waiting => {
                const next = crewReading([link('s1', 's2')]);
                next.workspaces[0].groups[0].panes[0] = pane({
                    waiting,
                    activity: activity({ title: 'Plan the release', progress_state: 'indeterminate' })
                });
                return next;
            };
            fetchAnswer = reading('');
            await sidebar.refresh();
            const row = drawnRow('s1');
            const markup = body().innerHTML;
            body().scrollTop = 40;
            const steps = [];
            for (const waiting of ['crew', 'task', '']) {
                fetchAnswer = reading(waiting);
                await sidebar.refresh();
                steps.push({
                    sameRow: drawnRow('s1') === row,
                    sameMarkup: body().innerHTML === markup,
                    word: /<span class="dash-state-word">([^<]*)<\\/span>/.exec(row.readingSlot.innerHTML)[1],
                    bar: row.progressSlot.innerHTML.includes('dash-progress-fill')
                });
            }
            const writes = row.readingSlot.writes;
            await sidebar.refresh();
            report({ steps, scroll: body().scrollTop, idle: row.readingSlot.writes === writes,
                plain: row.readingSlot.innerHTML === dashboardActivityHtml(fetchAnswer.workspaces[0].groups[0].panes[0]) });
            """
        )
        self.assertEqual(result["steps"], [
            {"sameRow": True, "sameMarkup": True, "word": "Waiting on its crew", "bar": False},
            {"sameRow": True, "sameMarkup": True, "word": "Standing by for its next task", "bar": False},
            {"sameRow": True, "sameMarkup": True, "word": "Working", "bar": True},
        ])
        self.assertEqual(result["scroll"], 40)
        self.assertTrue(result["idle"])
        self.assertTrue(result["plain"])


class DashboardSidebarInputTargetPageTestCase(DashboardSidebarNodeTestCase):
    """The page half, executed: the focus lifecycle lifted out of
    `terminals.js`, driven with focus events on a stub grid, answering the real
    column. Two agent panes (`s1`, `s2`), a plain shell with no row (`s3`) and
    an explorer card (`s4`)."""

    SETUP = TERMINAL_FOCUS_PAGE + TERMINAL_FOCUS_SOURCE + """
        sidebarShown();
        mountPane(0, 's1');
        mountPane(1, 's2');
        mountPane(2, 's3');
        mountPane(3, 's4', 'explorer-pane');
        fetchAnswer = snapshot([group([
            pane(),
            pane({ session_id: 's2', index: 1 })
        ], { pane_count: 4 })]);
        await sidebar.refresh();
    """

    def _run_page(self, body: str):
        return self._run_node(self.SETUP + body)

    def test_focus_moving_between_agent_panes_moves_the_row(self):
        result = self._run_page(
            """
            moveFocus(grid.children[0].input);
            const first = { marks: inputTargetMarks(), cards: activeCards() };
            moveFocus(grid.children[1].input);
            report({ first, second: { marks: inputTargetMarks(), cards: activeCards() } });
            """
        )
        self.assertEqual(result["first"], {"marks": [["s1", "true"]], "cards": ["tc-0"]})
        self.assertEqual(result["second"], {"marks": [["s2", "true"]], "cards": ["tc-1"]})

    def test_focus_leaving_for_anything_but_an_agent_terminal_clears_the_row(self):
        result = self._run_page(
            """
            moveFocus(grid.children[0].input);
            moveFocus(outside);
            const chrome = inputTargetMarks();
            moveFocus(grid.children[1].input);
            moveFocus(grid.children[3].input);
            const explorer = { marks: inputTargetMarks(), cards: activeCards() };
            moveFocus(grid.children[0].input);
            moveFocus(grid.children[2].input);
            report({
                chrome, explorer,
                shell: { marks: inputTargetMarks(), cards: activeCards() }
            });
            """
        )
        self.assertEqual(result["chrome"], [])
        self.assertEqual(result["explorer"], {"marks": [], "cards": []})
        # A plain shell is the input target and its pane says so; it has no row.
        self.assertEqual(result["shell"], {"marks": [], "cards": ["tc-2"]})

    def test_a_dashboard_landing_marks_the_row_it_landed_on(self):
        result = self._run_page(
            """
            moveFocus(outside);
            focusPaneForArrival(1);
            report(inputTargetMarks());
            """
        )
        self.assertEqual(result, [["s2", "true"]])

    def test_a_pane_replaced_or_removed_under_focus_is_answered_by_what_is_there(self):
        """The slot is not the identity. A relaunch into a new session keeps the
        pane focused and names the new session; a card that has left the
        document names nothing, whatever the slot number now holds."""
        result = self._run_page(
            """
            moveFocus(grid.children[0].input);
            terminals[0]._session.session_id = 's9';
            sessionIds[0] = 's9';
            await sidebar.refresh();
            const unlisted = { target: focusedTerminalSessionId(), marks: inputTargetMarks() };
            terminals[0]._session.session_id = 's1';
            sessionIds[0] = 's1';
            await sidebar.refresh();
            const back = inputTargetMarks();
            const removed = grid.children[0];
            grid.children[0] = new FakeCard(0);
            terminals[0] = { _session: { session_id: 's2', mode: 'ssh' } };
            await sidebar.refresh();
            report({
                unlisted, back,
                stillHeld: activeElement === removed.input,
                afterRemoval: { target: focusedTerminalSessionId(), marks: inputTargetMarks() }
            });
            """
        )
        self.assertEqual(result["unlisted"], {"target": "s9", "marks": []})
        self.assertEqual(result["back"], [["s1", "true"]])
        self.assertTrue(result["stillHeld"])
        self.assertEqual(result["afterRemoval"], {"target": "", "marks": []})

    def test_switching_the_focused_pane_to_files_clears_the_row_before_any_reading(self):
        """The mode switch replaces the pane's input without a focusout, so the
        target is dropped by the switch itself -- not left for the next poll,
        which may fail -- and only for the pane being switched."""
        result = self._run_page(
            """
            moveFocus(grid.children[0].input);
            replaceSessionPaneMode(1, { session_id: 's2', mode: 'explorer' });
            const other = { marks: inputTargetMarks(), index: _focusedTerminalIndex };
            replaceSessionPaneMode(0, { session_id: 's1', mode: 'explorer' });
            const switched = {
                marks: inputTargetMarks(),
                index: _focusedTerminalIndex,
                cards: activeCards()
            };
            fetchAnswer = null;
            await sidebar.refresh();
            report({ other, switched, afterFailedRead: inputTargetMarks() });
            """
        )
        self.assertEqual(result["other"], {"marks": [["s1", "true"]], "index": 0})
        self.assertEqual(result["switched"], {"marks": [], "index": -1, "cards": []})
        self.assertEqual(result["afterFailedRead"], [])

    def test_a_failed_reading_still_unmarks_a_target_that_went_away(self):
        result = self._run_page(
            """
            moveFocus(grid.children[0].input);
            const removed = grid.children[0];
            grid.children[0] = new FakeCard(0);
            fetchAnswer = null;
            await sidebar.refresh();
            report({ stillHeld: activeElement === removed.input, marks: inputTargetMarks() });
            """
        )
        self.assertTrue(result["stillHeld"])
        self.assertEqual(result["marks"], [])

    def test_leaving_the_window_or_the_tab_clears_the_row(self):
        result = self._run_page(
            """
            moveFocus(grid.children[0].input);
            dropTerminalFocusForWindowSwitch();
            const windowSwitch = {
                marks: inputTargetMarks(), cards: activeCards(), focus: activeElement
            };
            moveFocus(grid.children[1].input);
            const card = grid.children[1];
            cacheVisibleGroupView('g1');
            report({
                windowSwitch,
                tabSwitch: {
                    marks: inputTargetMarks(),
                    index: _focusedTerminalIndex,
                    cardStillActive: card.classList.contains('terminal-active'),
                    cached: cachedGroupViews.has('g1')
                }
            });
            """
        )
        self.assertEqual(
            result["windowSwitch"], {"marks": [], "cards": [], "focus": None}
        )
        self.assertEqual(
            result["tabSwitch"],
            {"marks": [], "index": -1, "cardStillActive": False, "cached": True},
        )

    def test_pressing_broadcast_keeps_the_pane_being_typed_into(self):
        """Broadcast rings every plain pane; it neither makes every agent the
        pane being typed into nor moves which one is. The press does not take
        focus, so turning it on keeps the second pane rather than falling back
        to the first, and turning it off leaves that pane where it was."""
        result = self._run_page(
            """
            wireBroadcastButton();
            moveFocus(grid.children[1].input);
            pressBroadcast();
            const on = {
                broadcast: grid.classList.contains('broadcast-input'),
                ring: grid.classList.contains('terminal-focus'),
                marks: inputTargetMarks(),
                cards: activeCards()
            };
            pressBroadcast();
            report({
                on,
                off: {
                    broadcast: grid.classList.contains('broadcast-input'),
                    marks: inputTargetMarks(),
                    cards: activeCards()
                }
            });
            """
        )
        self.assertEqual(
            result["on"],
            {"broadcast": True, "ring": True, "marks": [["s2", "true"]], "cards": ["tc-1"]},
        )
        self.assertEqual(
            result["off"],
            {"broadcast": False, "marks": [["s2", "true"]], "cards": ["tc-1"]},
        )


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
                'agentDashboardSidebarSide',
                /* terminals.js calls this on every focus change, and the
                   column reads the answer back off the page's own name. */
                'markAgentDashboardSidebarInputTarget'
            ];
            wireAgentDashboardSidebar();
            applyAgentDashboardSidebar(true, { persist: false });
            applyAgentDashboardSidebarSide('right');
            window.focusedTerminalSessionId = () => 's4';
            process.stdout.write(JSON.stringify({
                inputTarget: markAgentDashboardSidebarInputTarget(),
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
        self.assertEqual(len(result["callable"]), 8)
        self.assertEqual(result["inputTarget"], "s4")
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
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        config_patch = patch.object(
            web_config, "CONFIG_PATH", str(Path(self.temp_dir.name) / "config.json")
        )
        config_patch.start()
        def restore_config_path():
            config_patch.stop()
            api._refresh_runtime_config()

        self.addCleanup(restore_config_path)
        api._refresh_runtime_config()
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir()
        self.state_path = Path(self.temp_dir.name) / "runtime_state.json"
        patcher = patch.object(
            web_runtime_state, "RUNTIME_STATE_PATH", str(self.state_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_the_dialog_offers_the_two_sides_as_drawn_cards(self):
        """Two radio cards, like the launcher's layout cards: each draws the
        workspace with the dashboard column on its own edge, so the thumbnail
        is the answer and the words only confirm it. Both pages carry it."""
        for path in ("/", "/terminals"):
            with self.subTest(page=path):
                page = self.client.get(path).get_data(as_text=True)
                start = page.index('id="appAgentSidebarSide"')
                group = page[start:page.index("</fieldset>", start)]
                self.assertNotIn("<select", group)
                self.assertIn("<legend>Agent Dashboard Side</legend>", group)
                radios = re.findall(
                    r'<input type="radio" name="appAgentSidebarSide" value="(\w+)"',
                    group,
                )
                self.assertEqual(radios, ["left", "right"])

                previews = re.findall(
                    r'<span class="side-preview" data-side="(\w+)" aria-hidden="true">'
                    r'\s*<span class="side-preview-(\w+)">',
                    group,
                )
                # The column comes first on the left card and the panes come
                # first on the right one, so the grid places each on its edge.
                self.assertEqual(
                    previews, [("left", "panel"), ("right", "panes")]
                )
                self.assertEqual(group.count('class="pane"'), 8)
                self.assertIn("<strong>Left side</strong>", group)
                self.assertIn("<strong>Right side</strong>", group)

        css = self.client.get("/static/css/app-settings.css").get_data(as_text=True)
        self.assertRegex(
            css,
            r'\.side-preview\[data-side="left"\] \{\s*grid-template-columns: 15px minmax\(0, 1fr\);',
        )
        self.assertRegex(
            css,
            r'\.side-preview\[data-side="right"\] \{\s*grid-template-columns: minmax\(0, 1fr\) 15px;',
        )
        self.assertIn(".side-choice input:checked + .side-choice-body", css)
        self.assertIn(".side-choice input:focus-visible + .side-choice-body", css)

    @unittest.skipUnless(NODE, "Node.js is required for the side-picker harness")
    def test_the_checked_card_is_what_a_save_carries(self):
        """The real read/write pair and the real collect, run against a stubbed
        radio group: loading a side checks exactly its card, a save states the
        checked one, and anything the page never offered reads as the left."""
        app_settings = (STATIC_JS / "app-settings.js").read_text(encoding="utf-8")
        functions = app_settings[
            app_settings.index("    function isNativeWindowModeAvailable()"):
            app_settings.index("    function syncAutosaveIntervalLabel(")
        ]
        harness = (
            r"""
            const DEFAULT_APP_SETTINGS = { workspace: { autosave_interval_minutes: 5 } };
            const window = { pywebview: null };
            const radios = ['left', 'right'].map(value => ({ value, checked: false }));
            const group = {
                querySelectorAll: selector =>
                    selector === 'input[type="radio"]' ? radios : [],
                querySelector: selector =>
                    selector === 'input[type="radio"]:checked'
                        ? radios.find(radio => radio.checked) || null
                        : null
            };
            let present = true;
            const elements = {
                appSurfaceMode: { value: 'normal' },
                appWorkspaceAutosaveInterval: { value: '5' }
            };
            const document = {
                getElementById: id =>
                    id === 'appAgentSidebarSide' ? (present ? group : null) : (elements[id] || null)
            };
            """
            + functions
            + r"""
            const checked = () => radios.filter(r => r.checked).map(r => r.value);
            const result = {};
            result.nothingChecked = collectWorkspaceSettingsForm().agent_sidebar_side;
            writeAgentSidebarSideChoice('right');
            result.loadedRight = checked();
            result.savedRight = collectWorkspaceSettingsForm().agent_sidebar_side;
            writeAgentSidebarSideChoice('left');
            result.loadedLeft = checked();
            result.savedLeft = collectWorkspaceSettingsForm().agent_sidebar_side;
            writeAgentSidebarSideChoice('top');
            result.loadedBogus = checked();
            radios[1].checked = true;
            radios[0].checked = false;
            result.clickedRight = readAgentSidebarSideChoice();
            present = false;
            writeAgentSidebarSideChoice('right');
            result.noGroup = collectWorkspaceSettingsForm().agent_sidebar_side;
            process.stdout.write(JSON.stringify(result));
            """
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        result = json.loads(completed.stdout)
        self.assertEqual(result["nothingChecked"], "left")
        self.assertEqual(result["loadedRight"], ["right"])
        self.assertEqual(result["savedRight"], "right")
        self.assertEqual(result["loadedLeft"], ["left"])
        self.assertEqual(result["savedLeft"], "left")
        self.assertEqual(result["loadedBogus"], ["left"])
        self.assertEqual(result["clickedRight"], "right")
        self.assertEqual(result["noGroup"], "left")

    def test_the_broadcast_every_open_window_reads_carries_the_side(self):
        app_settings = self.client.get("/static/js/app-settings.js").get_data(
            as_text=True
        )
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

    def test_resize_strip_is_reserved_outside_the_scroller_on_both_edges(self):
        css = self._static("css/agent-dashboard-sidebar.css")
        base = re.search(r"\n\.agent-sidebar \{([^}]*)\}", css).group(1)
        right = re.search(r"body\.agent-sidebar-right \.agent-sidebar \{([^}]*)\}", css).group(1)
        handle = re.search(r"\n\.agent-sidebar-resizer \{([^}]*)\}", css).group(1)
        self.assertIn("--agent-sidebar-resize-strip: 6px;", base)
        self.assertIn("padding-right: var(--agent-sidebar-resize-strip);", base)
        self.assertIn("padding-right: 0;", right)
        self.assertIn("padding-left: var(--agent-sidebar-resize-strip);", right)
        self.assertIn("width: var(--agent-sidebar-resize-strip);", handle)

    def test_the_mark_wears_the_headers_frame_and_the_auto_pin_from_tokens(self):
        """The frame is the pane header's, down to its two colours: the same
        accent and the same override token, so the surfaces cannot disagree. The
        pin is drawn from an attribute, so it is a decoration and never markup."""
        css = self._static("css/agent-dashboard-sidebar.css")
        header = self._static("css/terminals.css")

        def rule(source, selector):
            found = re.search(re.escape(selector) + r" \{([^}]*)\}", source)
            self.assertIsNotNone(found, selector)
            return found.group(1)

        frame = rule(css, ".agent-sidebar-list .dash-agent-icon[data-mcp]")
        header_frame = rule(header, ".terminal-agent-icon[data-mcp]")
        self.assertIn("outline: 1.5px solid var(--gv-accent);", frame)
        # `--t-accent` is `--gv-accent`: the header's own spelling of the same token.
        self.assertIn("outline: 1.5px solid var(--t-accent);", header_frame)
        self.assertIn("outline-offset: 1.5px;", frame)
        self.assertIn("outline-offset: 1.5px;", header_frame)
        self.assertIn(
            "outline-color: var(--gv-mcp-override);",
            rule(css, ".agent-sidebar-list .dash-agent-icon[data-mcp][data-mcp-override]"),
        )
        pin = rule(css, ".agent-sidebar-list .dash-agent-icon[data-auto]::after")
        self.assertIn("content: 'A';", pin)
        self.assertIn("position: absolute;", pin)
        self.assertIn("border: 1px solid var(--gv-warning);", pin)
        # The chip is gone from the column's stylesheet.
        self.assertNotIn(".agent-sidebar-list .dash-tag", css)

    def test_the_column_states_no_palette_of_its_own(self):
        """Guardrail 7: every colour is a shared token, the same ones the dialog
        reads, so the panel follows the page's light/dark preference without
        this stylesheet knowing which page it is on."""
        css = self._static("css/agent-dashboard-sidebar.css")

        for literal in re.findall(r"#[0-9a-fA-F]{3,8}\b", css):
            self.fail(f"palette literal in agent-dashboard-sidebar.css: {literal}")
        self.assertNotIn("rgb(", css)

    def test_the_input_target_ring_is_the_panes_own_accent_in_both_themes(self):
        """The row wears the ring its terminal wears: the accent token both
        pages theme, inset and not a fill, so it stays apart from the hover and
        keyboard-focus wash. The token is defined for the dark root and again
        for the light theme, so the ring follows the theme with the pane."""
        css = self._static("css/agent-dashboard-sidebar.css")
        rule = re.search(
            r"\.agent-sidebar-list \.dash-agent\.is-input-target\s*\{([^}]*)\}", css
        )
        self.assertIsNotNone(rule)
        self.assertIn("box-shadow: inset 0 0 0 2px var(--gv-accent)", rule.group(1))
        self.assertNotIn("background", rule.group(1))

        tokens = self._static("css/tokens.css")
        dark = tokens[tokens.index(":root {"):]
        light = tokens[tokens.index('[data-theme="light"] {'):]
        self.assertRegex(dark[:dark.index("}")], r"--gv-accent:\s*#")
        self.assertRegex(light[:light.index("}")], r"--gv-accent:\s*#")
        # The terminal's active border reads the same token.
        self.assertIn("--t-accent: var(--gv-accent);", self._static("css/terminals.css"))

    def test_the_crew_gutter_highlight_and_chip_are_drawn_as_planned(self):
        """The gutter is more padding behind `has-crews` only, as wide as the
        lanes in use say (`--dash-wire-gutter`, with a first-paint fallback). The
        highlight dims a row's *contents*, never the row, so the input-target
        ring (the row's own box-shadow) is never dimmed, and a crew row's ring
        gives way to it. The chip is accent on accent-soft, mono 9.5px/600."""
        css = self._static("css/agent-dashboard-sidebar.css")

        def rule(selector, sheet=css):
            found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", sheet)
            self.assertIsNotNone(found, selector)
            return found.group(1)

        self.assertIn(
            "padding-left: var(--dash-wire-gutter, 16px)",
            rule(".agent-sidebar-list.has-crews .agent-sidebar-body"),
        )
        # With no crew the column keeps its own padding, whatever a stale
        # property says: the gutter rule is behind `has-crews` alone.
        self.assertNotIn("--dash-wire-gutter", rule(".agent-sidebar-list .agent-sidebar-body"))
        self.assertIn(
            "opacity: .32",
            rule(".agent-sidebar-list.is-crew-highlight .dash-agent:not(.is-crew-member) > *"),
        )
        self.assertNotRegex(
            css, r"is-crew-highlight \.dash-agent:not\(\.is-crew-member\)\s*\{"
        )
        self.assertIn(
            "inset 0 0 0 1.5px color-mix(in srgb, var(--gv-accent) 75%, transparent)",
            rule(".agent-sidebar-list.is-crew-highlight "
                 ".dash-agent.is-crew-member:not(.is-input-target)"),
        )

        shared = self._static("css/agent-dashboard.css")
        chip = rule(".dash-crew-chip", shared)
        self.assertIn("background: var(--gv-accent-soft)", chip)
        self.assertIn("color: var(--gv-accent)", chip)
        self.assertIn("font: 600 9.5px/1", chip)
        self.assertNotIn("border:", chip)
        self.assertIn("display: none", rule(".dash-agent-crew:empty", shared))

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
