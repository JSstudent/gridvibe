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
- **One block per agent, not one per pane.** Several panes running the same
  agent on the same shell are one heading and one line each, because the
  heading was the part that was identical and the line is what the reader came
  for. Two shells are two headings: a heading is a claim about every line under
  it.
- **The mark says which agent.** Every row wearing the same glyph said only
  "this is a row"; the mark comes from the registry key the name does, and an
  agent GridVibe has not drawn falls back rather than disappearing.
- **A block says what is running without going to look**: the agent's own name,
  what it is running on, what each pane announced, and whether it is working.
- **The state and the percentage are separate readings.** Every agent has a
  state; only one that speaks the progress sequence has a number. A working
  agent with no number still gets a moving bar, so "no percentage" never reads
  as "stalled at 0%". A pane that is not connected reports that instead.
- **Every row is a way out, to the pane it names.** This window is in no
  workspace, so an agent, its session and its workspace all open the window that
  owns it — and an agent row leaves the session tab and the pane waiting for
  that window to claim, because a window that is already open is raised without
  being reloaded and would otherwise land wherever it was left.
- **The window can be left with the keyboard.** Alt+W goes back to the
  workspace that opened it and Alt+Q opens the launcher, the two chords the
  launcher already answers from the same standing.
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
AGENT_GLYPHS_JS = STATIC_JS / "agent-glyphs.js"
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

/* One agent block, read back as the reader meets it: the mark, the heading, the
   shell it claims, and the lines gathered under it. */
function parseAgentGroups() {
    return body().innerHTML
        .split('<div class="dash-agent-group"')
        .slice(1)
        .map(chunk => {
            const scoped = chunk.split('</section>')[0];
            const agent = /^ data-agent="([^"]*)"/.exec(scoped);
            const title = /<span class="dash-agent-title">([\s\S]*?)<\/span>/.exec(scoped);
            const transport = /<span class="dash-tag dash-tag-transport">([\s\S]*?)<\/span>/.exec(scoped);
            const glyph = /<svg class="dash-agent-glyph"[\s\S]*?<\/svg>/.exec(scoped);
            const lines = [];
            const linePattern = /<span class="dash-agent-line">([\s\S]*?)<\/span>/g;
            let line;
            while ((line = linePattern.exec(scoped)) !== null) { lines.push(line[1].trim()); }
            return {
                agent: agent ? agent[1] : '',
                title: title ? title[1].trim() : '',
                transport: transport ? transport[1].trim() : '',
                glyph: glyph ? glyph[0] : '',
                lines
            };
        });
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
        const label = /<span class="dash-(?:agent-line|session-name|workspace-name)">([\s\S]*?)<\/span>/.exec(inner);
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
            tags,
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
            + AGENT_GLYPHS_JS.read_text(encoding="utf-8")
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
    def test_zero_progress_and_stale_or_disconnected_progress(self):
        result = self._run_node(
            """
            const readings = [
                pane({ activity: activity({ progress_state: 'normal', progress_value: 0 }) }),
                pane({ activity: activity({ progress_state: 'normal', progress_value: 65, progress_fresh: false }) }),
                pane({ status: 'disconnected', activity: activity({ progress_state: 'normal', progress_value: 65 }) }),
                pane({ status: 'connecting', activity: activity({ progress_state: 'normal', progress_value: 65 }) })
            ];
            report(readings.map(p => dashboardActivityHtml(p)));
            """
        )
        self.assertIn('width:0%', result[0])
        self.assertIn('>0%</span>', result[0])
        for html in result[1:]:
            self.assertNotIn('dash-progress-fill', html)

    def test_chat_changes_and_idle_ages_update_rows_without_rebuilding_buttons(self):
        result = self._run_node(
            """
            const first = pane({ activity: activity({ title: 'First chat', state: 'idle', idle_seconds: 5 }) });
            fetchAnswer = snapshot([group([first])]);
            await refreshAgentDashboard();
            const originalHtml = body().innerHTML;
            const line = { textContent: 'First chat' };
            const reading = { innerHTML: dashboardActivityHtml(first) };
            const row = { dataset: { sessionId: 's1' }, title: 'First chat', querySelector: selector =>
                selector === '.dash-agent-line' ? line : reading };
            body().querySelectorAll = () => [row];
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
        self.assertEqual(result["tooltip"], "Renamed chat")
        self.assertIn("Idle 1m", result["reading"])
        self.assertEqual(result["scroll"], 123)

    def test_a_hidden_window_aborts_its_request_and_ignores_the_late_result(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
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

    def test_a_block_says_what_is_running_without_going_to_look(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({
                activity: activity({ title: 'Claude: fixing the parser' })
            })])]);
            wireAgentDashboard();
            await settle();
            report({ groups: parseAgentGroups(), row: rowFor('pane:s1') });
            """
        )
        block = result["groups"][0]
        self.assertEqual(block["title"], "Claude Code")
        self.assertEqual(block["transport"], "SSH")
        self.assertEqual(block["agent"], "claude")
        self.assertIn("<svg", block["glyph"])
        # The line is the pane's own half of the answer, and the only half.
        self.assertEqual(block["lines"], ["Claude: fixing the parser"])
        self.assertEqual(result["row"]["label"], "Claude: fixing the parser")
        self.assertEqual(result["row"]["tags"], [])

    def test_several_panes_of_one_agent_are_one_heading_and_a_line_each(self):
        """The complaint the block layout answers: four panes running Claude
        used to be four copies of "Claude Code / POWERSHELL" with one useful
        line apiece."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({ mode: 'wsl', use_powershell: true, host: 'PowerShell',
                       activity: activity({ title: 'Agent dashboard todos' }) }),
                pane({ session_id: 's2', index: 1, mode: 'wsl', use_powershell: true,
                       host: 'PowerShell',
                       activity: activity({ title: 'Button in both windows' }) })
            ])]);
            wireAgentDashboard();
            await settle();
            report(parseAgentGroups());
            """
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["title"], "Claude Code")
        self.assertEqual(result[0]["transport"], "PowerShell")
        self.assertEqual(
            result[0]["lines"], ["Agent dashboard todos", "Button in both windows"]
        )

    def test_one_agent_on_two_shells_is_two_headings(self):
        """A heading is a claim about every line under it: two shells are two
        machines as far as the work is concerned, and folding them under one
        would state something false."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({ mode: 'wsl', use_powershell: true, host: 'PowerShell', directory: 'C:/repo' }),
                pane({ session_id: 's2', index: 1, mode: 'wsl', use_wsl: true,
                       distribution: 'Ubuntu', host: 'wsl', directory: '/srv' })
            ])]);
            wireAgentDashboard();
            await settle();
            report(parseAgentGroups().map(entry => [entry.title, entry.transport, entry.lines]));
            """
        )
        self.assertEqual(
            result,
            [
                ["Claude Code", "PowerShell", ["C:/repo"]],
                ["Claude Code", "WSL · Ubuntu", ["/srv"]],
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
            wireAgentDashboard();
            await settle();
            report(parseAgentGroups().map(entry => ({
                agent: entry.agent, title: entry.title, glyph: entry.glyph
            })));
            """
        )
        self.assertEqual(
            [entry["agent"] for entry in result], ["claude", "codex", "default"]
        )
        self.assertEqual(
            [entry["title"] for entry in result],
            ["Claude Code", "OpenAI Codex CLI", "house-agent"],
        )
        # Three different marks, and the agent with no mark of its own still
        # gets one rather than an empty chip.
        self.assertEqual(len({entry["glyph"] for entry in result}), 3)
        for entry in result:
            self.assertIn("<svg", entry["glyph"])

    def test_a_line_with_nothing_announced_falls_back_to_where_it_points(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane()])]);
            wireAgentDashboard();
            await settle();
            report(rowFor('pane:s1').label);
            """
        )
        self.assertEqual(result, "10.0.0.5: /srv/app")

    def test_the_active_chat_title_outranks_a_pane_label(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([pane({
                title: 'release cut',
                activity: activity({ title: 'Claude: fixing the parser' })
            })])]);
            wireAgentDashboard();
            await settle();
            report({ line: rowFor('pane:s1').label, heading: parseAgentGroups()[0].title });
            """
        )
        self.assertEqual(result["line"], "Claude: fixing the parser")
        self.assertEqual(result["heading"], "Claude Code")

    def test_a_local_agent_is_tagged_with_the_shell_it_runs(self):
        """And the heading's tag is the only place that is said.

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
            wireAgentDashboard();
            await settle();
            report({
                groups: parseAgentGroups().map(entry => entry.transport),
                lines: [rowFor('pane:s1').label, rowFor('pane:s2').label]
            });
            """
        )
        self.assertEqual(result["groups"], ["WSL · Ubuntu", "PowerShell"])
        self.assertEqual(result["lines"], ["C:/repo", "C:/repo"])

    def test_auto_approval_is_marked_on_the_pane_that_has_it(self):
        """It is a per-pane property, so it rides the line rather than the
        heading: two panes of one agent need not have been launched alike."""
        result = self._run_node(
            """
            fetchAnswer = snapshot([group([
                pane({ agent_auto_mode: true }),
                pane({ session_id: 's2', index: 1 })
            ])]);
            wireAgentDashboard();
            await settle();
            report({
                auto: rowFor('pane:s1').tags,
                plain: rowFor('pane:s2').tags,
                headings: parseAgentGroups().length
            });
            """
        )
        self.assertEqual(result["auto"], ["auto"])
        self.assertEqual(result["plain"], [])
        self.assertEqual(result["headings"], 1)

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
            report({ html: body().innerHTML, line: rowFor('pane:s1').label });
            """
        )
        self.assertNotIn("<img", result["html"])
        self.assertIn("&lt;img", result["line"])


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
            wireAgentDashboard();
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
            wireAgentDashboard();
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


class DashboardWindowChordTestCase(DashboardWindowTestCase):
    """The two ways out, driven through the page's own keydown listener.

    Without them the dashboard was the one GridVibe window that could only be
    left with the mouse. Both answer the launcher's chords, because this window
    stands where the launcher does: about workspaces, in none of them.
    """

    def _press(self, event: str, extra: str = ""):
        return self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            await settle();
            %s
            let prevented = false;
            document.fire('keydown', Object.assign(
                { altKey: false, ctrlKey: false, metaKey: false, shiftKey: false,
                  repeat: false, preventDefault() { prevented = true; } },
                %s
            ));
            await settle();
            report({
                prevented,
                workspaceReturns: calls.workspaceReturns,
                launcherOpens: calls.launcherOpens,
                notice: { hidden: notice().hidden, text: notice().textContent }
            });
            """
            % (extra, event)
        )

    def test_alt_w_goes_back_to_the_workspace_this_window_came_from(self):
        result = self._press("{ altKey: true, code: 'KeyW' }")
        self.assertTrue(result["prevented"])
        self.assertEqual(result["workspaceReturns"], 1)
        self.assertEqual(result["launcherOpens"], 0)
        self.assertTrue(result["notice"]["hidden"])

    def test_alt_shift_w_means_the_same_thing(self):
        # There is no cycle to run backwards from a window that is not a
        # workspace, so both directions are "go back" - the launcher's rule.
        result = self._press("{ altKey: true, shiftKey: true, code: 'KeyW' }")
        self.assertEqual(result["workspaceReturns"], 1)

    def test_alt_q_opens_the_launcher(self):
        result = self._press("{ altKey: true, code: 'KeyQ' }")
        self.assertTrue(result["prevented"])
        self.assertEqual(result["launcherOpens"], 1)
        self.assertEqual(result["workspaceReturns"], 0)

    def test_altgr_types_a_character_rather_than_leaving_the_window(self):
        # AltGr reaches the page as Ctrl+Alt on Windows, and AltGr+Q types a
        # backslash on several layouts.
        result = self._press("{ altKey: true, ctrlKey: true, code: 'KeyQ' }")
        self.assertFalse(result["prevented"])
        self.assertEqual(result["launcherOpens"], 0)
        self.assertEqual(result["workspaceReturns"], 0)

    def test_nowhere_to_go_back_to_is_said_rather_than_silently_ignored(self):
        result = self._press(
            "{ altKey: true, code: 'KeyW' }",
            "workspaceReturnOutcome = WORKSPACE_RETURN_NONE;",
        )
        self.assertFalse(result["notice"]["hidden"])
        self.assertEqual(
            result["notice"]["text"], "No workspace is open to switch back to."
        )

    def test_a_blocked_tab_is_reported_in_the_wording_that_owns_it(self):
        result = self._press(
            "{ altKey: true, code: 'KeyW' }",
            "workspaceReturnOutcome = WORKSPACE_RETURN_BLOCKED;",
        )
        self.assertEqual(result["notice"]["text"], WORKSPACE_TAB_BLOCKED_HINT)

    def test_a_launcher_that_could_not_be_opened_says_so_too(self):
        result = self._press(
            "{ altKey: true, code: 'KeyQ' }", "launcherOpens = false;"
        )
        self.assertEqual(result["launcherOpens"], 1)
        self.assertEqual(result["notice"]["text"], WORKSPACE_TAB_BLOCKED_HINT)

    def test_a_held_key_queues_one_return_and_not_a_burst(self):
        result = self._run_node(
            """
            fetchAnswer = snapshot();
            wireAgentDashboard();
            await settle();
            const press = () => document.fire('keydown', {
                altKey: true, ctrlKey: false, metaKey: false, shiftKey: false,
                repeat: false, code: 'KeyW', preventDefault() {}
            });
            press();
            press();
            press();
            await settle();
            report(calls.workspaceReturns);
            """
        )
        self.assertEqual(result, 1)


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
            report({ unchanged, afterChange: body().innerHTML.includes('the reader was here'), line: rowFor('pane:s1').label });
            """
        )
        self.assertTrue(result["unchanged"])
        self.assertFalse(result["afterChange"])
        self.assertEqual(result["line"], "now doing something else")

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
        self.assertIn("Use Refresh to retry", result["failed"]["text"])
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
