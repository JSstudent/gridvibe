"""The pane header's relaunch menu, driven through its own click handler.

`terminal-shell.js` renders the reset dropdown and decides what each row means,
so it is exercised by *running* it: the real module is loaded in Node behind a
stub page, the menu is opened, its markup is parsed back into rows, and rows are
pressed through the delegated handler the page wires up.

What is pinned here is the shape of the three dimensions the menu offers:

- a shell family and its agent list are separate decisions, so the chevron is a
  control *beside* the row rather than a second meaning for it, and pressing it
  reveals that family's agents and nobody else's;
- every actionable row states every dimension, so "Plain shell" is a stated
  choice of no agent rather than a silence, no row can name a shell family
  without saying what to start under it, and a plain agent row is the way back
  off GridVibe tools;
- a pane with no shell family to hang chevrons on -- an SSH pane, or a local
  pane on a POSIX host -- gets the agent list flat and names no shell at all;
- an agent whose CLI can take the sidecar carries a second target on its own
  row -- the MCP button -- which an SSH pane has as much as a local one, and
  which is inline rather than a flyout so a pane against the window's right
  edge cannot open it off-screen.

and when the pane is painted for the relaunch it asked for: before the request
rather than after it, because the connected broadcast that removes the overlay
can arrive while the route is still writing its response.

The route's own half of the contract lives in `tests/test_session_shell.py`,
and the page's half -- a group load healing an overlay nobody removed -- in
`tests/test_pane_connecting_overlay.py`.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_dashboard_targeting import PANE_IDENTITY_SOURCE

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TERMINAL_SHELL_JS = STATIC_JS / "terminal-shell.js"
# The real teardown, loaded rather than imitated: a relaunch writes the mouse
# reporting reset itself once the old shell is gone, and a harness with its own
# copy of that sequence would be checking the harness.
TERMINAL_MODES_JS = STATIC_JS / "terminal-modes.js"
# The real naming rule, loaded rather than imitated: a relaunch repaints the
# pane header from the session it got back, and a harness with a second rule of
# its own would be checking the harness.
AGENT_IDENTITY_JS = STATIC_JS / "agent-identity.js"

NODE = shutil.which("node")

# The whole page surface the module can reach. Small on purpose: the menu is one
# `innerHTML` write and one delegated click listener, so the stub only has to
# hold that element, hand back the buttons the module looks up by id, and answer
# the two `document` queries the module makes.
HARNESS_STUBS = r"""
/* Both kinds the registry really holds: CLIs that publish an MCP mechanism and
   one that publishes none, so "no mechanism, no button" is exercised rather
   than assumed. `other` is the launcher's free-text entry, which this menu
   drops for want of an input field. */
var AGENT_OPTIONS = [
    { value: 'claude', label: 'claude', display_name: 'Claude Code', mcp_supported: true },
    { value: 'codex', label: 'codex', display_name: 'OpenAI Codex CLI', mcp_supported: true },
    { value: 'kilo', label: 'kilo', display_name: 'Kilo CLI', mcp_supported: false },
    { value: 'other', label: 'other', display_name: 'other', mcp_supported: false }
];
var LOCAL_SHELL_MODES_AVAILABLE = true;
var terminals = [];
var sessionIds = [];

const TERMINAL_REFRESH_ICON = '<svg data-icon="refresh"></svg>';
const UI_CHECK_ICON = '<svg data-icon="check"></svg>';
const UI_CHEVRON_RIGHT_ICON = '<svg data-icon="chevron"></svg>';
const TERMINAL_PROMPT_ICON = '<svg data-icon="prompt"></svg>';
const AGENT_UPDATE_ICON = '<svg data-icon="update"></svg>';

/* shared.js's own escaper, copied rather than neutered: the rows' values reach
   the parser below through it, so a stub that did not escape would let a distro
   name containing markup rewrite the rows the assertions read. */
function escHtml(value) {
    return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function isExplorerSession(session) { return session?.startup_mode === 'explorer'; }
function isBrowserSession(session) { return session?.startup_mode === 'browser'; }

/* terminals.js's own one-line wrapper over agent-identity.js, which is loaded
   below this stub block. The rule itself is exercised in
   test_agent_identity.py; what it is doing here is letting the relaunch
   actually repaint a header. */
const window = globalThis;
function paneDisplayTitle(session, index) {
    return window.GridVibeAgentIdentity.paneDisplayTitle(session, index, AGENT_OPTIONS);
}

/* `order` is what the overlay cases read: a relaunch has to paint the pane
   before it asks for one, because the new transport can report connected while
   the route is still writing its response. */
const calls = {
    reset: [], connecting: [], toasts: [], requests: [], synced: [], order: [], writes: []
};
function refreshTerminalDisplay(index) { calls.reset.push(index); }

/* terminals.js's captured flush. Anything the dying agent queued behind a
   not-yet-fitted pane has to reach the pane before the teardown that exists to
   undo it, so the relaunch flushes first and the harness records the order. */
function flushCapturedPendingOutput(pane) {
    if (!pane || !pane._attached || !pane.term || !pane._pendingOutput) { return; }
    const pending = pane._pendingOutput;
    pane._pendingOutput = '';
    pane.term.write(pending);
}
function showPlaceholderConnecting(index) {
    calls.connecting.push(index);
    calls.order.push('connecting');
}
/* terminals.js's repaint-from-the-session-record helper, which the relaunch
   calls to take its own spinner back off a request that failed. */
function syncPanePlaceholder(index) {
    calls.synced.push(index);
    calls.order.push('sync-placeholder');
}
function showTerminalToast(message) { calls.toasts.push(message); }

function fakeClassList() {
    const names = new Set();
    return {
        names,
        toggle: (name, on) => { if (on) { names.add(name); } else { names.delete(name); } },
        contains: name => names.has(name)
    };
}

function fakeElement(attrs) {
    return {
        dataset: Object.assign({}, attrs),
        hidden: true,
        innerHTML: '',
        title: '',
        textContent: '',
        attributes: {},
        classList: fakeClassList(),
        listeners: {},
        addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; },
        querySelector() { return null; }
    };
}

const menus = new Map();
const byId = new Map();

function paneMenu(index) {
    if (!menus.has(index)) {
        menus.set(index, fakeElement({ paneShellMenu: String(index) }));
    }
    return menus.get(index);
}

function element(id) {
    if (!byId.has(id)) { byId.set(id, fakeElement({})); }
    return byId.get(id);
}

const document = {
    getElementById: id => element(id),
    querySelector(selector) {
        const match = /^\[data-pane-shell-menu="(\d+)"\]$/.exec(selector);
        return match ? paneMenu(Number(match[1])) : null;
    },
    querySelectorAll(selector) {
        if (selector === '.pane-shell-menu:not([hidden])') {
            return [...menus.values()].filter(menu => !menu.hidden);
        }
        return [];
    }
};

/* One distro, so the WSL section has both its "default distro" row and a named
   one -- the pair that shows a chevron is attached per family and not per kind. */
var WSL_DISTROS = { available: true, distros: [{ name: 'Ubuntu' }] };
async function fetch(url, options) {
    calls.requests.push({
        url,
        body: options && options.body ? JSON.parse(options.body) : null
    });
    if (String(url).includes('/api/wsl-distros')) {
        return { ok: true, json: async () => WSL_DISTROS };
    }
    calls.order.push('request');
    /* Whatever happens to the grid between the press and the answer. */
    if (typeof ON_RESPONSE === 'function') { ON_RESPONSE(); }
    if (RELAUNCH_REFUSED) {
        return { ok: false, status: 400, json: async () => ({ error: 'claude is not installed' }) };
    }
    return { ok: true, json: async () => Object.assign({ host: 'relaunched' }, RELAUNCH_RESPONSE) };
}
var RELAUNCH_RESPONSE = {};
/* The route's own refusal — an absent agent binary, say — which arrives after
   the pane has already been painted for the relaunch it is not getting. */
var RELAUNCH_REFUSED = false;
var ON_RESPONSE = null;

/* The rendered menu, read back as rows. Attribute values are escaped, so the
   opening tag really does end at its first '>'. */
function parseRows(html) {
    const rows = [];
    const tag = /<button\b([^>]*)>([\s\S]*?)<\/button>/g;
    let match;
    while ((match = tag.exec(html)) !== null) {
        const attrs = {};
        const dataset = {};
        const attribute = /([a-zA-Z0-9_:-]+)="([^"]*)"/g;
        let found;
        while ((found = attribute.exec(match[1])) !== null) {
            attrs[found[1]] = found[2];
            if (found[1].startsWith('data-')) {
                const key = found[1].slice(5).replace(/-([a-z])/g, (_all, c) => c.toUpperCase());
                dataset[key] = found[2];
            }
        }
        const label = /<span class="pane-shell-menu-label">([\s\S]*?)<\/span>/.exec(match[2]);
        const icon = /<span class="pane-shell-menu-icon">([\s\S]*?)<\/span>/.exec(match[2]);
        rows.push({
            dataset,
            icon: icon ? icon[1].trim() : '',
            label: label ? label[1].trim() : (attrs['aria-label'] || ''),
            checked: attrs['aria-checked'] === 'true',
            expander: Object.prototype.hasOwnProperty.call(dataset, 'paneShellExpand'),
            launch: Object.prototype.hasOwnProperty.call(dataset, 'paneShellLaunch'),
            /* The MCP button carries no label span -- its text is the badge --
               so it is told apart by the class the page styles it with. */
            tools: /class="[^"]*pane-shell-menu-mcp/.test(match[1]),
            /* The update button, likewise: an icon and an aria-label. */
            update: /class="[^"]*pane-shell-menu-update/.test(match[1])
        });
    }
    return rows;
}

/* A press goes through the delegated listener the page wired, so what a row
   means is decided by the module and never by the harness. */
async function press(index, row) {
    const menu = paneMenu(index);
    const node = { dataset: row.dataset };
    const target = {
        closest: selector => {
            if (selector === '[data-pane-shell-expand]') { return row.expander ? node : null; }
            if (selector === '[data-pane-shell-launch]') { return row.launch ? node : null; }
            /* The MCP button is a sibling control, not a menu item, so it is
               reachable only through the launch lookup above. */
            if (selector === '.pane-shell-menu-item') {
                return row.expander || row.tools || row.update ? null : node;
            }
            return null;
        }
    };
    menu.listeners.click.forEach(fn => fn({
        preventDefault() {}, stopPropagation() {}, target
    }));
    await new Promise(resolve => setImmediate(resolve));
}

function rowsFor(index) { return parseRows(paneMenu(index).innerHTML); }

/* Open the menu the way the header button does, and let the distro lookup land
   so the WSL rows are the ones a user actually sees. */
async function openMenu(index, session, options) {
    const opts = options || {};
    if (Object.prototype.hasOwnProperty.call(opts, 'windowsShells')) {
        LOCAL_SHELL_MODES_AVAILABLE = opts.windowsShells;
    }
    /* A live, attached pane holding whatever the outgoing program queued: the
       relaunch has to get that in front of its own teardown. */
    terminals[index] = {
        _session: session,
        _attached: true,
        _pendingOutput: Object.prototype.hasOwnProperty.call(opts, 'pending') ? opts.pending : '',
        term: {
            reset() { calls.order.push('term-reset'); },
            write(data) {
                calls.writes.push(data);
                calls.order.push(
                    data === window.GridVibeTerminalModes.MOUSE_REPORTING_RESET
                        ? 'mouse-teardown'
                        : 'pane-write'
                );
            }
        }
    };
    sessionIds[index] = session.session_id || 'sess-1';
    const card = { querySelector: () => paneMenu(index) };
    wirePaneShellMenu(card, index);
    handlePaneResetButton(index);
    await new Promise(resolve => setImmediate(resolve));
    await new Promise(resolve => setImmediate(resolve));
    return rowsFor(index);
}

function localPane(overrides) {
    return Object.assign({
        session_id: 'sess-1',
        mode: 'wsl',
        startup_mode: 'terminal',
        use_wsl: false,
        use_powershell: false,
        distribution: ''
    }, overrides || {});
}

function sshPane(overrides) {
    return Object.assign({
        session_id: 'sess-ssh',
        mode: 'ssh',
        startup_mode: 'terminal'
    }, overrides || {});
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for pane relaunch menu tests")
class TerminalShellMenuTestCase(unittest.TestCase):
    """Shared runner: the real module, a stub page, and one menu."""

    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + TERMINAL_MODES_JS.read_text(encoding="utf-8")
            + AGENT_IDENTITY_JS.read_text(encoding="utf-8")
            + (STATIC_JS / "agent-glyphs.js").read_text(encoding="utf-8")
            + PANE_IDENTITY_SOURCE
            + TERMINAL_SHELL_JS.read_text(encoding="utf-8")
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
                # The WSL rows carry a "·" the harness reports back, so the
                # pipe is decoded as UTF-8 rather than as the host's locale.
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class ShellFamilyChevronTestCase(TerminalShellMenuTestCase):
    """The chevron is a control beside the row, not a second meaning for it."""

    def test_every_shell_family_offers_its_agents_behind_a_chevron(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, localPane());
            report({
                families: rows.filter(row => row.launch).map(row => row.label),
                expanders: rows.filter(row => row.expander).map(row => row.dataset.paneShellExpand),
                agentRows: rows.filter(row => row.dataset.paneShellAgent).length
            });
            """
        )
        # Four families -- two Windows shells, WSL's default distro and the one
        # named distro the lookup returned -- and one chevron each.
        self.assertEqual(
            result["families"],
            ["Command Prompt", "PowerShell", "WSL", "WSL · Ubuntu"],
        )
        self.assertEqual(len(result["expanders"]), 4)
        # Nothing is emitted per family until a chevron is pressed: a menu that
        # built every family's agents to show at most one is the cost the
        # expansion exists to avoid.
        self.assertEqual(result["agentRows"], 0)

    def test_pressing_one_chevron_reveals_that_familys_agents_and_no_others(self):
        result = self._run_node(
            """
            let rows = await openMenu(0, localPane());
            const powershell = rows.find(row => row.dataset.paneShellExpand === 'powershell ');
            await press(0, powershell);
            rows = rowsFor(0);
            const agents = rows.filter(row => row.launch && row.dataset.paneShellAgent);
            report({
                agents: agents.map(row => ({ label: row.label, kind: row.dataset.paneShellKind })),
                plainRows: rows.filter(row => row.launch && !row.dataset.paneShellAgent)
                    .map(row => row.label),
                expandedFlags: rows.filter(row => row.expander)
                    .map(row => row.dataset.paneShellExpand + '=' + (row.checked ? '?' : '')),
                requests: calls.requests.map(request => request.url)
            });
            """
        )
        # The registry's agents, named in prose, all under the family that was
        # opened -- and the launcher's free-text "other" is not among them,
        # because composing a custom command needs an input this menu has none of.
        # Each MCP-capable one brings its tools button along; the one that
        # publishes no mechanism does not.
        self.assertEqual(
            result["agents"],
            [
                {"label": "Claude Code", "kind": "powershell"},
                {"label": "Claude Code with GridVibe tools", "kind": "powershell"},
                {"label": "OpenAI Codex CLI", "kind": "powershell"},
                {"label": "OpenAI Codex CLI with GridVibe tools", "kind": "powershell"},
                {"label": "Kilo CLI", "kind": "powershell"},
            ],
        )
        # "Plain shell" joins the four family rows, directly under the one it
        # was opened from: the agent radio group is complete, so choosing no
        # agent is a row rather than an absence.
        self.assertEqual(
            result["plainRows"],
            ["Command Prompt", "PowerShell", "Plain shell", "WSL", "WSL · Ubuntu"],
        )
        # Expanding costs no request: the list is the page's own constant.
        self.assertEqual(result["requests"], ["/api/wsl-distros"])

    def test_only_one_familys_agents_are_open_at_a_time(self):
        result = self._run_node(
            """
            let rows = await openMenu(0, localPane());
            await press(0, rows.find(row => row.dataset.paneShellExpand === 'cmd '));
            await press(0, rowsFor(0).find(row => row.dataset.paneShellExpand === 'wsl Ubuntu'));
            rows = rowsFor(0);
            report({
                kinds: [...new Set(rows.filter(row => row.dataset.paneShellAgent)
                    .map(row => row.dataset.paneShellKind + ' ' + row.dataset.paneShellDistro))]
            });
            """
        )
        self.assertEqual(result["kinds"], ["wsl Ubuntu"])

    def test_closing_the_menu_forgets_which_family_was_open(self):
        """An expansion is a pointer gesture inside one opening, not pane state."""
        result = self._run_node(
            """
            const rows = await openMenu(0, localPane());
            await press(0, rows.find(row => row.dataset.paneShellExpand === 'cmd '));
            const whileOpen = rowsFor(0).filter(row => row.dataset.paneShellAgent).length;
            handlePaneResetButton(0);
            handlePaneResetButton(0);
            await new Promise(resolve => setImmediate(resolve));
            report({ whileOpen, reopened: rowsFor(0).filter(row => row.dataset.paneShellAgent).length });
            """
        )
        # Three agents and the two tools buttons beside the capable ones.
        self.assertEqual(result["whileOpen"], 5)
        self.assertEqual(result["reopened"], 0)


class RelaunchRowPayloadTestCase(TerminalShellMenuTestCase):
    """What a row sends, and what it deliberately leaves unsaid."""

    def test_a_shell_row_relaunches_its_family_plainly(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, localPane({
                startup_mode: 'agent', agent_selection: 'claude'
            }));
            await press(0, rows.find(row => row.label === 'PowerShell'));
            report({ requests: calls.requests.filter(request => request.body) });
            """
        )
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(
            result["requests"][0]["body"],
            {"agent": "", "mcp": False, "shell": "powershell", "distribution": ""},
        )

    def test_an_agent_row_carries_the_family_it_was_opened_under(self):
        result = self._run_node(
            """
            let rows = await openMenu(0, localPane());
            await press(0, rows.find(row => row.dataset.paneShellExpand === 'wsl Ubuntu'));
            rows = rowsFor(0);
            await press(0, rows.find(row => row.label === 'Claude Code'));
            report({ requests: calls.requests.filter(request => request.body) });
            """
        )
        self.assertEqual(
            result["requests"][0]["body"],
            {"agent": "claude", "mcp": False, "shell": "wsl", "distribution": "Ubuntu"},
        )

    def test_reselecting_what_the_pane_already_runs_requests_a_relaunch(self):
        result = self._run_node(
            """
            let rows = await openMenu(0, localPane({
                use_powershell: true, startup_mode: 'agent', agent_selection: 'claude'
            }));
            await press(0, rows.find(row => row.dataset.paneShellExpand === 'powershell '));
            rows = rowsFor(0);
            await press(0, rows.find(row => row.label === 'Claude Code'));
            report({
                requests: calls.requests.filter(request => request.body),
                menuClosed: paneMenu(0).hidden
            });
            """
        )
        self.assertEqual(
            result["requests"][0]["body"],
            {"agent": "claude", "mcp": False, "shell": "powershell", "distribution": ""},
        )
        self.assertTrue(result["menuClosed"])

    def test_the_check_marks_report_the_family_and_the_agent_separately(self):
        """Two dimensions, two radio groups -- and an agent row never claims a
        family the pane is not running in."""
        result = self._run_node(
            """
            let rows = await openMenu(0, localPane({
                startup_mode: 'agent', agent_selection: 'claude'
            }));
            const families = rows.filter(row => row.launch && row.checked).map(row => row.label);
            await press(0, rows.find(row => row.dataset.paneShellExpand === 'cmd '));
            const here = rowsFor(0).filter(row => row.checked).map(row => row.label);
            await press(0, rowsFor(0).find(row => row.dataset.paneShellExpand === 'powershell '));
            const elsewhere = rowsFor(0).filter(row => row.checked).map(row => row.label);
            report({ families, here, elsewhere });
            """
        )
        self.assertEqual(result["families"], ["Command Prompt"])
        self.assertEqual(result["here"], ["Command Prompt", "Claude Code"])
        # The pane runs cmd, so nothing under PowerShell is what it is running.
        self.assertEqual(result["elsewhere"], ["Command Prompt"])


class PaneWithoutShellFamiliesTestCase(TerminalShellMenuTestCase):
    """A pane with no family to hang chevrons on still picks an agent."""

    def test_an_ssh_pane_gets_the_agent_list_flat_and_names_no_shell(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane());
            await press(0, rows.find(row => row.label === 'Claude Code'));
            report({
                labels: rows.filter(row => row.launch).map(row => row.label),
                expanders: rows.filter(row => row.expander).length,
                requests: calls.requests.filter(request => request.body)
            });
            """
        )
        self.assertEqual(
            result["labels"],
            [
                "Plain shell",
                "Claude Code",
                "Claude Code with GridVibe tools",
                "OpenAI Codex CLI",
                "OpenAI Codex CLI with GridVibe tools",
                "Kilo CLI",
            ],
        )
        self.assertEqual(result["expanders"], 0)
        # No `shell` key at all: an unstated shell is what leaves the pane's own
        # alone, and an SSH pane has no local family to state.
        self.assertEqual(
            result["requests"][0]["body"], {"agent": "claude", "mcp": False}
        )

    def test_a_local_pane_on_a_posix_host_gets_the_same_flat_list(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, localPane(), { windowsShells: false });
            report({
                labels: rows.filter(row => row.launch).map(row => row.label),
                expanders: rows.filter(row => row.expander).length
            });
            """
        )
        self.assertEqual(
            result["labels"],
            [
                "Plain shell",
                "Claude Code",
                "Claude Code with GridVibe tools",
                "OpenAI Codex CLI",
                "OpenAI Codex CLI with GridVibe tools",
                "Kilo CLI",
            ],
        )
        self.assertEqual(result["expanders"], 0)

    def test_plain_shell_returns_an_ssh_agent_pane_to_a_plain_shell(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane({
                startup_mode: 'agent', agent_selection: 'codex'
            }));
            await press(0, rows.find(row => row.label === 'Plain shell'));
            report({ requests: calls.requests.filter(request => request.body) });
            """
        )
        self.assertEqual(result["requests"][0]["body"], {"agent": "", "mcp": False})


class AgentRowIdentityTestCase(TerminalShellMenuTestCase):
    """An agent row names its agent the way the pane header will: its mark and
    its brand colour, from the same glyph module the header paints from."""

    def test_each_agent_row_wears_its_own_mark_and_brand_key(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane());
            report(rows.filter(row => row.launch && !row.tools).map(row => ({
                label: row.label,
                agent: row.dataset.agent || '',
                icon: row.icon
            })));
            """
        )
        by_label = {row["label"]: row for row in result}
        self.assertEqual(by_label["Claude Code"]["agent"], "claude")
        self.assertIn("/docs/images/agent/claude-code.svg", by_label["Claude Code"]["icon"])
        self.assertEqual(by_label["OpenAI Codex CLI"]["agent"], "codex")
        self.assertIn("/docs/images/agent/openai.svg", by_label["OpenAI Codex CLI"]["icon"])
        self.assertIn("/docs/images/agent/kilocode.svg", by_label["Kilo CLI"]["icon"])
        # A plain shell is no agent, so it takes no brand colour -- it wears the
        # prompt glyph the pane header's terminal toggle uses.
        self.assertEqual(by_label["Plain shell"]["agent"], "")
        self.assertIn('data-icon="prompt"', by_label["Plain shell"]["icon"])

    def test_the_mcp_button_and_the_shell_rows_carry_no_agent_mark(self):
        result = self._run_node(
            """
            const opened = await openMenu(0, localPane());
            await press(0, opened.find(row => row.dataset.paneShellExpand === 'powershell '));
            const rows = rowsFor(0);
            report({
                tools: rows.filter(row => row.tools).map(row => row.icon + (row.dataset.agent || '')),
                families: rows.filter(row => row.launch && !row.dataset.paneShellAgent
                    && row.label !== 'Plain shell').map(row => row.label + '=' + row.icon)
            });
            """
        )
        # The MCP badge is a second target on its agent's row, not a second
        # agent row: the mark and the colour belong to the row beside it.
        self.assertEqual(result["tools"], ["", ""])
        # A shell family is where an agent runs, not what it is.
        self.assertEqual(
            result["families"],
            ["Command Prompt=", "PowerShell=", "WSL=", "WSL · Ubuntu="],
        )


class AgentToolsButtonTestCase(TerminalShellMenuTestCase):
    """The third dimension: starting an agent with GridVibe tools.

    Seated as a second target on the agent's own row rather than as a flyout,
    so the panel never opens sideways out of the window on a pane docked
    against its right edge -- and so either choice is one press.
    """

    def test_only_an_agent_whose_cli_can_take_the_sidecar_carries_a_button(self):
        result = self._run_node(
            """
            let rows = await openMenu(0, sshPane());
            report({
                tools: rows.filter(row => row.tools).map(row => row.dataset.paneShellAgent),
                stated: rows.filter(row => row.launch)
                    .map(row => (row.dataset.paneShellAgent || '-') + '=' + row.dataset.paneShellMcp)
            });
            """
        )
        # The CLI publishing no MCP mechanism gets the bare row, exactly as a
        # pane with no shell family gets no chevron.
        self.assertEqual(result["tools"], ["claude", "codex"])
        # Every row states the dimension, and only the buttons state it true --
        # so a plain row is the way back off the tools rather than a silence
        # the route would read as "leave that alone".
        self.assertEqual(
            result["stated"],
            ["-=0", "claude=0", "claude=1", "codex=0", "codex=1", "kilo=0"],
        )

    def test_the_button_starts_that_agent_with_gridvibe_tools(self):
        result = self._run_node(
            """
            let rows = await openMenu(0, localPane());
            await press(0, rows.find(row => row.dataset.paneShellExpand === 'wsl Ubuntu'));
            rows = rowsFor(0);
            await press(0, rows.find(row => row.tools && row.dataset.paneShellAgent === 'codex'));
            report({ requests: calls.requests.filter(request => request.body) });
            """
        )
        # The button carries the family it was opened under, exactly as the row
        # beside it does: "this pane, but Codex in WSL Ubuntu, with tools".
        self.assertEqual(
            result["requests"][0]["body"],
            {"agent": "codex", "mcp": True, "shell": "wsl", "distribution": "Ubuntu"},
        )

    def test_an_ssh_pane_may_start_its_agent_with_tools_too(self):
        """A remote pane's tools ride home on its own transport."""
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane());
            await press(0, rows.find(row => row.tools && row.dataset.paneShellAgent === 'claude'));
            report({ requests: calls.requests.filter(request => request.body) });
            """
        )
        self.assertEqual(
            result["requests"][0]["body"], {"agent": "claude", "mcp": True}
        )

    def test_the_row_beside_the_button_is_the_way_back_off_the_tools(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane({
                startup_mode: 'agent', agent_selection: 'claude', agent_mcp: true
            }));
            await press(0, rows.find(row => !row.tools && row.label === 'Claude Code'));
            report({ requests: calls.requests.filter(request => request.body) });
            """
        )
        self.assertEqual(
            result["requests"][0]["body"], {"agent": "claude", "mcp": False}
        )

    def test_the_pair_reports_which_of_the_two_the_pane_is_running(self):
        result = self._run_node(
            """
            function checkedIn(session) {
                menus.clear();
                return openMenu(0, session).then(rows => rows
                    .filter(row => row.checked)
                    .map(row => row.label));
            }
            const withTools = await checkedIn(sshPane({
                startup_mode: 'agent', agent_selection: 'claude', agent_mcp: true
            }));
            const without = await checkedIn(sshPane({
                startup_mode: 'agent', agent_selection: 'claude'
            }));
            const plain = await checkedIn(sshPane({ agent_mcp: true }));
            report({ withTools, without, plain });
            """
        )
        # Exactly one of the pair wears the check, and it is the one describing
        # what the pane is actually running.
        self.assertEqual(result["withTools"], ["Claude Code with GridVibe tools"])
        self.assertEqual(result["without"], ["Claude Code"])
        # No agent, no tools -- however a preset written before the pane was
        # sent back to a plain shell left the flag.
        self.assertEqual(result["plain"], ["Plain shell"])


# The registry as it really is for updates: two agents publish a command and the
# third is left without one, so "no command, no button" is exercised.
WITH_UPDATE_COMMANDS = """
AGENT_OPTIONS = AGENT_OPTIONS.map(option => Object.assign({}, option, {
    update_command: { claude: 'claude update', kilo: 'kilo upgrade' }[option.value] || ''
}));
"""


class AgentUpdateButtonTestCase(TerminalShellMenuTestCase):
    """The update icon beside an agent row: update the agent, then start it.

    It took the slot the row's grey command-name hint used to fill, and it is a
    relaunch target like the MCP button: it states every dimension the row does
    and asks for the update on top.
    """

    def test_the_command_name_hint_gave_way_to_an_update_button(self):
        result = self._run_node(
            WITH_UPDATE_COMMANDS
            + """
            const rows = await openMenu(0, sshPane());
            report({
                hints: (paneMenu(0).innerHTML.match(/pane-shell-menu-hint/g) || []).length,
                updates: rows.filter(row => row.update).map(row => ({
                    agent: row.dataset.paneShellAgent,
                    label: row.label,
                    icon: /data-icon="update"/.test(paneMenu(0).innerHTML)
                }))
            });
            """
        )
        self.assertEqual(result["hints"], 0)
        # Codex publishes no command in this fixture, so it has no button.
        self.assertEqual(
            result["updates"],
            [
                {"agent": "claude", "label": "Update Claude Code (claude update), then start it", "icon": True},
                {"agent": "kilo", "label": "Update Kilo CLI (kilo upgrade), then start it", "icon": True},
            ],
        )

    def test_the_button_updates_the_agent_under_the_family_it_was_opened_in(self):
        result = self._run_node(
            WITH_UPDATE_COMMANDS
            + """
            let rows = await openMenu(0, localPane());
            await press(0, rows.find(row => row.dataset.paneShellExpand === 'wsl Ubuntu'));
            rows = rowsFor(0);
            await press(0, rows.find(row => row.update && row.dataset.paneShellAgent === 'kilo'));
            report({ requests: calls.requests.filter(request => request.body) });
            """
        )
        self.assertEqual(
            result["requests"],
            [{
                "url": "/api/sessions/sess-1/shell",
                "body": {
                    "agent": "kilo", "mcp": False, "update": True,
                    "shell": "wsl", "distribution": "Ubuntu",
                },
            }],
        )

    def test_the_row_itself_never_asks_for_an_update(self):
        result = self._run_node(
            WITH_UPDATE_COMMANDS
            + """
            let rows = await openMenu(0, sshPane());
            await press(0, rows.find(row => !row.update && !row.tools && row.label === 'Claude Code'));
            menus.clear();
            rows = await openMenu(0, sshPane());
            await press(0, rows.find(row => row.tools && row.dataset.paneShellAgent === 'claude'));
            report({ bodies: calls.requests.filter(request => request.body).map(request => request.body) });
            """
        )
        self.assertEqual(
            result["bodies"],
            [{"agent": "claude", "mcp": False}, {"agent": "claude", "mcp": True}],
        )

    def test_updating_the_running_agent_keeps_its_gridvibe_tools(self):
        """Updating is never also the way off the tools."""
        result = self._run_node(
            WITH_UPDATE_COMMANDS
            + """
            const running = () => sshPane({
                startup_mode: 'agent', agent_selection: 'claude', agent_mcp: true
            });
            let rows = await openMenu(0, running());
            const checked = rows.filter(row => row.checked).map(row => row.label);
            await press(0, rows.find(row => row.update && row.dataset.paneShellAgent === 'claude'));
            menus.clear();
            rows = await openMenu(0, running());
            await press(0, rows.find(row => row.update && row.dataset.paneShellAgent === 'kilo'));
            report({
                bodies: calls.requests.filter(request => request.body).map(request => request.body),
                checked
            });
            """
        )
        # Kilo is not what the pane runs, so it starts the way its row would.
        self.assertEqual(
            result["bodies"],
            [
                {"agent": "claude", "mcp": True, "update": True},
                {"agent": "kilo", "mcp": False, "update": True},
            ],
        )
        # An action, not a choice: the button never wears the check.
        self.assertEqual(result["checked"], ["Claude Code with GridVibe tools"])


class RelaunchedPaneHeaderTestCase(TerminalShellMenuTestCase):
    """A relaunched pane's header says what it is running now.

    The pane keeps its slot, its group and its stored title, so the relaunch
    used to repaint only the host line — and a pane pointed at a different
    agent went on calling itself by the old one until something else rebuilt
    the window. The stored title is still never written to: what changes is
    only what the header prints, from the session the route handed back.
    """

    def _relaunch(self, session: str, response: str, row: str):
        return self._run_node(
            """
            RELAUNCH_RESPONSE = %s;
            const rows = await openMenu(0, %s);
            await press(0, rows.find(row => row.label === '%s'));
            report({
                name: element('tname-0').textContent,
                host: element('thost-0').textContent,
                toasts: calls.toasts,
                connecting: calls.connecting
            });
            """
            % (response, session, row)
        )

    def test_relaunching_onto_an_agent_renames_the_header(self):
        result = self._relaunch(
            "sshPane({ startup_mode: 'terminal', title: 'Terminal 1' })",
            "{ startup_mode: 'agent', agent_selection: 'codex', title: 'Terminal 1' }",
            "OpenAI Codex CLI",
        )
        self.assertEqual(result["name"], "OpenAI Codex CLI")
        self.assertEqual(result["host"], "relaunched")
        self.assertEqual(result["toasts"], [])
        self.assertEqual(result["connecting"], [0])

    def test_relaunching_back_to_a_plain_shell_takes_the_agents_name_off(self):
        result = self._relaunch(
            "sshPane({ startup_mode: 'agent', agent_selection: 'claude', title: 'Terminal 1' })",
            "{ startup_mode: 'terminal', agent_selection: '', title: 'Terminal 1' }",
            "Plain shell",
        )
        self.assertEqual(result["name"], "Terminal 1")

    def test_a_title_the_user_typed_survives_the_relaunch(self):
        result = self._relaunch(
            "sshPane({ startup_mode: 'terminal', title: 'build box' })",
            "{ startup_mode: 'agent', agent_selection: 'codex', title: 'build box' }",
            "OpenAI Codex CLI",
        )
        self.assertEqual(result["name"], "build box")


class RelaunchedPaneOverlayTestCase(TerminalShellMenuTestCase):
    """When the pane is painted for the relaunch it asked for.

    The new transport is started while the route is still writing its response,
    and a local shell is marked connected inside that same request — so the
    connected broadcast can reach the page before the response does. That event
    is the only thing that takes the overlay off an attached pane, so a spinner
    raised behind it is a spinner nothing removes (ISSUE-2026-053). The order is
    therefore part of the contract, not an implementation detail.
    """

    def test_the_pane_is_painted_before_the_relaunch_is_requested(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane());
            await press(0, rows.find(row => row.label === 'Claude Code'));
            report({ order: calls.order, connecting: calls.connecting });
            """
        )
        # The reset rides in front for the same reason: the backend has already
        # dropped the old shell's replay buffer, so a reset that waited for the
        # response would clear what the new shell had drawn in the meantime.
        # A newly launched agent owns its mouse mode. The response can arrive
        # after that agent has already armed it, so no teardown may follow.
        self.assertEqual(
            result["order"],
            ["term-reset", "connecting", "request"],
        )
        self.assertEqual(result["connecting"], [0])

    def test_a_refused_relaunch_takes_its_own_spinner_back_off(self):
        """Nothing was relaunched, so the pane is still running what it was —
        and a live shell must not end up behind a permanent "Connecting…"."""
        result = self._run_node(
            """
            RELAUNCH_REFUSED = true;
            const rows = await openMenu(0, sshPane());
            await press(0, rows.find(row => row.label === 'Claude Code'));
            report({ order: calls.order, toasts: calls.toasts, synced: calls.synced });
            """
        )
        self.assertEqual(result["toasts"], ["claude is not installed"])
        # Repainted from the pane's own session record rather than blanked: a
        # pane that was disconnected before the press still says so.
        self.assertEqual(result["synced"], [0])
        self.assertEqual(
            result["order"],
            ["term-reset", "connecting", "request", "sync-placeholder"],
        )

    def test_a_relaunch_refused_before_the_request_paints_nothing(self):
        """An SSH pane has no local shell family, so naming one is refused where
        it is read — and a refusal that never asked for anything must not leave
        an overlay over the pane it declined to touch."""
        result = self._run_node(
            """
            await openMenu(0, sshPane());
            await relaunchSessionShell(0, { shell: 'powershell', agent: '' });
            report({ order: calls.order, requests: calls.requests.filter(r => r.body) });
            """
        )
        self.assertEqual(result["order"], [])
        self.assertEqual(result["requests"], [])

    def test_a_pane_whose_slot_changed_hands_is_not_repainted(self):
        """The answer belongs to the pane that asked. If that pane left the grid
        while the request was in flight, its replacement is not the one whose
        relaunch failed."""
        result = self._run_node(
            """
            RELAUNCH_REFUSED = true;
            ON_RESPONSE = () => { terminals[0] = { _session: sshPane(), term: {} }; };
            const rows = await openMenu(0, sshPane());
            await press(0, rows.find(row => row.label === 'Claude Code'));
            report({ synced: calls.synced, toasts: calls.toasts });
            """
        )
        self.assertEqual(result["synced"], [])
        # The failure is still reported: the toast is not addressed to a pane.
        self.assertEqual(result["toasts"], ["claude is not installed"])


class RelaunchedPaneMouseReportingTestCase(TerminalShellMenuTestCase):
    """The pointer movement a relaunched pane used to start typing at its shell.

    Mouse reporting belongs to whatever runs in the pane: a TUI arms it with
    `\\x1b[?1003h` and re-asserts it on every redraw. Relaunching a pane off an
    agent and onto a plain shell resets the xterm *before* the request, which
    disarms the mode — and then the agent, still alive while the request is in
    flight, goes on redrawing. Those bytes are written to the pane after the
    reset that cleared them, so the plain shell that inherits the prompt gets
    every pointer movement typed at it and Enter submits the lot.

    That is the pane `terminal-modes.js` exists to rescue, and until now only
    the Reset view button rescued it. Here GridVibe caused the state itself, so
    the relaunch undoes it without the reader having to notice.
    """

    def test_the_teardown_is_written_once_the_old_shell_is_gone(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane({
                startup_mode: 'agent', agent_selection: 'claude'
            }));
            await press(0, rows.find(row => row.label === 'Plain shell'));
            report({
                order: calls.order,
                writes: calls.writes,
                teardown: window.GridVibeTerminalModes.MOUSE_REPORTING_RESET
            });
            """
        )
        self.assertEqual(result["writes"], [result["teardown"]])
        # After the answer, never before it: the program that kept re-arming
        # the mode is only gone once the route has replaced it.
        self.assertLess(
            result["order"].index("request"),
            result["order"].index("mouse-teardown"),
        )

    def test_a_new_agent_keeps_the_mouse_mode_it_owns(self):
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane({ startup_mode: 'terminal' }));
            await press(0, rows.find(row => row.label === 'Claude Code'));
            report({ order: calls.order, writes: calls.writes });
            """
        )
        self.assertEqual(result["writes"], [])
        self.assertNotIn("mouse-teardown", result["order"])

    def test_the_dying_agents_queued_bytes_land_before_the_teardown(self):
        """A backlog held behind a not-yet-fitted pane is exactly where the
        re-arming redraw sits, so a teardown written in front of it would be
        undone by the very bytes it exists to undo."""
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane({
                startup_mode: 'agent', agent_selection: 'claude'
            }), { pending: '\\u001b[?1003h\\u001b[?1006hredraw' });
            await press(0, rows.find(row => row.label === 'Plain shell'));
            report({ order: calls.order, writes: calls.writes });
            """
        )
        self.assertEqual(
            result["order"],
            ["term-reset", "connecting", "request", "pane-write", "mouse-teardown"],
        )
        self.assertEqual(result["writes"][0], "\x1b[?1003h\x1b[?1006hredraw")

    def test_a_refused_relaunch_writes_no_teardown(self):
        """Nothing was relaunched, so the pane is still running the TUI that
        armed the mode — disarming it would break a live program instead."""
        result = self._run_node(
            """
            RELAUNCH_REFUSED = true;
            const rows = await openMenu(0, sshPane({
                startup_mode: 'agent', agent_selection: 'claude'
            }));
            await press(0, rows.find(row => row.label === 'Plain shell'));
            report({ order: calls.order, writes: calls.writes });
            """
        )
        self.assertEqual(result["writes"], [])
        self.assertNotIn("mouse-teardown", result["order"])

    def test_the_teardown_follows_the_pane_whose_slot_changed_hands(self):
        """The write is owed to the pane that asked for it, wherever the grid
        has since put it — the same rule Reset view follows."""
        result = self._run_node(
            """
            const rows = await openMenu(0, sshPane({
                startup_mode: 'agent', agent_selection: 'claude'
            }));
            const asked = terminals[0];
            const replacement = { _session: sshPane({ session_id: 'sess-other' }),
                                  _attached: true, _pendingOutput: '',
                                  term: { reset() {}, write() { throw new Error('wrong pane'); } } };
            ON_RESPONSE = () => { terminals[0] = replacement; sessionIds[0] = 'sess-other'; };
            await press(0, rows.find(row => row.label === 'Plain shell'));
            report({
                writes: calls.writes,
                teardown: window.GridVibeTerminalModes.MOUSE_REPORTING_RESET,
                askedStillHeld: calls.writes.length === 1
            });
            """
        )
        # The replacement pane's `write` throws, so reaching it at all would
        # fail the harness rather than pass quietly.
        self.assertEqual(result["writes"], [result["teardown"]])


class PaneWithoutARelaunchTestCase(TerminalShellMenuTestCase):
    """Panes with no shell at all keep the plain one-click reset."""

    def test_an_explorer_or_browser_pane_has_no_relaunch_menu(self):
        result = self._run_node(
            """
            report({
                explorer: paneHasResetMenu({ mode: 'wsl', startup_mode: 'explorer' }),
                browser: paneHasResetMenu({ mode: 'wsl', startup_mode: 'browser' }),
                browserTitle: paneShellResetTitle({ mode: 'wsl', startup_mode: 'browser' }),
                terminal: paneHasResetMenu({ mode: 'wsl', startup_mode: 'terminal' }),
                ssh: paneHasResetMenu({ mode: 'ssh', startup_mode: 'terminal' })
            });
            """
        )
        self.assertFalse(result["explorer"])
        self.assertFalse(result["browser"])
        self.assertEqual(result["browserTitle"], "Reload browser pane")
        self.assertTrue(result["terminal"])
        self.assertTrue(result["ssh"])

    def test_a_pane_with_no_menu_resets_on_the_first_click(self):
        result = self._run_node(
            """
            terminals[0] = { _session: { mode: 'wsl', startup_mode: 'explorer' } };
            sessionIds[0] = 'sess-1';
            handlePaneResetButton(0);
            report({ reset: calls.reset, menuOpen: !paneMenu(0).hidden });
            """
        )
        self.assertEqual(result["reset"], [0])
        self.assertFalse(result["menuOpen"])


if __name__ == "__main__":
    unittest.main()
