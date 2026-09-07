"""The agent dashboard window, driven through its own wiring.

`dashboard-window.js` is one fetch, one builder and a delegated click, so it is
exercised by *running* it: the real module and the real `agent-identity.js` are
loaded in Node behind a stub page, wired the way the page wires it, and its
markup is parsed back into the rows a reader would see.

What is pinned is what the window is *for*:

- **Three levels, drawn as three things.** A workspace is a band, a session is
  a card inside it, an agent is a row inside that — the complaint about the
  dropdown this replaced was that all three were the same list at different
  indents, so the nesting is asserted as structure and not as padding.
- **An agent row says what is running without going to look**: the agent's own
  name, what it is running on, what it announced, and whether it is working.
- **The state and the percentage are separate readings.** Every agent has a
  state; only one that speaks the progress sequence has a number. A working
  agent with no number still gets a moving bar, so "no percentage" never reads
  as "stalled at 0%". A pane that is not connected reports that instead.
- **Every row is a way out.** This window is in no workspace, so an agent, its
  session and its workspace all open the window that owns it, at that session.
- **Values reach the markup escaped**, so an agent that announces markup in its
  window title cannot rewrite the rows.
- **A repaint that changes nothing is not performed**, because this window
  stays open while it is read: an identical reading must not drop the caret,
  the selection or the scroll. A reading that *did* change puts the caret back
  on the row it was on and the scroller back where it was.
- **A slow answer never repaints over a newer one**, and a failed read leaves
  the last good tree on screen behind a stated notice rather than blanking it.
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
DASHBOARD_WINDOW_JS = STATIC_JS / "dashboard-window.js"

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

const calls = { fetches: 0, openWorkspaceWindow: [] };
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

function fakeElement(id) {
    return {
        id,
        innerHTML: '',
        textContent: '',
        hidden: false,
        scrollTop: 0,
        dataset: {},
        style: {},
        contains: () => focusIsInside,
        querySelector(selector) { focusQueries.push(selector); return focusTarget; },
        ...fakeListeners()
    };
}

const byId = new Map();
[
    'agentDashboardBody',
    'agentDashboardTotals',
    'agentDashboardNotice',
    'agentDashboardRefreshBtn'
].forEach(id => byId.set(id, fakeElement(id)));

const document = {
    activeElement: null,
    hidden: false,
    getElementById: id => byId.get(id) || null,
    ...fakeListeners()
};

/* window is the real global object so the UMD module below can attach itself
   where dashboard-window.js looks for it. */
const window = globalThis;
const timers = { armed: 0, cleared: 0 };
Object.assign(globalThis, {
    /* Real timers would keep the process alive; the cadence itself is not what
       these cases are about, only whether one is armed. */
    setInterval: () => { timers.armed += 1; return timers.armed; },
    clearInterval: () => { timers.cleared += 1; }
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
    const count = pattern => (body().innerHTML.match(pattern) || []).length;
    return {
        workspaces: count(/<section class="dash-workspace">/g),
        sessions: count(/<section class="dash-session">/g),
        agents: count(/class="dash-agent"/g)
    };
}

/* The rendered window, read back as the reader meets it. */
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
        const inner = found[2];
        const label = /<span class="dash-(?:agent-label|session-name|workspace-name)">([\s\S]*?)<\/span>/.exec(inner);
        const note = /<span class="dash-agent-note">([\s\S]*?)<\/span>/.exec(inner);
        const state = /class="dash-activity dash-state-([a-z]+)"/.exec(inner);
        const word = /<span class="dash-state-word">([\s\S]*?)<\/span>/.exec(inner);
        const percent = /<span class="dash-progress-value">(\d+)%<\/span>/.exec(inner);
        const tags = [];
        const tagPattern = /<span class="dash-tag[^"]*">([\s\S]*?)<\/span>/g;
        let tag;
        while ((tag = tagPattern.exec(inner)) !== null) { tags.push(tag[1].trim()); }
        rows.push({
            kind: attributes['data-dashboard-action'] || '',
            key: attributes['data-dashboard-key'] || '',
            dataset,
            label: label ? label[1].trim() : '',
            note: note ? note[1].trim() : '',
            tags,
            hasIcon: inner.includes('dash-agent-glyph'),
            state: state ? state[1] : '',
            word: word ? word[1].trim() : '',
            hasBar: inner.includes('dash-progress-fill'),
            indeterminate: inner.includes('is-indeterminate'),
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

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard window tests")
class DashboardWindowTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + AGENT_IDENTITY_JS.read_text(encoding="utf-8")
            + DASHBOARD_WINDOW_JS.read_text(encoding="utf-8")
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


class DashboardWindowStructureTestCase(DashboardWindowTestCase):
    def test_the_three_levels_are_three_sections(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([
                group([pane(), pane({ session_id: 's2', index: 1 })]),
                group([pane({ session_id: 's3', group_id: 'g2' })], {
                    group_id: 'g2', name: 'Docs', is_active: false
                })
            ]);
            wireAgentDashboard();
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

    def test_an_agent_row_says_what_is_running_without_going_to_look(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'Claude: fixing the parser' })
            })])]);
            wireAgentDashboard();
            await settle();
            report(rowFor('pane:s1'));
            """
        )
        self.assertEqual(result["label"], "Claude Code")
        self.assertEqual(result["note"], "Claude: fixing the parser")
        self.assertEqual(result["tags"], ["SSH"])
        self.assertTrue(result["hasIcon"])

    def test_a_row_with_nothing_announced_falls_back_to_where_it_points(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane()])]);
            wireAgentDashboard();
            await settle();
            report(rowFor('pane:s1').note);
            """
        )
        self.assertEqual(result, "10.0.0.5: /srv/app")

    def test_a_local_agent_is_tagged_with_the_shell_it_runs(self):
        """And the tag is the only place that is said.

        A local pane's `host` field holds the shell it started, so a note that
        also printed it spent half the line saying "PowerShell" twice. A remote
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
            wireAgentDashboard();
            await settle();
            report({
                wsl: rowFor('pane:s1'),
                powershell: rowFor('pane:s2')
            });
            """
        )
        self.assertEqual(result["wsl"]["tags"], ["WSL · Ubuntu"])
        self.assertEqual(result["wsl"]["note"], "C:/repo")
        self.assertEqual(result["powershell"]["tags"], ["PowerShell"])
        self.assertEqual(result["powershell"]["note"], "C:/repo")

    def test_auto_approval_is_marked_and_only_when_it_is_on(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({ agent_auto_mode: true }),
                pane({ session_id: 's2', index: 1 })
            ])]);
            wireAgentDashboard();
            await settle();
            report({ auto: rowFor('pane:s1').tags, plain: rowFor('pane:s2').tags });
            """
        )
        self.assertEqual(result["auto"], ["SSH", "auto"])
        self.assertEqual(result["plain"], ["SSH"])

    def test_the_session_card_says_how_many_of_its_panes_are_agents(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane()], { pane_count: 4, agent_count: 1 })]);
            wireAgentDashboard();
            await settle();
            report({ html: body().innerHTML, active: rowFor('session:g1').tags });
            """
        )
        self.assertIn("1 agent · 3 other panes", result["html"])
        self.assertEqual(result["active"], ["active"])

    def test_the_totals_line_counts_what_the_window_is_about(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane(), pane({ session_id: 's2', index: 1 })])]);
            wireAgentDashboard();
            await settle();
            report(totals().textContent);
            """
        )
        self.assertEqual(result, "2 agents · 1 session · 1 workspace")

    def test_nothing_running_says_so_rather_than_showing_an_empty_frame(self):
        result = self._run_node(
            """
            fetchAnswer = { generated_at: 1, workspaces: [], totals: { workspaces: 0, sessions: 0, agents: 0 } };
            wireAgentDashboard();
            await settle();
            report({ html: body().innerHTML, totals: totals().textContent, rows: parseRows().length });
            """
        )
        self.assertIn("No agents are running.", result["html"])
        self.assertEqual(result["totals"], "Nothing running")
        self.assertEqual(result["rows"], 0)

    def test_an_announced_title_cannot_rewrite_the_rows(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: '<img src=x onerror=alert(1)>' })
            })])]);
            wireAgentDashboard();
            await settle();
            report({ html: body().innerHTML, note: rowFor('pane:s1').note });
            """
        )
        self.assertNotIn("<img", result["html"])
        self.assertIn("&lt;img", result["note"])


class DashboardWindowActivityTestCase(DashboardWindowTestCase):
    def _row(self, overrides: str):
        return self._run_node(
            """
            fetchAnswer = snapshot([group([pane(%s)])]);
            wireAgentDashboard();
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


class DashboardWindowRowActionTestCase(DashboardWindowTestCase):
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
            wireAgentDashboard();
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

    def test_the_dashboard_stays_open_behind_the_window_it_opened(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({ rows: parseRows().length, notice: notice().hidden });
            """
        )
        self.assertEqual(result["rows"], 3)
        self.assertTrue(result["notice"])

    def test_a_blocked_pop_up_is_reported_in_the_one_wording_that_owns_it(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            workspaceOpens = false;
            wireAgentDashboard();
            await settle();
            clickRow('pane:s1');
            await settle();
            report({ hidden: notice().hidden, text: notice().textContent });
            """
        )
        self.assertFalse(result["hidden"])
        self.assertEqual(result["text"], WORKSPACE_TAB_BLOCKED_HINT)


class DashboardWindowRepaintTestCase(DashboardWindowTestCase):
    def test_an_unchanged_reading_is_not_repainted_at_all(self):
        """This window stays open while it is read, so an identical tick must
        not drop the caret, the selection or the scroll."""
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            await settle();
            /* A mark nothing in the module would ever write: it survives only
               if the second reading was not painted over it. */
            body().innerHTML += '<!--the reader was here-->';
            await refreshAgentDashboard();
            const unchanged = body().innerHTML.includes('the reader was here');
            fetchAnswer = snapshot([group([pane({ activity: activity({ title: 'now doing something else' }) })])]);
            await refreshAgentDashboard();
            report({ unchanged, afterChange: body().innerHTML.includes('the reader was here'), note: rowFor('pane:s1').note });
            """
        )
        self.assertTrue(result["unchanged"])
        self.assertFalse(result["afterChange"])
        self.assertEqual(result["note"], "now doing something else")

    def test_a_reading_that_changed_keeps_the_caret_and_the_scroll(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            await settle();
            body().scrollTop = 240;
            focusIsInside = true;
            focusTarget = { focused: false, focus() { this.focused = true; } };
            document.activeElement = { dataset: { dashboardKey: 'pane:s1' } };
            fetchAnswer = snapshot([group([pane({ activity: activity({ title: 'a different thing' }) })])]);
            await refreshAgentDashboard();
            report({ scrollTop: body().scrollTop, queries: focusQueries, refocused: focusTarget.focused });
            """
        )
        self.assertEqual(result["scrollTop"], 240)
        self.assertEqual(result["queries"], ['[data-dashboard-key="pane:s1"]'])
        self.assertTrue(result["refocused"])

    def test_a_slow_answer_never_repaints_over_a_newer_one(self):
        result = self._run_node(
            """
            let pending = null;
            fetchAnswer = () => new Promise(resolve => { pending = resolve; });
            const slow = refreshAgentDashboard();
            const slowResolve = pending;
            fetchAnswer = snapshot([group([pane({ activity: activity({ title: 'the newer reading' }) })])]);
            await refreshAgentDashboard();
            const afterNewer = rowFor('pane:s1').note;
            slowResolve(snapshot([group([pane({ activity: activity({ title: 'the older reading' }) })])]));
            await slow;
            report({ afterNewer, afterSlowLanded: rowFor('pane:s1').note });
            """
        )
        self.assertEqual(result["afterNewer"], "the newer reading")
        self.assertEqual(result["afterSlowLanded"], "the newer reading")

    def test_a_failed_read_says_so_and_keeps_the_last_good_tree(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
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
        self.assertEqual(result["failed"]["text"], "Could not read what is running.")
        self.assertEqual(result["failed"]["rows"], 3)
        self.assertTrue(result["recovered"]["hidden"])
        self.assertEqual(result["recovered"]["rows"], 3)

    def test_a_hidden_window_arms_no_poll_and_reads_again_on_the_way_back(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            await settle();
            const wired = { armed: timers.armed, fetches: calls.fetches };
            document.hidden = true;
            document.fire('visibilitychange');
            await settle();
            const hidden = { armed: timers.armed, cleared: timers.cleared, fetches: calls.fetches };
            document.hidden = false;
            document.fire('visibilitychange');
            await settle();
            report({ wired, hidden, back: { armed: timers.armed, fetches: calls.fetches } });
            """
        )
        self.assertEqual(result["wired"], {"armed": 1, "fetches": 1})
        self.assertEqual(result["hidden"]["armed"], 1)
        self.assertGreaterEqual(result["hidden"]["cleared"], 1)
        self.assertEqual(result["hidden"]["fetches"], 1)
        self.assertEqual(result["back"], {"armed": 2, "fetches": 2})



if __name__ == "__main__":
    unittest.main()
