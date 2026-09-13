"""Landing where a dashboard row said, and saying what a pane is now running.

Two halves of the same complaint, and both are executed rather than asserted as
source text: the shipped functions are lifted out of ``workspaces.js`` and
``terminals.js`` and run in Node against a stubbed page.

- **The request survives the trip.** A row names a workspace, a session tab and
  one pane. The native bridge raises an already-open workspace window without
  reloading it, so the last two cannot travel in the URL: they are stored, and
  claimed once by the window that arrives. Both ends run here, because a test
  that handed the target straight to the reader could not catch the two ends
  disagreeing about what is stored.
- **A held target waits for the grid that will satisfy it.** A window opened for
  the trip claims the request while its panes are still being built, so the pane
  it names does not exist yet. The target is settled again at the end of every
  load -- under a deadline, because a session closed between the click and the
  arrival must never leave the window waiting for a pane that is not coming.
- **A pane stops naming an agent that left it.** An agent that exits hands its
  terminal back and one started by hand takes it over; both arrive as a status
  broadcast rather than a rebuild, so the header is repainted from the record.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"

NODE = shutil.which("node")


def _js_function_source(script: str, name: str) -> str:
    """Return one top-level JS function's source, brace-matched."""
    start = script.index(f"function {name}(")
    if script[max(0, start - 6):start] == "async ":
        start -= 6
    parameter_depth = 0
    parameter_end = None
    for index in range(script.index("(", start), len(script)):
        if script[index] == "(":
            parameter_depth += 1
        elif script[index] == ")":
            parameter_depth -= 1
            if parameter_depth == 0:
                parameter_end = index
                break
    if parameter_end is None:
        raise AssertionError(f"unbalanced parameters in {name}")
    depth = 0
    for index in range(script.index("{", parameter_end), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def _js_const_source(script: str, name: str) -> str:
    start = script.index(f"const {name} =")
    return script[start:script.index("\n", start)]


class NodeHarnessTestCase(unittest.TestCase):
    def _run_node(self, script: str):
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


# ── The stored side: workspaces.js writes it, the workspace window claims it ──

WORKSPACES_JS = (STATIC_JS / "workspaces.js").read_text(encoding="utf-8")

FOCUS_REQUEST_SOURCE = "\n".join(
    [
        _js_const_source(WORKSPACES_JS, name)
        for name in (
            "WORKSPACE_DEFAULT_ID",
            "WORKSPACE_ID_PATTERN",
            "WORKSPACE_FOCUS_REQUEST_STORAGE_KEY",
            "WORKSPACE_FOCUS_REQUEST_TTL_MS",
        )
    ]
    + [
        _js_function_source(WORKSPACES_JS, name)
        for name in (
            "normalizeWorkspaceId",
            "requestWorkspaceFocusTarget",
            "claimWorkspaceFocusTarget",
        )
    ]
)

FOCUS_REQUEST_STUBS = """
const store = new Map();
const localStorage = {
    getItem: key => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, String(value)),
    removeItem: key => store.delete(key)
};
/* A window never claims its own request. Here the writer is the dashboard and
   the claimant a workspace window, so the two differ. */
const GRIDVIBE_WINDOW_ID = 'dashboard-window';
let claimingWindowId = 'workspace-window';
function isOwnBroadcast(payload) {
    return Boolean(payload) && payload.source === claimingWindowId;
}
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard targeting tests")
class WorkspaceFocusRequestTestCase(NodeHarnessTestCase):
    def _run(self, body: str):
        return self._run_node(
            FOCUS_REQUEST_STUBS + FOCUS_REQUEST_SOURCE + "\n" + body
        )

    def test_the_pane_a_row_named_reaches_the_window_that_arrives(self):
        result = self._run(
            """
            requestWorkspaceFocusTarget('a1b2c3d4e5f6', { groupId: 'g7', sessionId: 's3' });
            process.stdout.write(JSON.stringify({
                claimed: claimWorkspaceFocusTarget('a1b2c3d4e5f6')
            }));
            """
        )
        self.assertEqual(result["claimed"], {"groupId": "g7", "sessionId": "s3"})

    def test_a_request_is_claimed_once(self):
        """A window regaining focus later must not be moved again by a request
        that has already been honoured."""
        result = self._run(
            """
            requestWorkspaceFocusTarget('default', { groupId: 'g1', sessionId: 's1' });
            const first = claimWorkspaceFocusTarget('default');
            const second = claimWorkspaceFocusTarget('default');
            process.stdout.write(JSON.stringify({ first, second }));
            """
        )
        self.assertIsNotNone(result["first"])
        self.assertIsNone(result["second"])

    def test_a_request_for_another_workspace_is_left_for_it(self):
        """That window has not been raised yet, so the request is still its to
        claim -- and taking it here would move the wrong window's tab."""
        result = self._run(
            """
            requestWorkspaceFocusTarget('a1b2c3d4e5f6', { groupId: 'g7', sessionId: 's3' });
            const wrongWindow = claimWorkspaceFocusTarget('default');
            const rightWindow = claimWorkspaceFocusTarget('a1b2c3d4e5f6');
            process.stdout.write(JSON.stringify({ wrongWindow, rightWindow }));
            """
        )
        self.assertIsNone(result["wrongWindow"])
        self.assertEqual(result["rightWindow"], {"groupId": "g7", "sessionId": "s3"})

    def test_a_request_nobody_arrived_on_expires(self):
        result = self._run(
            """
            requestWorkspaceFocusTarget('default', { groupId: 'g1', sessionId: 's1' });
            const record = JSON.parse(store.get(WORKSPACE_FOCUS_REQUEST_STORAGE_KEY));
            record.timestamp -= WORKSPACE_FOCUS_REQUEST_TTL_MS + 1000;
            store.set(WORKSPACE_FOCUS_REQUEST_STORAGE_KEY, JSON.stringify(record));
            process.stdout.write(JSON.stringify({
                claimed: claimWorkspaceFocusTarget('default'),
                cleared: !store.has(WORKSPACE_FOCUS_REQUEST_STORAGE_KEY)
            }));
            """
        )
        self.assertIsNone(result["claimed"])
        # Spent either way: an expired request must not sit there being
        # re-examined on every later arrival.
        self.assertTrue(result["cleared"])

    def test_a_row_naming_no_tab_and_no_pane_stores_nothing(self):
        """Opening the workspace wherever it was left is what happens with no
        request at all, so writing one would only leave a later arrival
        something stale to claim."""
        result = self._run(
            """
            const stored = requestWorkspaceFocusTarget('default', {});
            process.stdout.write(JSON.stringify({ stored, size: store.size }));
            """
        )
        self.assertFalse(result["stored"])
        self.assertEqual(result["size"], 0)

    def test_a_window_does_not_claim_its_own_request(self):
        result = self._run(
            """
            claimingWindowId = GRIDVIBE_WINDOW_ID;
            requestWorkspaceFocusTarget('default', { groupId: 'g1', sessionId: 's1' });
            process.stdout.write(JSON.stringify({
                claimed: claimWorkspaceFocusTarget('default')
            }));
            """
        )
        self.assertIsNone(result["claimed"])


# ── The arriving side: terminals.js holds the target until a grid satisfies it ──

TERMINALS_JS = (STATIC_JS / "terminals.js").read_text(encoding="utf-8")

FOCUS_TARGET_SOURCE = "\n".join(
    [
        _js_const_source(TERMINALS_JS, "WORKSPACE_FOCUS_TARGET_GRACE_MS"),
        "let pendingWorkspaceFocusTarget = null;",
    ]
    + [
        _js_function_source(TERMINALS_JS, name)
        for name in (
            "applyWorkspaceFocusTarget",
            "settleWorkspaceFocusTarget",
            "focusPaneForArrival",
        )
    ]
)

FOCUS_TARGET_STUBS = """
const calls = { switches: [], focused: [], scrolled: [] };
let activeGroupId = 'g1';
let sessionGroups = [{ group_id: 'g1' }, { group_id: 'g2' }];
/* Which panes the grid holds right now, by session id. An empty map is a window
   whose grid has not been built yet. */
let routes = new Map();
let terminals = [];
/* What each tab paints once it has been loaded. */
const paint = new Map();

function resolveSessionTarget(sessionId) {
    const route = routes.get(sessionId);
    if (!route) { return null; }
    return { index: route.index, active: route.groupId === activeGroupId };
}

/* The real switchGroup runs a whole load. This does the part that matters here:
   it makes the tab current, paints its panes, and settles again from inside the
   load -- which is where a target that arrived too early is picked back up. */
let switchGroupDeclines = false;
async function switchGroup(groupId) {
    calls.switches.push(groupId);
    if (switchGroupDeclines) { return; }
    activeGroupId = groupId;
    routes = new Map(paint.get(groupId) || []);
    await settleWorkspaceFocusTarget();
}

const cards = new Map();
function isPlainTerminalCard(card) { return Boolean(card) && card.plain; }
const document = {
    getElementById(id) { return cards.get(id) || null; }
};

function makeCard(id, { plain = true } = {}) {
    const card = {
        id,
        plain,
        scrollIntoView() { calls.scrolled.push(id); },
        focus() { calls.focused.push('card:' + id); }
    };
    cards.set(id, card);
    return card;
}

function settle() { return new Promise(resolve => setTimeout(resolve, 0)); }
function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the dashboard targeting tests")
class WorkspaceFocusTargetTestCase(NodeHarnessTestCase):
    def _run(self, body: str):
        return self._run_node(
            FOCUS_TARGET_STUBS
            + FOCUS_TARGET_SOURCE
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )

    def test_a_pane_on_the_tab_already_in_front_is_focused_without_a_switch(self):
        result = self._run(
            """
            routes.set('s2', { groupId: 'g1', index: 1 });
            terminals = [{ term: {} }, { term: { focus() { calls.focused.push('term:1'); } } }];
            makeCard('tc-1');
            applyWorkspaceFocusTarget({ groupId: 'g1', sessionId: 's2' });
            await settle();
            report({
                switches: calls.switches,
                focused: calls.focused,
                scrolled: calls.scrolled,
                pending: pendingWorkspaceFocusTarget
            });
            """
        )
        self.assertEqual(result["switches"], [])
        self.assertEqual(result["focused"], ["term:1"])
        self.assertEqual(result["scrolled"], ["tc-1"])
        self.assertIsNone(result["pending"])

    def test_a_pane_on_another_tab_switches_to_it_and_then_lands(self):
        """The whole of note 2: a row used to reach the window and stop there,
        leaving the reader on whichever tab that window was last left on."""
        result = self._run(
            """
            paint.set('g2', [['s9', { groupId: 'g2', index: 2 }]]);
            terminals = [{}, {}, { term: { focus() { calls.focused.push('term:2'); } } }];
            makeCard('tc-2');
            applyWorkspaceFocusTarget({ groupId: 'g2', sessionId: 's9' });
            await settle();
            report({
                switches: calls.switches,
                activeGroupId,
                focused: calls.focused,
                pending: pendingWorkspaceFocusTarget
            });
            """
        )
        self.assertEqual(result["switches"], ["g2"])
        self.assertEqual(result["activeGroupId"], "g2")
        self.assertEqual(result["focused"], ["term:2"])
        self.assertIsNone(result["pending"])

    def test_a_tab_this_window_has_never_heard_of_is_not_switched_to(self):
        """A stale row would otherwise blank the grid it landed on."""
        result = self._run(
            """
            applyWorkspaceFocusTarget({ groupId: 'gone', sessionId: 's1' });
            await settle();
            report({
                switches: calls.switches,
                activeGroupId,
                stillHeld: Boolean(pendingWorkspaceFocusTarget)
            });
            """
        )
        self.assertEqual(result["switches"], [])
        self.assertEqual(result["activeGroupId"], "g1")
        self.assertTrue(result["stillHeld"])

    def test_a_target_that_arrives_before_the_grid_waits_for_the_load(self):
        """A window opened for the trip claims the request while its panes are
        still being built, so the pane it names does not exist yet."""
        result = self._run(
            """
            applyWorkspaceFocusTarget({ groupId: 'g1', sessionId: 's5' });
            await settle();
            const beforeLoad = {
                focused: [...calls.focused],
                held: Boolean(pendingWorkspaceFocusTarget)
            };
            routes.set('s5', { groupId: 'g1', index: 0 });
            terminals = [{ term: { focus() { calls.focused.push('term:0'); } } }];
            makeCard('tc-0');
            await settleWorkspaceFocusTarget();
            report({
                beforeLoad,
                focused: calls.focused,
                held: Boolean(pendingWorkspaceFocusTarget)
            });
            """
        )
        self.assertEqual(result["beforeLoad"], {"focused": [], "held": True})
        self.assertEqual(result["focused"], ["term:0"])
        self.assertFalse(result["held"])

    def test_a_pane_that_never_arrives_is_given_up_on(self):
        """A session closed between the click and the arrival must not leave the
        window reaching for a pane for the rest of the run."""
        result = self._run(
            """
            applyWorkspaceFocusTarget({ groupId: 'g1', sessionId: 'closed' });
            await settle();
            pendingWorkspaceFocusTarget.expiresAt = Date.now() - 1;
            await settleWorkspaceFocusTarget();
            report({ held: Boolean(pendingWorkspaceFocusTarget), focused: calls.focused });
            """
        )
        self.assertFalse(result["held"])
        self.assertEqual(result["focused"], [])

    def test_a_declined_switch_leaves_the_target_standing(self):
        """switchGroup refuses while an editor holds unsaved changes or a copy is
        in flight, and pretending the trip finished would drop the request."""
        result = self._run(
            """
            switchGroupDeclines = true;
            applyWorkspaceFocusTarget({ groupId: 'g2', sessionId: 's9' });
            await settle();
            report({
                switches: calls.switches,
                activeGroupId,
                stillHeld: Boolean(pendingWorkspaceFocusTarget)
            });
            """
        )
        self.assertEqual(result["switches"], ["g2"])
        self.assertEqual(result["activeGroupId"], "g1")
        self.assertTrue(result["stillHeld"])

    def test_a_pane_with_no_terminal_to_focus_is_still_shown(self):
        """An explorer or browser pane has no xterm; landing on one still has to
        say which pane was meant."""
        result = self._run(
            """
            routes.set('s4', { groupId: 'g1', index: 0 });
            terminals = [{}];
            makeCard('tc-0', { plain: false });
            applyWorkspaceFocusTarget({ groupId: 'g1', sessionId: 's4' });
            await settle();
            report({ focused: calls.focused, scrolled: calls.scrolled });
            """
        )
        self.assertEqual(result["focused"], ["card:tc-0"])
        self.assertEqual(result["scrolled"], ["tc-0"])


# ── What a pane calls itself, repainted without a rebuild ──

PANE_IDENTITY_STUBS = """
const fields = new Map();
const document = {
    getElementById(id) { return fields.get(id) || null; }
};

/* Counts writes as well as answering them, so "an unchanged reading touches
   nothing" is a fact about the field rather than about the string. */
function makeField(id, text) {
    const field = { id, writes: 0, _text: '', dataset: {} };
    Object.defineProperty(field, 'textContent', {
        get() { return this._text; },
        set(value) { this._text = value; this.writes += 1; }
    });
    field.textContent = text;
    fields.set(id, field);
    return field;
}

const AGENT_OPTIONS = [
    { value: 'claude', label: 'claude', display_name: 'Claude Code' },
    { value: 'codex', label: 'codex', display_name: 'OpenAI Codex CLI' }
];
const window = globalThis;
function report(value) { process.stdout.write(JSON.stringify(value)); }
"""

PANE_IDENTITY_SOURCE = "\n".join(
    _js_function_source(TERMINALS_JS, name)
    for name in ("paneDisplayTitle", "paneAgentIconHtml", "syncPaneAgentIcon", "syncPaneIdentityChrome")
)


@unittest.skipUnless(NODE, "Node.js is required for the dashboard targeting tests")
class PaneIdentityChromeTestCase(NodeHarnessTestCase):
    def _run(self, body: str):
        return self._run_node(
            PANE_IDENTITY_STUBS
            + (STATIC_JS / "agent-identity.js").read_text(encoding="utf-8")
            + (STATIC_JS / "agent-glyphs.js").read_text(encoding="utf-8")
            + PANE_IDENTITY_SOURCE
            + "\n"
            + body
        )

    def test_a_pane_stops_naming_an_agent_that_exited(self):
        """Note 4: leaving Codex left OpenAI's name on the header until
        something forced a rebuild, so a Claude session started by hand at the
        prompt afterwards ran under it."""
        result = self._run(
            """
            const name = makeField('tname-0', 'OpenAI Codex CLI');
            makeField('thost-0', 'PowerShell');
            syncPaneIdentityChrome(0, {
                title: 'Terminal 1', host: 'PowerShell', startup_mode: 'terminal',
                agent_selection: '', custom_agent: ''
            });
            report(name.textContent);
            """
        )
        self.assertEqual(result, "Terminal 1")

    def test_icons_follow_agent_switches_and_disappear_for_plain_panes(self):
        result = self._run(
            """
            const icon = { innerHTML: '', hidden: true };
            const name = makeField('tname-0', '');
            fields.set('ticon-0', icon);
            const captures = [];
            for (const [startup_mode, agent_selection] of [
                ['agent', 'claude'], ['agent', 'codex'], ['agent', 'other'],
                ['terminal', ''], ['explorer', ''], ['browser', '']
            ]) {
                syncPaneIdentityChrome(0, { startup_mode, agent_selection });
                captures.push({ html: icon.innerHTML, hidden: icon.hidden, brand: name.dataset.agent || '' });
            }
            report(captures);
            """
        )
        self.assertEqual([row["brand"] for row in result],
                         ["claude", "codex", "default", "", "", ""])
        for index, row in enumerate(result[:3]):
            self.assertIn("<img" if index < 2 else "<svg", row["html"])
            self.assertFalse(row["hidden"])
        self.assertNotEqual(result[0]["html"], result[1]["html"])
        for row in result[3:]:
            self.assertEqual(row, {"html": "", "hidden": True, "brand": ""})

    def test_a_pane_takes_the_name_of_the_agent_started_in_it(self):
        result = self._run(
            """
            const name = makeField('tname-0', 'OpenAI Codex CLI');
            makeField('thost-0', 'PowerShell');
            syncPaneIdentityChrome(0, {
                title: 'Terminal 1', host: 'PowerShell', startup_mode: 'agent',
                agent_selection: 'claude', custom_agent: ''
            });
            report(name.textContent);
            """
        )
        self.assertEqual(result, "Claude Code")

    def test_a_name_the_user_typed_is_never_overwritten(self):
        result = self._run(
            """
            const name = makeField('tname-0', 'release cut');
            makeField('thost-0', '');
            syncPaneIdentityChrome(0, {
                title: 'release cut', host: '', startup_mode: 'agent',
                agent_selection: 'claude', custom_agent: ''
            });
            report(name.textContent);
            """
        )
        self.assertEqual(result, "release cut")

    def test_a_reading_that_says_the_same_thing_writes_nothing(self):
        """This runs on every status broadcast and every reconciliation poll, so
        an unchanged reading must not touch the header at all."""
        result = self._run(
            """
            const name = makeField('tname-0', 'Claude Code');
            const host = makeField('thost-0', 'PowerShell');
            const session = {
                title: 'Terminal 1', host: 'PowerShell', startup_mode: 'agent',
                agent_selection: 'claude', custom_agent: ''
            };
            const before = { name: name.writes, host: host.writes };
            syncPaneIdentityChrome(0, session);
            syncPaneIdentityChrome(0, session);
            report({
                nameWrites: name.writes - before.name,
                hostWrites: host.writes - before.host
            });
            """
        )
        self.assertEqual(result, {"nameWrites": 0, "hostWrites": 0})

    def test_a_pane_with_no_header_yet_is_left_alone(self):
        result = self._run(
            """
            syncPaneIdentityChrome(7, {
                title: 'Terminal 8', host: '', startup_mode: 'terminal',
                agent_selection: '', custom_agent: ''
            });
            report('survived');
            """
        )
        self.assertEqual(result, "survived")


if __name__ == "__main__":
    unittest.main()
