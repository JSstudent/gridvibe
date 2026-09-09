"""Ending a session or a workspace from the agent dashboard.

`dashboard-close.js` takes every DOM, bridge, dialog and network touch from an
injected runtime, so the rules that matter here are *executed* in Node rather
than asserted as source text -- and every one of them is an ordering or a
refusal, which is exactly the kind of rule a source-text assertion cannot pin.

What is pinned, and why each would be silent if it broke:

- **Save runs before delete, and a failed save cancels the close.** The whole
  point of *Save and close* is to not lose the group. A DELETE first would
  leave nothing to snapshot; closing anyway after a failed save costs exactly
  what the button was pressed to preserve.
- **Cancel sends nothing at all.** Not a save, not a delete, not a refresh.
- **One press is one request.** These rows are rebuilt every couple of seconds
  by the poll, so a guard living on the pressed node is a guard a repaint can
  drop -- and dropping it means a second DELETE.
- **Close workspace window is withheld in browser mode**, not disabled: a tab
  cannot close another tab, and the shared helper's fallback is
  `window.close()`, which from here would close the dashboard itself.
- **A close reports only what it could not do**, with one exception: closing a
  window changes nothing on this page, so that one says it worked.

The second class renders the real `dashboard-dialog.js` over the real
`dashboard-close.js` and reads the controls back out of the markup, because
where the × sits is itself a rule: a button inside a button is not a control.
It opens the dialog first, because the reading is only fetched while it is
up.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
DASHBOARD_CLOSE_JS = STATIC_JS / "dashboard-close.js"
AGENT_IDENTITY_JS = STATIC_JS / "agent-identity.js"
AGENT_GLYPHS_JS = STATIC_JS / "agent-glyphs.js"
DASHBOARD_DIALOG_JS = STATIC_JS / "dashboard-dialog.js"

NODE = shutil.which("node")

# Every collaborator the controller reaches for, recorded rather than
# simulated: what these cases are about is which calls happen and in what
# order, so the runtime is a ledger.
HARNESS_STUBS = """
function harness(options) {
    const settings = options || {};
    const log = [];
    const notices = [];
    const bridge = settings.native === false ? null : {
        close_workspace_window: workspaceId => {
            log.push(`window:${workspaceId}`);
            if (settings.bridgeThrows) throw new Error('bridge exploded');
            return settings.windowAnswer === undefined ? { ok: true } : settings.windowAnswer;
        }
    };
    const controller = dashboardClose.create({
        getBridge: () => bridge,
        fetchJson: async (url, requestOptions) => {
            const method = (requestOptions && requestOptions.method) || 'GET';
            log.push(`${method} ${url}`);
            if (method === 'GET') {
                if (settings.listThrows) throw new Error('offline');
                return {
                    ok: settings.listOk !== false,
                    status: 200,
                    data: { sessions: settings.sessions || [{ status: 'connected' }] }
                };
            }
            if (url.includes('/save')) {
                return settings.saveOk === false
                    ? { ok: false, status: 503, data: { error: 'The window did not flush' } }
                    : { ok: true, status: 200, data: { id: 'preset-1', name: 'API work' } };
            }
            if (settings.deleteThrows) throw new Error('gone');
            /* A close held open, so a second press lands while the first is
               genuinely still in flight — the only state the guard is for. */
            if (settings.holdDelete) await settings.holdDelete.promise;
            return settings.deleteOk === false
                ? { ok: false, status: 400, data: { error: 'Session group not found' } }
                : { ok: true, status: 200, data: {} };
        },
        confirmCloseSession: async request => {
            log.push('confirm:session');
            confirmedSessionRequest = request;
            return settings.decision === undefined ? 'close' : settings.decision;
        },
        skipDecision: sessions => (
            sessions.length > 0 && sessions.every(s => s.status !== 'connected') ? 'close' : null
        ),
        connectedCount: sessions => sessions.filter(s => s.status === 'connected').length,
        /* The prompt's own constants, handed in the way the page hands them in
           — the controller never spells them itself, which a case below
           demonstrates by renaming them. */
        decisions: settings.decisions || { cancel: 'cancel', saveAndClose: 'save-and-close' },
        confirmCloseWorkspace: async workspace => {
            log.push('confirm:workspace');
            confirmedWorkspace = workspace;
            return settings.workspaceConfirmed !== false;
        },
        closeLiveWorkspace: async workspaceId => {
            log.push(`DELETE workspace:${workspaceId}`);
            if (settings.workspaceCloseFails) {
                const error = new Error('Could not close this workspace');
                throw error;
            }
            return { closed: true };
        },
        notifySavedSession: saved => log.push(`broadcast:${saved && saved.id}`),
        notifyWorkspacesChanged: reason => log.push(`workspaces:${reason}`),
        notice: (message, tone) => notices.push({ message, tone: tone || null }),
        refresh: async () => { log.push('refresh'); },
        logError: () => {}
    });
    return { controller, log, notices };
}

function gate() {
    const held = {};
    held.promise = new Promise(resolve => { held.release = resolve; });
    return held;
}

let confirmedSessionRequest = null;
let confirmedWorkspace = null;

const sessionTarget = { workspaceId: 'default', groupId: 'g1', name: 'API work' };
const workspaceTarget = { workspaceId: 'ws-2', label: 'Docs', groupCount: 3 };

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""

# The window harness, for the markup half: the same stub page
# tests/test_dashboard_window.py uses, trimmed to what a close control needs and
# with `dashboard-close.js` actually loaded, which is what makes the controls
# render at all.
WINDOW_STUBS = r"""
function escHtml(value) {
    return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const AGENT_OPTIONS = [{ value: 'claude', label: 'claude', display_name: 'Claude Code' }];
const WORKSPACE_TAB_BLOCKED_HINT = 'Allow pop-ups.';

function workspaceDisplayLabel(workspace, index) {
    return String(workspace && workspace.label ? workspace.label : '')
        || (workspace && workspace.workspace_id === 'default' ? 'Main workspace' : `Workspace ${index + 1}`);
}
function requestWorkspaceFocusTarget() { return true; }
async function openWorkspaceWindow() { return true; }
async function returnToOriginWorkspace() { return { outcome: 'focused', workspaceId: 'default' }; }
async function openLauncherWindow() { return true; }

function fakeListeners() {
    const handlers = new Map();
    return {
        addEventListener(type, handler) {
            if (!handlers.has(type)) handlers.set(type, new Set());
            handlers.get(type).add(handler);
        },
        removeEventListener() {},
        fire(type, event) { [...(handlers.get(type) || [])].forEach(h => h(event)); }
    };
}

function fakeClassList() {
    const names = new Set();
    return {
        names,
        add(n) { names.add(n); },
        remove(n) { names.delete(n); },
        contains(n) { return names.has(n); },
        toggle(n, force) {
            const on = force === undefined ? !names.has(n) : Boolean(force);
            if (on) names.add(n); else names.delete(n);
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
        classList: fakeClassList(),
        attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        focus() {},
        contains: () => false,
        querySelector: () => null,
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

const document = {
    activeElement: null,
    hidden: false,
    getElementById: id => byId.get(id) || null,
    /* The Escape guard asks the page which shells are up; this harness has
       exactly the one. */
    querySelectorAll: () => (
        byId.get('agentDashboardShell').classList.contains('visible')
            ? [byId.get('agentDashboardShell')]
            : []
    ),
    ...fakeListeners()
};

/* The reading is fetched only while the dialog is open, so every case here
   opens it: the controls being asserted do not exist until it is. */
function showDashboard() {
    wireAgentDashboard();
    openAgentDashboardDialog();
}

/* `window` is the real global object so both UMD modules attach themselves
   where the page looks for them — and `document` is published on it too,
   because dashboard-close.js asks `root.document` before it wires anything
   (a page with no document is a require() from Node, not a browser). */
const window = globalThis;
const windowListeners = fakeListeners();
Object.assign(globalThis, {
    document,
    setInterval: () => 1,
    clearInterval: () => {},
    addEventListener: windowListeners.addEventListener,
    removeEventListener: windowListeners.removeEventListener,
    fireWindow: windowListeners.fire
});

/* Swapped per case: `null` is a browser tab, an object is the native window. */
let nativeBridge = null;
Object.defineProperty(globalThis, 'pywebview', {
    get() { return nativeBridge ? { api: nativeBridge } : undefined; },
    configurable: true
});

let fetchAnswer = null;
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => fetchAnswer });

function body() { return byId.get('agentDashboardBody'); }
function notice() { return byId.get('agentDashboardNotice'); }
function settle() { return new Promise(resolve => setTimeout(resolve, 0)); }

/* Every button in the rendered tree, by the action it states. */
function buttons() {
    const found = [];
    const pattern = /<button\b([^>]*)>/g;
    let match;
    while ((match = pattern.exec(body().innerHTML)) !== null) {
        const attributes = {};
        const attributePattern = /([a-zA-Z-]+)="([^"]*)"/g;
        let attribute;
        while ((attribute = attributePattern.exec(match[1])) !== null) {
            attributes[attribute[1]] = attribute[2];
        }
        found.push(attributes);
    }
    return found;
}

function actions() {
    return buttons().map(b => b['data-dashboard-action']).filter(Boolean);
}

function snapshot(overrides) {
    return Object.assign({
        generated_at: 100,
        workspaces: [{
            workspace_id: 'default',
            label: '',
            active_group_id: 'g1',
            group_count: 1,
            live_group_count: 4,
            agent_count: 1,
            groups: [{
                group_id: 'g1',
                workspace_id: 'default',
                name: 'API work',
                is_active: true,
                pane_count: 2,
                agent_count: 1,
                panes: [{
                    session_id: 's1', group_id: 'g1', workspace_id: 'default', index: 0,
                    title: 'Terminal 1', host: '', directory: '/srv', mode: 'wsl',
                    startup_mode: 'agent', agent_selection: 'claude', custom_agent: '',
                    agent_auto_mode: false, use_wsl: true, use_powershell: false,
                    distribution: 'Ubuntu', status: 'connected', activity: null
                }]
            }]
        }],
        totals: { workspaces: 1, sessions: 1, agents: 1 }
    }, overrides || {});
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard close tests")
class DashboardCloseNodeTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + "\nconst dashboardClose = require(process.argv[2]);\n"
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        return self._execute(script, [str(DASHBOARD_CLOSE_JS)])

    def _execute(self, script: str, args):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class DashboardCloseSessionTestCase(DashboardCloseNodeTestCase):
    def test_a_plain_close_asks_then_deletes_and_reports_nothing(self):
        result = self._run_node(
            """
            const page = harness();
            const closed = await page.controller.closeSession(sessionTarget);
            report({ closed, log: page.log, notices: page.notices, asked: confirmedSessionRequest });
            """
        )
        self.assertTrue(result["closed"])
        self.assertEqual(
            result["log"],
            [
                "GET /api/sessions?workspace_id=default&group=g1",
                "confirm:session",
                "DELETE /api/sessions?workspace_id=default&group=g1",
                # The launcher has no socket of its own, so this window tells it.
                "workspaces:group_closed",
                "refresh",
            ],
        )
        # A closed row disappears on the next read, which says more than a
        # message would; the only write to the notice line is the clear.
        self.assertEqual(result["notices"], [{"message": "", "tone": None}])
        self.assertEqual(result["asked"]["connectedCount"], 1)
        self.assertEqual(result["asked"]["group"]["name"], "API work")
        self.assertEqual(result["asked"]["totalCount"], 1)

    def test_save_and_close_saves_first_and_a_failed_save_keeps_the_session(self):
        result = self._run_node(
            """
            const good = harness({ decision: 'save-and-close' });
            const savedThenClosed = await good.controller.closeSession(sessionTarget);

            const bad = harness({ decision: 'save-and-close', saveOk: false });
            const refused = await bad.controller.closeSession(sessionTarget);

            report({
                savedThenClosed,
                goodLog: good.log,
                refused,
                badLog: bad.log,
                badNotices: bad.notices
            });
            """
        )
        self.assertTrue(result["savedThenClosed"])
        self.assertEqual(
            result["goodLog"],
            [
                "GET /api/sessions?workspace_id=default&group=g1",
                "confirm:session",
                # The preset is written before anything is torn down, and the
                # launcher is told a preset exists.
                "POST /api/session-groups/g1/save",
                "broadcast:preset-1",
                "DELETE /api/sessions?workspace_id=default&group=g1",
                "workspaces:group_closed",
                "refresh",
            ],
        )
        # A requested save that failed must not cost the terminals it was meant
        # to preserve: no DELETE, no refresh, and the server's own reason.
        self.assertFalse(result["refused"])
        self.assertNotIn("DELETE /api/sessions?workspace_id=default&group=g1", result["badLog"])
        self.assertNotIn("refresh", result["badLog"])
        self.assertEqual(
            result["badNotices"], [{"message": "The window did not flush", "tone": "error"}]
        )

    def test_the_decisions_are_the_prompts_own_and_never_spelled_here(self):
        """A second copy of 'save-and-close' compares equal today and stops
        matching, in silence, the day the prompt's constant changes — and the
        failure mode is a save that is simply skipped on the way to a close.
        Renaming both decisions must change nothing."""
        result = self._run_node(
            """
            const renamed = { cancel: 'keep-it', saveAndClose: 'preserve-then-close' };

            const saving = harness({ decisions: renamed, decision: 'preserve-then-close' });
            const saved = await saving.controller.closeSession(sessionTarget);

            const keeping = harness({ decisions: renamed, decision: 'keep-it' });
            const kept = await keeping.controller.closeSession(sessionTarget);

            report({ saved, savingLog: saving.log, kept, keepingLog: keeping.log });
            """
        )
        self.assertTrue(result["saved"])
        self.assertIn("POST /api/session-groups/g1/save", result["savingLog"])
        self.assertFalse(result["kept"])
        self.assertNotIn(
            "DELETE /api/sessions?workspace_id=default&group=g1", result["keepingLog"]
        )

    def test_cancel_sends_nothing_and_a_dead_session_is_never_asked_about(self):
        result = self._run_node(
            """
            const cancelled = harness({ decision: 'cancel' });
            const keptOnCancel = await cancelled.controller.closeSession(sessionTarget);

            const dead = harness({ sessions: [{ status: 'disconnected' }] });
            const closedWithoutAsking = await dead.controller.closeSession(sessionTarget);

            report({
                keptOnCancel,
                cancelledLog: cancelled.log,
                closedWithoutAsking,
                deadLog: dead.log
            });
            """
        )
        self.assertFalse(result["keptOnCancel"])
        self.assertEqual(
            result["cancelledLog"],
            ["GET /api/sessions?workspace_id=default&group=g1", "confirm:session"],
        )
        self.assertTrue(result["closedWithoutAsking"])
        self.assertNotIn("confirm:session", result["deadLog"])

    def test_a_failed_close_says_why_and_does_not_claim_the_session_is_gone(self):
        result = self._run_node(
            """
            const refused = harness({ deleteOk: false });
            const byServer = await refused.controller.closeSession(sessionTarget);

            const offline = harness({ deleteThrows: true });
            const byNetwork = await offline.controller.closeSession(sessionTarget);

            report({
                byServer, serverNotices: refused.notices, serverLog: refused.log,
                byNetwork, networkNotices: offline.notices
            });
            """
        )
        self.assertFalse(result["byServer"])
        self.assertEqual(
            result["serverNotices"],
            [{"message": "Session group not found", "tone": "error"}],
        )
        # Nothing is announced and nothing is re-read when nothing happened.
        self.assertNotIn("workspaces:group_closed", result["serverLog"])
        self.assertNotIn("refresh", result["serverLog"])
        self.assertFalse(result["byNetwork"])
        self.assertEqual(
            result["networkNotices"],
            [{"message": "This session could not be closed.", "tone": "error"}],
        )

    def test_a_status_lookup_that_failed_asks_rather_than_assuming(self):
        result = self._run_node(
            """
            const page = harness({ listThrows: true });
            const closed = await page.controller.closeSession(sessionTarget);
            report({ closed, log: page.log, asked: confirmedSessionRequest });
            """
        )
        self.assertTrue(result["closed"])
        self.assertIn("confirm:session", result["log"])
        # Nothing could be established, so nothing is claimed in the copy.
        self.assertEqual(result["asked"]["totalCount"], 0)

    def test_a_second_press_while_the_first_is_running_sends_nothing(self):
        """The poll rebuilds these rows every couple of seconds, so the guard
        cannot live on the node that was pressed: it is module state, and the
        second press is refused while the first close is genuinely in flight."""
        result = self._run_node(
            """
            const held = gate();
            const page = harness({ holdDelete: held });
            const first = page.controller.closeSession(sessionTarget);
            /* The macrotask runs once every microtask of the first press has,
               so by here it is parked inside its own DELETE. */
            await new Promise(resolve => setTimeout(resolve, 0));
            const second = await page.controller.closeSession(sessionTarget);
            held.release();
            report({
                first: await first,
                second,
                deletes: page.log.filter(entry => entry.startsWith('DELETE')).length,
                confirms: page.log.filter(entry => entry === 'confirm:session').length
            });
            """
        )
        self.assertTrue(result["first"])
        self.assertFalse(result["second"])
        self.assertEqual(result["deletes"], 1)
        # And the refusal is not a silent second prompt either: the row that is
        # already closing never asks again.
        self.assertEqual(result["confirms"], 1)

    def test_a_row_that_names_no_group_does_nothing(self):
        result = self._run_node(
            """
            const page = harness();
            report({
                noGroup: await page.controller.closeSession({ workspaceId: 'default' }),
                noWorkspace: await page.controller.closeSession({ groupId: 'g1' }),
                nothing: await page.controller.closeSession(null),
                log: page.log
            });
            """
        )
        self.assertFalse(result["noGroup"])
        self.assertFalse(result["noWorkspace"])
        self.assertFalse(result["nothing"])
        self.assertEqual(result["log"], [])


class DashboardCloseWorkspaceTestCase(DashboardCloseNodeTestCase):
    def test_closing_a_workspace_confirms_with_what_it_actually_ends(self):
        result = self._run_node(
            """
            const page = harness();
            const closed = await page.controller.closeWorkspace(workspaceTarget);
            report({ closed, log: page.log, confirmed: confirmedWorkspace, notices: page.notices });
            """
        )
        self.assertTrue(result["closed"])
        self.assertEqual(
            result["log"], ["confirm:workspace", "DELETE workspace:ws-2", "refresh"]
        )
        # The band lists only agent-bearing sessions; the confirmation states
        # every live one, because that is what closing the workspace ends.
        self.assertEqual(
            result["confirmed"],
            {"workspace_id": "ws-2", "label": "Docs", "group_count": 3},
        )

    def test_declining_the_workspace_confirm_closes_nothing(self):
        result = self._run_node(
            """
            const page = harness({ workspaceConfirmed: false });
            const closed = await page.controller.closeWorkspace(workspaceTarget);
            report({ closed, log: page.log });
            """
        )
        self.assertFalse(result["closed"])
        self.assertEqual(result["log"], ["confirm:workspace"])

    def test_a_workspace_that_could_not_be_closed_says_so(self):
        result = self._run_node(
            """
            const page = harness({ workspaceCloseFails: true });
            const closed = await page.controller.closeWorkspace(workspaceTarget);
            report({ closed, notices: page.notices, log: page.log });
            """
        )
        self.assertFalse(result["closed"])
        self.assertEqual(
            result["notices"],
            [{"message": "Could not close this workspace", "tone": "error"}],
        )
        self.assertNotIn("refresh", result["log"])


class DashboardCloseWindowTestCase(DashboardCloseNodeTestCase):
    def test_the_window_verb_exists_only_where_a_window_can_be_closed(self):
        """A browser tab cannot close another tab, and the shared helper falls
        back to `window.close()` — which from here would close the dashboard."""
        result = self._run_node(
            """
            const native = harness();
            const browser = harness({ native: false });
            report({
                nativeAvailable: native.controller.canCloseWindow(),
                browserAvailable: browser.controller.canCloseWindow(),
                nativeControls: dashboardClose.policy
                    .workspaceControls({}, { native: true }).map(c => c.action),
                browserControls: dashboardClose.policy
                    .workspaceControls({}, { native: false }).map(c => c.action),
                browserRun: await browser.controller.closeWorkspaceWindow({ workspaceId: 'ws-2' }),
                browserLog: browser.log
            });
            """
        )
        self.assertTrue(result["nativeAvailable"])
        self.assertFalse(result["browserAvailable"])
        # Withheld, not disabled — and the harmless verb comes first, so the
        # pointer travels further to reach the one that ends shells.
        self.assertEqual(
            result["nativeControls"], ["close-workspace-window", "close-workspace"]
        )
        self.assertEqual(result["browserControls"], ["close-workspace"])
        self.assertFalse(result["browserRun"])
        self.assertEqual(result["browserLog"], [])

    def test_closing_a_window_says_it_worked_because_nothing_here_changes(self):
        result = self._run_node(
            """
            const page = harness();
            const closed = await page.controller.closeWorkspaceWindow(
                { workspaceId: 'ws-2', label: 'Docs' }
            );
            report({ closed, log: page.log, notices: page.notices });
            """
        )
        self.assertTrue(result["closed"])
        # Nothing live changed, so nothing is re-read and nothing is announced.
        self.assertEqual(result["log"], ["window:ws-2"])
        self.assertEqual(
            result["notices"],
            [
                {
                    "message": "Closed the window for Docs. Its sessions keep running.",
                    "tone": "info",
                }
            ],
        )

    def test_a_workspace_with_no_window_open_is_reported(self):
        result = self._run_node(
            """
            const refused = harness({ windowAnswer: { ok: false, error: 'No workspace window open' } });
            const byBridge = await refused.controller.closeWorkspaceWindow({ workspaceId: 'ws-2' });

            const broken = harness({ bridgeThrows: true });
            const byThrow = await broken.controller.closeWorkspaceWindow({ workspaceId: 'ws-2' });

            report({
                byBridge, bridgeNotices: refused.notices,
                byThrow, throwNotices: broken.notices
            });
            """
        )
        self.assertFalse(result["byBridge"])
        self.assertEqual(
            result["bridgeNotices"],
            [{"message": "No workspace window open", "tone": "error"}],
        )
        self.assertFalse(result["byThrow"])
        self.assertEqual(
            result["throwNotices"],
            [{"message": "That workspace window could not be closed.", "tone": "error"}],
        )


class DashboardCloseDispatchTestCase(DashboardCloseNodeTestCase):
    def test_only_the_three_close_verbs_are_claimed_from_the_shared_listener(self):
        """The close verbs and the open verbs share one delegated listener, so
        what separates them is the action a row states — never which element it
        is."""
        result = self._run_node(
            """
            const page = harness();
            report({
                session: page.controller.handles('close-session'),
                workspace: page.controller.handles('close-workspace'),
                window: page.controller.handles('close-workspace-window'),
                openPane: page.controller.handles('pane'),
                openSession: page.controller.handles('session'),
                openWorkspace: page.controller.handles('workspace'),
                nothing: page.controller.handles(''),
                unknownRun: await page.controller.run('pane', sessionTarget),
                log: page.log
            });
            """
        )
        self.assertTrue(result["session"])
        self.assertTrue(result["workspace"])
        self.assertTrue(result["window"])
        # The three navigation actions stay with openDashboardTarget().
        self.assertFalse(result["openPane"])
        self.assertFalse(result["openSession"])
        self.assertFalse(result["openWorkspace"])
        self.assertFalse(result["nothing"])
        self.assertFalse(result["unknownRun"])
        self.assertEqual(result["log"], [])


@unittest.skipUnless(NODE, "Node.js is required for the dashboard close tests")
class DashboardCloseControlsTestCase(DashboardCloseNodeTestCase):
    """Where the controls sit, read back out of the real rendered markup."""

    def _run_window(self, body: str):
        script = (
            WINDOW_STUBS
            + DASHBOARD_CLOSE_JS.read_text(encoding="utf-8")
            + AGENT_IDENTITY_JS.read_text(encoding="utf-8")
            + AGENT_GLYPHS_JS.read_text(encoding="utf-8")
            + DASHBOARD_DIALOG_JS.read_text(encoding="utf-8")
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        return self._execute(script, [])

    def test_the_session_close_is_a_control_beside_the_heading_never_inside_it(self):
        """A button inside a button is not a control, and the heading already
        has an action of its own — pressing it opens the tab."""
        result = self._run_window(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const html = body().innerHTML;
            const head = html.split('<header class="dash-session-head">')[1].split('</header>')[0];
            report({
                actions: actions(),
                headOpensThenCloses: [
                    head.indexOf('data-dashboard-action="session"'),
                    head.indexOf('data-dashboard-action="close-session"')
                ],
                /* The open control closes before the close control opens: two
                   siblings, never one nested in the other. */
                nested: head.indexOf('</button>') > head.indexOf('data-dashboard-action="close-session"'),
                closeIsSvg: /class="dash-session-close"[\\s\\S]*?<svg/.test(head),
                labelled: /aria-label="Close &quot;API work&quot; — ends its terminals"/.test(head)
            });
            """
        )
        self.assertIn("close-session", result["actions"])
        first, second = result["headOpensThenCloses"]
        self.assertLess(first, second)
        self.assertFalse(result["nested"])
        # A stroke SVG, not a text glyph (guardrail 7).
        self.assertTrue(result["closeIsSvg"])
        self.assertTrue(result["labelled"])

    def test_the_band_offers_one_verb_in_a_browser_and_two_in_the_native_window(self):
        result = self._run_window(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();
            const inBrowser = actions();

            nativeBridge = { close_workspace_window: () => ({ ok: true }) };
            fireWindow('pywebviewready', {});
            await settle();
            const inNative = actions();
            const closes = buttons().find(b => b['data-dashboard-action'] === 'close-workspace');

            report({ inBrowser, inNative, closes });
            """
        )
        self.assertNotIn("close-workspace-window", result["inBrowser"])
        self.assertIn("close-workspace", result["inBrowser"])
        # The bridge arrives after the first paint, and the reading has not
        # changed — so the rendered structure has to be dropped explicitly or
        # the band keeps its browser-mode shape for the life of the window.
        self.assertIn("close-workspace-window", result["inNative"])
        # The confirmation counts every live session, not only the listed ones.
        self.assertEqual(result["closes"]["data-group-count"], "4")
        self.assertEqual(result["closes"]["data-workspace-label"], "Main workspace")
        self.assertIn("is-danger", result["closes"]["class"])

    def test_a_close_row_runs_the_close_and_never_opens_the_window(self):
        result = self._run_window(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();

            const ran = [];
            window.GridVibeDashboardClose = {
                policy: { sessionCloseTitle: name => `Close ${name}` },
                handles: action => action.startsWith('close-'),
                run: (action, target) => { ran.push({ action, target }); return Promise.resolve(true); },
                canCloseWindow: () => false
            };
            let opened = 0;
            openWorkspaceWindow = async () => { opened += 1; return true; };

            const closeRow = buttons().find(b => b['data-dashboard-action'] === 'close-session');
            body().fire('click', {
                target: { closest: () => ({ dataset: {
                    dashboardAction: 'close-session',
                    workspaceId: closeRow['data-workspace-id'],
                    groupId: closeRow['data-group-id'],
                    sessionName: closeRow['data-session-name']
                } }) },
                preventDefault() {}
            });
            await settle();
            report({ ran, opened });
            """
        )
        self.assertEqual(len(result["ran"]), 1)
        self.assertEqual(result["ran"][0]["action"], "close-session")
        self.assertEqual(
            result["ran"][0]["target"],
            {
                "workspaceId": "default",
                "groupId": "g1",
                "name": "API work",
                "label": "",
                "groupCount": 0,
            },
        )
        # A close is never also a navigation: the row that ends a session must
        # not raise the window it was in on the way out.
        self.assertEqual(result["opened"], 0)

    def test_a_confirmation_hands_the_notice_line_back_and_a_failure_does_not(self):
        result = self._run_window(
            """
            fetchAnswer = snapshot();
            showDashboard();
            await settle();

            setAgentDashboardNotice('Closed the window.', 'action', 'info');
            const asInfo = { text: notice().textContent, info: notice().classList.contains('is-info') };
            setAgentDashboardNotice('This session could not be closed.', 'action', 'error');
            const asError = { text: notice().textContent, info: notice().classList.contains('is-info') };
            setAgentDashboardNotice('');
            const cleared = { text: notice().textContent, hidden: notice().hidden, info: notice().classList.contains('is-info') };

            report({ asInfo, asError, cleared });
            """
        )
        self.assertEqual(result["asInfo"]["text"], "Closed the window.")
        self.assertTrue(result["asInfo"]["info"])
        self.assertEqual(result["asError"]["text"], "This session could not be closed.")
        # Severity is the tint, and an error never wears the confirmation hue.
        self.assertFalse(result["asError"]["info"])
        self.assertEqual(result["cleared"]["text"], "")
        self.assertTrue(result["cleared"]["hidden"])
        self.assertFalse(result["cleared"]["info"])


if __name__ == "__main__":
    unittest.main()
