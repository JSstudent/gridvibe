"""The pane header's relaunch menu, driven through its own click handler.

`terminal-shell.js` renders the reset dropdown and decides what each row means,
so it is exercised by *running* it: the real module is loaded in Node behind a
stub page, the menu is opened, its markup is parsed back into rows, and rows are
pressed through the delegated handler the page wires up.

What is pinned here is the shape of the two dimensions the menu offers:

- a shell family and its agent list are separate decisions, so the chevron is a
  control *beside* the row rather than a second meaning for it, and pressing it
  reveals that family's agents and nobody else's;
- every actionable row states both dimensions, so "Plain shell" is a stated
  choice of no agent rather than a silence, and no row can name a shell family
  without saying what to start under it;
- a pane with no shell family to hang chevrons on -- an SSH pane, or a local
  pane on a POSIX host -- gets the agent list flat and names no shell at all.

The route's own half of the contract lives in `tests/test_session_shell.py`.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TERMINAL_SHELL_JS = STATIC_JS / "terminal-shell.js"

NODE = shutil.which("node")

# The whole page surface the module can reach. Small on purpose: the menu is one
# `innerHTML` write and one delegated click listener, so the stub only has to
# hold that element, hand back the buttons the module looks up by id, and answer
# the two `document` queries the module makes.
HARNESS_STUBS = r"""
var AGENT_OPTIONS = [
    { value: 'claude', label: 'claude', display_name: 'Claude Code' },
    { value: 'codex', label: 'codex', display_name: 'OpenAI Codex CLI' },
    { value: 'other', label: 'other', display_name: 'other' }
];
var LOCAL_SHELL_MODES_AVAILABLE = true;
var terminals = [];
var sessionIds = [];

const TERMINAL_REFRESH_ICON = '<svg data-icon="refresh"></svg>';
const UI_CHECK_ICON = '<svg data-icon="check"></svg>';
const UI_CHEVRON_RIGHT_ICON = '<svg data-icon="chevron"></svg>';

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

const calls = { reset: [], connecting: [], toasts: [], requests: [] };
function refreshTerminalDisplay(index) { calls.reset.push(index); }
function showPlaceholderConnecting(index) { calls.connecting.push(index); }
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
    return { ok: true, json: async () => Object.assign({ host: 'relaunched' }, RELAUNCH_RESPONSE) };
}
var RELAUNCH_RESPONSE = {};

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
        rows.push({
            dataset,
            label: label ? label[1].trim() : (attrs['aria-label'] || ''),
            checked: attrs['aria-checked'] === 'true',
            expander: Object.prototype.hasOwnProperty.call(dataset, 'paneShellExpand'),
            launch: Object.prototype.hasOwnProperty.call(dataset, 'paneShellLaunch')
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
            if (selector === '.pane-shell-menu-item') { return row.expander ? null : node; }
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
    terminals[index] = { _session: session, term: { reset() {} } };
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
        self.assertEqual(
            result["agents"],
            [
                {"label": "Claude Code", "kind": "powershell"},
                {"label": "OpenAI Codex CLI", "kind": "powershell"},
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
        self.assertEqual(result["whileOpen"], 2)
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
            {"agent": "", "shell": "powershell", "distribution": ""},
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
            {"agent": "claude", "shell": "wsl", "distribution": "Ubuntu"},
        )

    def test_reselecting_what_the_pane_already_runs_costs_no_request(self):
        result = self._run_node(
            """
            let rows = await openMenu(0, localPane({
                use_powershell: true, startup_mode: 'agent', agent_selection: 'claude'
            }));
            await press(0, rows.find(row => row.dataset.paneShellExpand === 'powershell '));
            rows = rowsFor(0);
            await press(0, rows.find(row => row.label === 'Claude Code'));
            report({
                requests: calls.requests.filter(request => request.body).length,
                menuClosed: paneMenu(0).hidden
            });
            """
        )
        self.assertEqual(result["requests"], 0)
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
            result["labels"], ["Plain shell", "Claude Code", "OpenAI Codex CLI"]
        )
        self.assertEqual(result["expanders"], 0)
        # No `shell` key at all: an unstated shell is what leaves the pane's own
        # alone, and an SSH pane has no local family to state.
        self.assertEqual(result["requests"][0]["body"], {"agent": "claude"})

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
            result["labels"], ["Plain shell", "Claude Code", "OpenAI Codex CLI"]
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
        self.assertEqual(result["requests"][0]["body"], {"agent": ""})


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
