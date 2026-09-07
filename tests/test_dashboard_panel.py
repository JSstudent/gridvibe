"""The dashboard panel, driven through its own button.

`dashboard.js` is one fetch, one builder and a delegated click, so it is
exercised by *running* it: the real module and the real `agent-identity.js` are
loaded in Node behind a stub page, the panel is opened the way the button opens
it, and its markup is parsed back into the rows a reader would see.

What is pinned is what the panel is *for*:

- **Wiring the page reads once, for the badge, and renders nothing**; rows
  appear only when the panel is opened, and closing takes them back out — a
  closed dashboard holding a stale tree is an answer waiting to be believed.
- **An agent pane is named after its agent** and shows what that agent
  announced, because the whole point of the row is to say what is running
  without going to look.
- **The state and the percentage are separate readings.** Every pane has a
  state; only an agent that speaks the progress sequence has a number. A
  working pane with no number still gets a moving bar, so "no percentage" never
  reads as "stalled at 0%".
- **A row acts where the pane is.** A row in this window's own workspace
  switches to it; a row in another workspace opens that window instead — one
  rule, and the launcher (which is in no workspace) always takes the second
  branch.
- **Values reach the markup escaped**, so an agent that announces markup in its
  window title cannot rewrite the rows.
- **A slow answer never repaints over a newer one**: the panel polls while it is
  open, so an out-of-order response has to be dropped rather than painted.
- **The panel stays inside the window.** Which side it hangs from is a CSS fact
  about where each page's button is; the measured fit is what catches the case
  where that side runs out of room — including the one that shipped, a
  right-anchored panel on a button at the far left of the session bar.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
AGENT_IDENTITY_JS = STATIC_JS / "agent-identity.js"
DASHBOARD_JS = STATIC_JS / "dashboard.js"

NODE = shutil.which("node")

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

/* The page globals dashboard.js reads through `typeof` guards. Declared with
   `let` so a case can put the harness on the launcher (no workspace of its
   own) or in a workspace window. */
let currentWorkspaceId = 'default';
let activeGroupId = 'g1';
let terminals = [];
const calls = { switchGroup: [], openWorkspaceWindow: [], fetches: 0 };

async function switchGroup(groupId) { calls.switchGroup.push(groupId); }
async function openWorkspaceWindow(workspaceId, options) {
    calls.openWorkspaceWindow.push({ workspaceId, options });
    return true;
}
function workspaceDisplayLabel(workspace, index) {
    return String(workspace && workspace.label ? workspace.label : '')
        || (workspace && workspace.workspace_id === 'default' ? 'Main workspace' : `Workspace ${index + 1}`);
}

function fakeClassList() {
    const names = new Set();
    return {
        add: name => names.add(name),
        remove: name => names.delete(name),
        contains: name => names.has(name)
    };
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

/* Geometry that fits comfortably, so the fit is a no-op unless a case moves
   something: the panel hangs below its button, well inside a 1400x900 window. */
function fakeElement(id, rect) {
    return {
        id,
        innerHTML: '',
        textContent: '',
        hidden: false,
        attributes: {},
        dataset: {},
        style: {},
        rect: rect || { left: 100, right: 420, top: 60, bottom: 500 },
        classList: fakeClassList(),
        setAttribute(name, value) { this.attributes[name] = value; },
        focus() { document.activeElement = this; },
        getBoundingClientRect() { return this.rect; },
        /* Focus never sits inside the panel in these cases, so the render's
           refocus path is a no-op and the rows are read as written. */
        contains: () => false,
        querySelector: () => null,
        ...fakeListeners()
    };
}

const byId = new Map();
['dashboardRoot', 'dashboardBtn', 'dashboardPanel', 'dashboardBadge'].forEach(
    id => byId.set(id, fakeElement(id))
);
byId.get('dashboardBtn').rect = { left: 100, right: 140, top: 20, bottom: 58 };

const document = {
    activeElement: null,
    hidden: false,
    getElementById: id => byId.get(id) || null,
    ...fakeListeners()
};

/* window is the real global object so the UMD module below can attach itself
   where dashboard.js looks for it. */
const window = globalThis;
Object.assign(globalThis, {
    addEventListener() {},
    removeEventListener() {},
    innerWidth: 1400,
    innerHeight: 900,
    /* The stylesheet's own cap, which the fit is allowed to lower and never
       raise. */
    getComputedStyle: () => ({ maxHeight: '620px' }),
    /* Real timers would keep the process alive; the cadence itself is not what
       these cases are about. */
    setInterval: () => 1,
    clearInterval: () => {}
});

/* One snapshot, replaced per case. `fetchAnswer` may be swapped for a function
   so a case can fail a request or delay one. */
let fetchAnswer = null;
globalThis.fetch = async () => {
    calls.fetches += 1;
    const answer = typeof fetchAnswer === 'function' ? await fetchAnswer() : fetchAnswer;
    if (answer === null) {
        return { ok: false, status: 500, json: async () => ({}) };
    }
    return { ok: true, status: 200, json: async () => answer };
};

function root() { return byId.get('dashboardRoot'); }
function button() { return byId.get('dashboardBtn'); }
function panel() { return byId.get('dashboardPanel'); }
function badge() { return byId.get('dashboardBadge'); }
function isOpen() { return root().classList.contains('open'); }

/* The root has to answer "is this press inside me" for the outside-press
   dismisser; the default above says no to everything. */
function pressOutside() {
    document.fire('mousedown', { target: 'somewhere-else' });
}

function press() { toggleDashboard({ preventDefault() {}, stopPropagation() {} }); }

/* Let every pending microtask and timer callback run: the panel paints from an
   async fetch, so a case that read innerHTML straight after the press would be
   reading the placeholder. */
function settle() { return new Promise(resolve => setTimeout(resolve, 0)); }

function camel(name) {
    return name.replace(/-([a-z])/g, (_match, letter) => letter.toUpperCase());
}

/* The rendered panel, read back as the reader meets it. */
function parseRows() {
    const rows = [];
    const pattern = /<button\b([^>]*)>([\s\S]*?)<\/button>/g;
    let found;
    while ((found = pattern.exec(panel().innerHTML)) !== null) {
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
        const body = found[2];
        const label = /<span class="dashboard-row-label">([\s\S]*?)<\/span>/.exec(body);
        const note = /<span class="dashboard-row-note">([\s\S]*?)<\/span>/.exec(body);
        const tag = /<span class="dashboard-tag[^"]*">([\s\S]*?)<\/span>/.exec(body);
        const state = /class="dashboard-activity dashboard-state-([a-z]+)"/.exec(body);
        const percent = /<span class="dashboard-progress-value">(\d+)%<\/span>/.exec(body);
        rows.push({
            kind: attributes['data-dashboard-action'] || '',
            key: attributes['data-dashboard-key'] || '',
            dataset,
            label: label ? label[1].trim() : '',
            note: note ? note[1].trim() : '',
            tag: tag ? tag[1].trim() : '',
            state: state ? state[1] : '',
            hasBar: body.includes('dashboard-progress-fill'),
            indeterminate: body.includes('is-indeterminate'),
            percent: percent ? Number(percent[1]) : null
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
    panel().fire('click', {
        target: { closest: () => ({ dataset: row.dataset }) },
        preventDefault() {},
        stopPropagation() {}
    });
}

function pane(overrides) {
    return Object.assign({
        session_id: 's1',
        group_id: 'g1',
        workspace_id: 'default',
        title: 'Terminal 1',
        host: '10.0.0.5',
        directory: '/srv/app',
        mode: 'ssh',
        startup_mode: 'terminal',
        agent_selection: '',
        custom_agent: '',
        status: 'connected',
        activity: null
    }, overrides || {});
}

function snapshot(panes, extra) {
    const list = panes || [pane()];
    return Object.assign({
        generated_at: 100,
        workspaces: [{
            workspace_id: 'default',
            label: '',
            active_group_id: 'g1',
            group_count: 1,
            pane_count: list.length,
            groups: [{
                group_id: 'g1',
                workspace_id: 'default',
                name: 'API work',
                is_active: true,
                pane_count: list.length,
                panes: list
            }]
        }],
        totals: { workspaces: 1, sessions: 1, panes: list.length, agents: 0 }
    }, extra || {});
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard panel tests")
class DashboardPanelTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + AGENT_IDENTITY_JS.read_text(encoding="utf-8")
            + DASHBOARD_JS.read_text(encoding="utf-8")
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

    def test_the_page_load_read_fills_the_badge_and_renders_nothing(self):
        # Wiring the page reads once, because the badge is the reason to open
        # the panel and a badge that is only right after you open it is worse
        # than none. It must not put a row on the page: the panel is closed.
        result = self._run_node(
            """
            fetchAnswer = snapshot([pane({ startup_mode: 'agent', agent_selection: 'claude' })],
                { totals: { workspaces: 1, sessions: 1, panes: 1, agents: 1 } });
            const untouched = { open: isOpen(), html: panel().innerHTML, fetches: calls.fetches };
            wireDashboard();
            await settle();
            const wired = { html: panel().innerHTML, badge: badge().textContent, fetches: calls.fetches };
            press();
            await settle();
            report({ untouched, wired, opened: { open: isOpen(), rows: parseRows().length } });
            """
        )
        self.assertEqual(result["untouched"], {"open": False, "html": "", "fetches": 0})
        self.assertEqual(result["wired"], {"html": "", "badge": "1", "fetches": 1})
        self.assertTrue(result["opened"]["open"])
        self.assertEqual(result["opened"]["rows"], 3)

    def test_closing_takes_the_rows_back_out(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            press();
            await settle();
            const open = { rows: parseRows().length, expanded: button().attributes['aria-expanded'] };
            press();
            report({ open, closed: { html: panel().innerHTML, isOpen: isOpen(), expanded: button().attributes['aria-expanded'] } });
            """
        )
        self.assertEqual(result["open"]["rows"], 3)
        self.assertEqual(result["open"]["expanded"], "true")
        self.assertEqual(result["closed"], {"html": "", "isOpen": False, "expanded": "false"})

    def test_a_press_outside_the_panel_closes_it(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            press();
            await settle();
            const opened = isOpen();
            pressOutside();
            report({ opened, closed: !isOpen() });
            """
        )
        self.assertEqual(result, {"opened": True, "closed": True})

    def test_the_tree_is_one_row_per_workspace_session_and_pane(self):
        rows = self._run_node(
            """
            fetchAnswer = snapshot([
                pane({ session_id: 's1' }),
                pane({ session_id: 's2', title: 'Terminal 2' })
            ]);
            press();
            await settle();
            report(parseRows().map(row => ({ kind: row.kind, label: row.label, tag: row.tag })));
            """
        )
        self.assertEqual(
            [row["kind"] for row in rows], ["workspace", "session", "pane", "pane"]
        )
        self.assertEqual(rows[0]["label"], "Main workspace")
        self.assertEqual(rows[0]["tag"], "this window")
        self.assertEqual(rows[1]["label"], "API work")
        self.assertEqual(rows[1]["tag"], "active")
        self.assertEqual([rows[2]["label"], rows[3]["label"]], ["Terminal 1", "Terminal 2"])

    def test_an_agent_pane_is_named_after_its_agent_and_shows_what_it_announced(self):
        row = self._run_node(
            """
            fetchAnswer = snapshot([pane({
                startup_mode: 'agent',
                agent_selection: 'claude',
                activity: {
                    state: 'working',
                    state_source: 'progress',
                    title: 'Claude: fixing the parser',
                    progress_state: 'normal',
                    progress_value: 65,
                    idle_seconds: 0.2
                }
            })]);
            press();
            await settle();
            report(rowFor('pane:s1'));
            """
        )
        self.assertEqual(row["label"], "Claude Code")
        self.assertEqual(row["note"], "Claude: fixing the parser")
        self.assertEqual(row["tag"], "agent")
        self.assertEqual(row["state"], "working")
        self.assertEqual(row["percent"], 65)
        self.assertFalse(row["indeterminate"])

    def test_working_without_a_percentage_still_moves(self):
        rows = self._run_node(
            """
            fetchAnswer = snapshot([
                pane({ session_id: 'busy', startup_mode: 'agent', agent_selection: 'codex', activity: {
                    state: 'working', state_source: 'progress', title: '',
                    progress_state: 'indeterminate', progress_value: 0, idle_seconds: 0.1
                } }),
                pane({ session_id: 'spinning', startup_mode: 'agent', agent_selection: 'codex', activity: {
                    state: 'working', state_source: 'output', title: '',
                    progress_state: '', progress_value: 0, idle_seconds: 0.4
                } }),
                pane({ session_id: 'waiting', startup_mode: 'agent', agent_selection: 'codex', activity: {
                    state: 'idle', state_source: 'output', title: '',
                    progress_state: '', progress_value: 0, idle_seconds: 240
                } })
            ]);
            press();
            await settle();
            report({
                published: rowFor('pane:busy'),
                inferred: rowFor('pane:spinning'),
                idle: rowFor('pane:waiting')
            });
            """
        )
        # An agent that says "busy, no number" gets a moving bar.
        self.assertEqual(rows["published"]["state"], "working")
        self.assertTrue(rows["published"]["indeterminate"])
        self.assertIsNone(rows["published"]["percent"])
        # One that says nothing at all is working on the strength of its output,
        # and gets the dot without a bar: there is no reading to draw.
        self.assertEqual(rows["inferred"]["state"], "working")
        self.assertFalse(rows["inferred"]["hasBar"])
        # And an idle pane gets neither.
        self.assertEqual(rows["idle"]["state"], "idle")
        self.assertFalse(rows["idle"]["hasBar"])

    def test_a_pane_with_no_transport_shows_where_it_points(self):
        row = self._run_node(
            """
            fetchAnswer = snapshot([pane({
                startup_mode: 'explorer', title: 'Terminal 1', activity: null
            })]);
            press();
            await settle();
            report(rowFor('pane:s1'));
            """
        )
        self.assertEqual(row["tag"], "explorer")
        self.assertEqual(row["note"], "10.0.0.5: /srv/app")
        self.assertEqual(row["state"], "")

    def test_an_announced_title_cannot_rewrite_the_rows(self):
        row = self._run_node(
            """
            fetchAnswer = snapshot([pane({
                startup_mode: 'agent', agent_selection: 'claude',
                activity: {
                    state: 'idle', state_source: 'output',
                    title: '<img src=x onerror=alert(1)>', progress_state: '',
                    progress_value: 0, idle_seconds: 30
                }
            })]);
            press();
            await settle();
            report({ note: rowFor('pane:s1').note, html: panel().innerHTML.includes('<img') });
            """
        )
        self.assertEqual(row["note"], "&lt;img src=x onerror=alert(1)&gt;")
        self.assertFalse(row["html"])

    def test_a_row_in_this_window_switches_to_it_rather_than_opening_another(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([pane({ session_id: 's1' })]);
            currentWorkspaceId = 'default';
            activeGroupId = 'other';
            wireDashboard();
            press();
            await settle();
            clickRow('session:g1');
            await settle();
            report({ switched: calls.switchGroup, opened: calls.openWorkspaceWindow, closed: !isOpen() });
            """
        )
        self.assertEqual(result["switched"], ["g1"])
        self.assertEqual(result["opened"], [])
        self.assertTrue(result["closed"])

    def test_a_row_elsewhere_opens_the_window_that_owns_it(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([pane({ session_id: 's1' })]);
            /* The launcher is in no workspace, so every row is elsewhere. */
            currentWorkspaceId = '';
            wireDashboard();
            press();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({ switched: calls.switchGroup, opened: calls.openWorkspaceWindow });
            """
        )
        self.assertEqual(result["switched"], [])
        self.assertEqual(
            result["opened"],
            [{"workspaceId": "default", "options": {"groupId": "g1"}}],
        )

    def test_the_badge_counts_agents_and_hides_when_there_are_none(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([pane()], { totals: { workspaces: 1, sessions: 1, panes: 1, agents: 3 } });
            press();
            await settle();
            const withAgents = { text: badge().textContent, hidden: badge().hidden };
            fetchAnswer = snapshot([pane()], { totals: { workspaces: 1, sessions: 1, panes: 1, agents: 0 } });
            await refreshDashboard();
            report({ withAgents, without: { text: badge().textContent, hidden: badge().hidden } });
            """
        )
        self.assertEqual(result["withAgents"], {"text": "3", "hidden": False})
        self.assertEqual(result["without"], {"text": "0", "hidden": True})

    def test_a_failed_read_says_so_instead_of_showing_an_empty_tree(self):
        result = self._run_node(
            """
            fetchAnswer = null;
            press();
            await settle();
            report({ rows: parseRows().length, error: panel().innerHTML.includes('dashboard-error') });
            """
        )
        self.assertEqual(result["rows"], 0)
        self.assertTrue(result["error"])

    def test_an_answer_that_arrives_out_of_order_is_dropped(self):
        result = self._run_node(
            """
            let release = null;
            const slow = new Promise(resolve => { release = resolve; });
            fetchAnswer = () => slow;
            press();                       /* the slow read is in flight */
            fetchAnswer = snapshot([pane({ session_id: 'newer', title: 'Newer' })]);
            await refreshDashboard();      /* a newer one lands first */
            release(snapshot([pane({ session_id: 'stale', title: 'Stale' })]));
            await settle();
            report(parseRows().map(row => row.key));
            """
        )
        self.assertIn("pane:newer", result)
        self.assertNotIn("pane:stale", result)

    def test_a_panel_hanging_off_the_left_of_the_window_is_pushed_back_in(self):
        # The shipped regression: the workspace window's button heads the
        # session bar, hard against the left edge, so a right-anchored panel
        # landed entirely outside the window. The CSS anchor is what fixes that
        # case; this is the rule that keeps any *other* one from happening.
        fit = self._run_node(
            """
            report(dashboardPanelFit({
                panelLeft: -210, panelRight: 110,
                panelTop: 74, panelBottom: 694,
                viewportWidth: 1920, viewportHeight: 1040,
                opensUp: false, styleCap: 620
            }));
            """
        )
        self.assertEqual(fit["shiftX"], 218)

    def test_a_panel_running_past_the_right_edge_is_pulled_back_in(self):
        fit = self._run_node(
            """
            report(dashboardPanelFit({
                panelLeft: 1100, panelRight: 1660,
                panelTop: 74, panelBottom: 694,
                viewportWidth: 1400, viewportHeight: 900,
                opensUp: false, styleCap: 620
            }));
            """
        )
        self.assertEqual(fit["shiftX"], -268)

    def test_a_panel_that_fits_is_not_moved_at_all(self):
        fit = self._run_node(
            """
            report(dashboardPanelFit({
                panelLeft: 100, panelRight: 420,
                panelTop: 64, panelBottom: 500,
                viewportWidth: 1400, viewportHeight: 900,
                opensUp: false, styleCap: 620
            }));
            """
        )
        self.assertEqual(fit["shiftX"], 0)

    def test_a_panel_wider_than_the_window_is_pinned_to_the_left_margin(self):
        # Rows are read from their left edge, so losing the right of one costs
        # less than losing the start of one.
        fit = self._run_node(
            """
            report(dashboardPanelFit({
                panelLeft: 40, panelRight: 600,
                panelTop: 64, panelBottom: 500,
                viewportWidth: 400, viewportHeight: 900,
                opensUp: false, styleCap: 620
            }));
            """
        )
        self.assertEqual(fit["shiftX"], -32)

    def test_the_height_is_capped_by_the_room_in_the_direction_it_opened(self):
        heights = self._run_node(
            """
            /* A button at y=700..738 with the stylesheet's 6px gap: opening
               down the panel starts at 744, opening up it ends at 694. */
            const geometry = { panelLeft: 100, panelRight: 420, viewportWidth: 1400, styleCap: 620 };
            const down = Object.assign({}, geometry, { panelTop: 744, panelBottom: 1364, opensUp: false });
            const up = Object.assign({}, geometry, { panelTop: 74, panelBottom: 694, opensUp: true });
            report({
                down: dashboardPanelFit(Object.assign({}, down, { viewportHeight: 900 })).maxHeight,
                up: dashboardPanelFit(Object.assign({}, up, { viewportHeight: 900 })).maxHeight,
                /* Never raised above the stylesheet's own cap. */
                roomy: dashboardPanelFit(Object.assign({}, down, { viewportHeight: 4000 })).maxHeight,
                /* And never collapsed to a sliver in a tiny window. */
                cramped: dashboardPanelFit(Object.assign({}, geometry,
                    { panelTop: 64, panelBottom: 500, viewportHeight: 100, opensUp: false })).maxHeight
            });
            """
        )
        # Down: from where the panel starts (744) to the window's bottom, less
        # the margin -- the 6px gap is already inside `panelTop`.
        self.assertEqual(heights["down"], 900 - 744 - 8)
        self.assertEqual(heights["up"], 620)
        self.assertEqual(heights["roomy"], 620)
        self.assertEqual(heights["cramped"], 140)

    def test_opening_measures_the_panel_and_nudges_it_into_the_window(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            /* A panel anchored off the left of the window, as shipped. */
            panel().rect = { left: -210, right: 110, top: 60, bottom: 500 };
            press();
            await settle();
            const nudged = { transform: panel().style.transform, maxHeight: panel().style.maxHeight };
            press();
            report({ nudged, afterClose: { transform: panel().style.transform, maxHeight: panel().style.maxHeight } });
            """
        )
        self.assertEqual(result["nudged"]["transform"], "translateX(218px)")
        self.assertEqual(result["nudged"]["maxHeight"], "620px")
        # Closing drops the fit with the rows, so the next open measures from
        # the stylesheet rather than from what the last one left behind.
        self.assertEqual(result["afterClose"], {"transform": "", "maxHeight": ""})

    def test_an_empty_server_says_so(self):
        result = self._run_node(
            """
            fetchAnswer = { generated_at: 1, workspaces: [], totals: { workspaces: 0, sessions: 0, panes: 0, agents: 0 } };
            press();
            await settle();
            report({ rows: parseRows().length, empty: panel().innerHTML.includes('dashboard-empty') });
            """
        )
        self.assertEqual(result["rows"], 0)
        self.assertTrue(result["empty"])


if __name__ == "__main__":
    unittest.main()
