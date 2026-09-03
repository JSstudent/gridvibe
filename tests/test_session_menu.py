"""The session/workspace menu, driven through its own click handler.

`session-menu.js` builds the panel that replaced the top bar's two dropdowns and
decides what each row means, so it is exercised by *running* it: the real module
is loaded in Node behind a stub page, the menu is opened the way the button
opens it, its markup is parsed back into rows, and rows are pressed through the
delegated handler the page wires up.

What is pinned here is the disclosure model and the one thing it has to keep
true about the two menus it merged:

- the whole row is the control, so a collapsed panel offers exactly two rows and
  nothing else — nothing is emitted per section until one is pointed at, and
  hover, press and keyboard focus are three ways into the same idempotent open;
- at most one section is open, and the expansion is a pointer gesture inside one
  opening of the menu: closing the menu drops it;
- a menu opened for `Save Session` costs no `fetchLiveWorkspaces()` — the two
  dynamic workspace lists are asked for when **Workspace** is expanded, and only
  then;
- every actionable row states its action, the menu closes before the action
  runs, and a disabled row sends nothing;
- the multi-workspace half of the Workspace section is gated on the flag, and
  Save Workspace survives it;
- the pointer leaving the menu closes it, after a grace that survives the gap
  between the button and the panel, and every close drops the pending one so a
  reopened menu cannot inherit it.

The peek's side of the removal — the retention input these menus used to feed —
lives in `tests/test_topbar_peek.py`.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
SESSION_MENU_JS = STATIC_JS / "session-menu.js"

NODE = shutil.which("node")

# The whole page surface the module can reach. The menu is one `innerHTML` write
# and one delegated click listener, so the stub only has to hold the three
# elements it looks up by id, the page globals its registry reads, and the
# handlers its rows dispatch to.
HARNESS_STUBS = r"""
var MULTI_WORKSPACE = true;
var sessionGroups = [{ group_id: 'g1' }];

function isMultiWorkspaceEnabled() { return MULTI_WORKSPACE; }

/* shared.js's own escaper, copied rather than neutered: the rows' values reach
   the parser below through it, so a stub that did not escape would let a label
   containing markup rewrite the rows the assertions read. */
function escHtml(value) {
    return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

const UI_CHEVRON_RIGHT_ICON = '<svg data-icon="chevron"></svg>';

const calls = { actions: [], refreshLists: 0 };
function record(name) { return (...args) => calls.actions.push({ name, args: args.length }); }

function refreshWorkspaceMenuLists() { calls.refreshLists += 1; }
const openNewSessionSelector = record('openNewSessionSelector');
const saveActiveWorkspaceSession = record('saveActiveWorkspaceSession');
const saveActiveWorkspaceSessionAs = record('saveActiveWorkspaceSessionAs');
const saveAllWorkspaceSessions = record('saveAllWorkspaceSessions');
const saveWorkspace = record('saveWorkspace');
const renameCurrentWorkspace = record('renameCurrentWorkspace');
const createAndOpenWorkspace = record('createAndOpenWorkspace');
const closeThisWorkspaceWindow = record('closeThisWorkspaceWindow');
const closeCurrentWorkspace = record('closeCurrentWorkspace');

/* Timers are driven by hand. The close grace is a duration the module owns, and
   a test that slept for it would be both slow and flaky about the one thing it
   is asserting -- that the close is deferred at all. These declarations shadow
   Node's own globals for the module too, which is the point. */
const timers = new Map();
let nextTimerId = 1;
function setTimeout(fn, ms) {
    const id = nextTimerId++;
    timers.set(id, { fn, ms });
    return id;
}
function clearTimeout(id) { timers.delete(id); }
function pendingDelays() { return [...timers.values()].map(entry => entry.ms); }
function runTimers() {
    const due = [...timers.values()];
    timers.clear();
    due.forEach(entry => entry.fn());
}

function fakeClassList() {
    const names = new Set();
    return {
        names,
        add: name => names.add(name),
        remove: name => names.delete(name),
        toggle: (name, on) => { if (on) { names.add(name); } else { names.delete(name); } },
        contains: name => names.has(name)
    };
}

function fakeElement(id) {
    const element = {
        id,
        _html: '',
        disabled: false,
        attributes: {},
        classList: fakeClassList(),
        listeners: {},
        addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); },
        setAttribute(name, value) { this.attributes[name] = value; },
        removeAttribute(name) { delete this.attributes[name]; },
        /* The panel's rows are markup, not nodes, so `querySelector` answers a
           stand-in that only has to be focusable -- which is the whole of what
           the module does with it. */
        contains: node => node === document.activeElement && focusedSection !== '',
        querySelector(selector) {
            const match = /^\[data-session-menu-section="(\w+)"\]$/.exec(selector);
            if (!match) { return null; }
            return { focus: () => { focusedSection = match[1]; document.activeElement = { section: match[1] }; } };
        }
    };
    /* Writing the panel's markup destroys the row that had focus, and the
       browser hands focus to the body -- which is the whole reason the module
       has to put it back. Modelling that here is what stops the hand-back test
       passing on a value nothing ever cleared. */
    Object.defineProperty(element, 'innerHTML', {
        get() { return this._html; },
        set(value) {
            this._html = value;
            focusedSection = '';
            document.activeElement = null;
        }
    });
    return element;
}

/* Which section row holds focus, or '' for "focus is not in the panel". */
var focusedSection = '';

const byId = new Map();
/* Only the ids the page really ships exist up front; anything else the module
   asks for is answered null, exactly as a document would answer for a row that
   is not currently rendered. */
['sessionMenuRoot', 'sessionMenuBtn', 'sessionMenu'].forEach(id => byId.set(id, fakeElement(id)));

const document = {
    activeElement: null,
    getElementById: id => byId.get(id) || null
};

function panel() { return byId.get('sessionMenu'); }
function root() { return byId.get('sessionMenuRoot'); }
function button() { return byId.get('sessionMenuBtn'); }
function isOpen() { return root().classList.contains('open'); }

/* The rendered panel, read back as the reader meets it. Attribute values are
   escaped, so an opening tag really does end at its first '>'. */
function parseButtons(html) {
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
        const label = /<span class="session-menu-section-label">([\s\S]*?)<\/span>/.exec(match[2]);
        rows.push({
            dataset,
            attrs,
            id: attrs.id || '',
            label: label ? label[1].trim() : match[2].trim(),
            disabled: / disabled(?=[\s>])/.test(match[1] + ' '),
            section: dataset.sessionMenuSection || '',
            caret: /class="session-menu-section-caret" aria-hidden="true"/.test(match[2]),
            action: dataset.sessionMenuAction || ''
        });
    }
    return rows;
}

function rows() { return parseButtons(panel().innerHTML); }
function heads() { return rows().filter(row => row.section); }
function items() { return rows().filter(row => !row.section); }
function listContainers() {
    return [...panel().innerHTML.matchAll(/<div id="(\w+)" role="group"/g)].map(m => m[1]);
}

/* A row as the delegated listeners see it: `closest` is the only thing the
   module asks of an event target, so what a gesture means stays the module's
   decision and never the harness's. */
function targetFor(row) {
    const node = { dataset: row.dataset, disabled: row.disabled };
    return {
        closest: selector => {
            if (selector === '[data-session-menu-section]') { return row.section ? node : null; }
            if (selector === '[data-session-menu-action]') { return row.action ? node : null; }
            return null;
        }
    };
}

function fire(type, row) {
    const listeners = panel().listeners[type] || [];
    listeners.forEach(fn => fn({
        preventDefault() {}, stopPropagation() {}, target: targetFor(row)
    }));
}

function press(row) { fire('click', row); }
function hover(row) { fire('mouseover', row); }

/* mouseenter/mouseleave answer for the whole root subtree, so the harness fires
   them on the root exactly as the browser would -- moving between the button,
   the panel and a flyout is not a leave. */
function fireOnRoot(type) { (root().listeners[type] || []).forEach(fn => fn({})); }
function pointerLeaves() { fireOnRoot('mouseleave'); }
function pointerReturns() { fireOnRoot('mouseenter'); }

function headRow(key) {
    const row = heads().find(candidate => candidate.section === key);
    if (!row) { throw new Error(`no section row for ${key}`); }
    return row;
}

/* The gesture the reader actually makes: pointing at the row. */
function hoverSection(key) { hover(headRow(key)); }
function pressSection(key) { press(headRow(key)); }

/* Tab onto the row, which is the keyboard's half of the same gesture. */
function focusSection(key) {
    focusedSection = key;
    document.activeElement = { section: key };
    fire('focusin', headRow(key));
}

function pressItem(action) {
    const row = items().find(candidate => candidate.action === action);
    if (!row) { throw new Error(`no row for action ${action}`); }
    press(row);
    return row;
}

/* Open the menu the way the header button does. The page wires the panel once
   at boot, so the harness does too -- wiring per open would stack listeners and
   make every press fire twice, which reads as a chevron that never opens. */
let wired = false;
function openMenu(options) {
    const opts = options || {};
    if (Object.prototype.hasOwnProperty.call(opts, 'multiWorkspace')) {
        MULTI_WORKSPACE = opts.multiWorkspace;
    }
    if (Object.prototype.hasOwnProperty.call(opts, 'groups')) {
        sessionGroups = opts.groups;
    }
    if (!wired) {
        wireSessionMenu();
        wired = true;
    }
    toggleSessionMenu({ preventDefault() {}, stopPropagation() {} });
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for session menu tests")
class SessionMenuTestCase(unittest.TestCase):
    """Shared runner: the real module, a stub page, and one menu."""

    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + SESSION_MENU_JS.read_text(encoding="utf-8")
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


class SessionMenuDisclosureTestCase(SessionMenuTestCase):
    """The whole row is the control, and pointing at it is the gesture."""

    def test_an_opened_menu_offers_two_rows_and_nothing_else(self):
        result = self._run_node(
            """
            openMenu();
            report({
                open: isOpen(),
                expanded: button().attributes['aria-expanded'],
                sections: heads().map(row => row.section),
                collapsed: heads().map(row => row.attrs['aria-expanded']),
                labels: heads().map(row => row.label),
                haspopup: heads().map(row => row.attrs['aria-haspopup']),
                carets: heads().map(row => row.caret),
                items: items().length,
                listRefreshes: calls.refreshLists
            });
            """
        )
        self.assertEqual(result["open"], True)
        self.assertEqual(result["expanded"], "true")
        # One row per section, and each one names the section it opens.
        self.assertEqual(result["sections"], ["sessions", "workspace"])
        self.assertEqual(result["collapsed"], ["false", "false"])
        self.assertEqual(result["labels"], ["Sessions", "Workspace"])
        # The row is the control: it announces the submenu itself rather than
        # sitting beside a separate opener.
        self.assertEqual(result["haspopup"], ["menu", "menu"])
        # The chevron rides inside it as a marker, never as a second control.
        self.assertEqual(result["carets"], [True, True])
        # Nothing is emitted per section until one is pointed at.
        self.assertEqual(result["items"], 0)
        # And a menu opened for Save Session costs no live-workspace fetch.
        self.assertEqual(result["listRefreshes"], 0)

    def test_pointing_at_a_row_reveals_that_section_and_closes_the_other(self):
        """No click anywhere in here: hovering is the whole gesture, the way a
        menu opens a submenu."""
        result = self._run_node(
            """
            openMenu();
            hoverSection('sessions');
            const afterSessions = {
                actions: items().map(row => row.action),
                expanded: heads().map(row => row.attrs['aria-expanded'])
            };
            hoverSection('workspace');
            const afterWorkspace = {
                actions: items().map(row => row.action),
                expanded: heads().map(row => row.attrs['aria-expanded'])
            };
            report({ afterSessions, afterWorkspace });
            """
        )
        self.assertEqual(
            result["afterSessions"]["actions"],
            ["importSession", "saveSession", "saveSessionAs", "saveAllSessions"],
        )
        self.assertEqual(result["afterSessions"]["expanded"], ["true", "false"])
        # At most one section open at a time: the Sessions rows are gone, not
        # merely pushed down the panel.
        self.assertNotIn("importSession", result["afterWorkspace"]["actions"])
        self.assertEqual(result["afterWorkspace"]["expanded"], ["false", "true"])

    def test_a_press_and_a_focus_open_the_same_section_a_hover_does(self):
        """Three ways in, one open. The pointer is not the only way to reach a
        menu row, and a press must not undo what the hover that preceded it
        just did."""
        result = self._run_node(
            """
            openMenu();
            pressSection('workspace');
            const pressed = items().map(row => row.action);
            closeSessionMenu();
            toggleSessionMenu({ preventDefault() {}, stopPropagation() {} });
            focusSection('sessions');
            report({ pressed, focused: items().map(row => row.action) });
            """
        )
        self.assertIn("saveWorkspace", result["pressed"])
        self.assertEqual(
            result["focused"],
            ["importSession", "saveSession", "saveSessionAs", "saveAllSessions"],
        )

    def test_pointing_again_at_the_open_row_changes_nothing(self):
        """Opening is idempotent, and it has to be: the render replaces the row
        under the pointer, which the browser reports as a fresh pointer event on
        the new node. A toggle here would flicker the section shut under a
        pointer that never moved."""
        result = self._run_node(
            """
            openMenu();
            hoverSection('sessions');
            const opened = items().length;
            hoverSection('sessions');
            pressSection('sessions');
            report({
                opened,
                stillOpen: items().length,
                expanded: heads().map(row => row.attrs['aria-expanded'])
            });
            """
        )
        self.assertGreater(result["opened"], 0)
        self.assertEqual(result["stillOpen"], result["opened"])
        self.assertEqual(result["expanded"], ["true", "false"])

    def test_pointing_at_a_revealed_item_does_not_close_its_section(self):
        """The items sit under the row that revealed them, so reaching one means
        the pointer leaves that row. Only a *section* row moves the expansion."""
        result = self._run_node(
            """
            openMenu();
            hoverSection('workspace');
            const opened = items().length;
            items().forEach(hover);
            report({ opened, afterHoveringItems: items().length });
            """
        )
        self.assertGreater(result["opened"], 0)
        self.assertEqual(result["afterHoveringItems"], result["opened"])

    def test_a_keyboard_open_hands_focus_back_to_the_row_that_asked(self):
        """The render replaces that row, and focus goes to the body with it —
        so Tab would restart at the top of the document. A hover must not take
        focus off anything, so the hand-back is conditional on the panel having
        had it."""
        result = self._run_node(
            """
            openMenu();
            focusSection('workspace');
            const afterKeyboard = focusedSection;
            closeSessionMenu();
            toggleSessionMenu({ preventDefault() {}, stopPropagation() {} });
            focusedSection = '';
            document.activeElement = null;
            hoverSection('sessions');
            report({ afterKeyboard, afterHover: focusedSection });
            """
        )
        self.assertEqual(result["afterKeyboard"], "workspace")
        self.assertEqual(result["afterHover"], "")

    def test_closing_the_menu_drops_the_expansion(self):
        """An expansion is a pointer gesture inside one opening of the menu:
        never workspace state, never persisted, and so needing no invalidation
        hook anywhere."""
        result = self._run_node(
            """
            openMenu();
            hoverSection('workspace');
            const beforeClose = items().length;
            closeSessionMenu();
            const closed = { open: isOpen(), expanded: button().attributes['aria-expanded'] };
            toggleSessionMenu({ preventDefault() {}, stopPropagation() {} });
            report({
                beforeClose,
                closed,
                reopenedItems: items().length,
                reopenedExpanded: heads().map(row => row.attrs['aria-expanded'])
            });
            """
        )
        self.assertGreater(result["beforeClose"], 0)
        self.assertEqual(result["closed"], {"open": False, "expanded": "false"})
        self.assertEqual(result["reopenedItems"], 0)
        self.assertEqual(result["reopenedExpanded"], ["false", "false"])

    def test_the_button_toggles_rather_than_reopening(self):
        result = self._run_node(
            """
            openMenu();
            const opened = isOpen();
            toggleSessionMenu({ preventDefault() {}, stopPropagation() {} });
            report({ opened, closed: isOpen(), expanded: button().attributes['aria-expanded'] });
            """
        )
        self.assertEqual(result, {"opened": True, "closed": False, "expanded": "false"})


class SessionMenuPointerLeaveTestCase(SessionMenuTestCase):
    """The pointer leaving the menu closes it, the way the top bar's reveal
    closes when the pointer leaves the bar."""

    def test_the_pointer_leaving_closes_the_menu_and_drops_the_expansion(self):
        result = self._run_node(
            """
            openMenu();
            hoverSection('workspace');
            pointerLeaves();
            const beforeGrace = { open: isOpen(), pending: pendingDelays().length };
            runTimers();
            const afterGrace = { open: isOpen(), expanded: button().attributes['aria-expanded'] };
            /* Reopening shows the expansion went with it. */
            openMenu();
            report({
                beforeGrace,
                afterGrace,
                reopened: heads().map(row => row.attrs['aria-expanded'])
            });
            """
        )
        # The close is deferred, not immediate: the panel hangs below the button
        # and a pointer travelling between them is briefly over neither.
        self.assertEqual(result["beforeGrace"], {"open": True, "pending": 1})
        self.assertEqual(result["afterGrace"], {"open": False, "expanded": "false"})
        self.assertEqual(result["reopened"], ["false", "false"])

    def test_a_pointer_that_comes_back_keeps_the_menu(self):
        """Crossing the gap between the button and the panel is a leave followed
        by a return, so the grace has to be cancellable or the menu would close
        on the way into it."""
        result = self._run_node(
            """
            openMenu();
            pointerLeaves();
            pointerReturns();
            const pending = pendingDelays().length;
            runTimers();
            report({ pending, open: isOpen() });
            """
        )
        self.assertEqual(result, {"pending": 0, "open": True})

    def test_a_closed_menu_schedules_nothing(self):
        result = self._run_node(
            """
            openMenu();
            toggleSessionMenu({ preventDefault() {}, stopPropagation() {} });
            pointerLeaves();
            report({ open: isOpen(), pending: pendingDelays().length });
            """
        )
        self.assertEqual(result, {"open": False, "pending": 0})

    def test_a_reopened_menu_does_not_inherit_the_pending_close(self):
        """Escape, an outside click and a row's own action all close the menu
        while the grace may be in flight; a timer that outlived its opening
        would shut the next one under the pointer."""
        result = self._run_node(
            """
            openMenu();
            pointerLeaves();
            const leftPending = pendingDelays().length;
            closeSessionMenu();
            openMenu();
            const pending = pendingDelays().length;
            runTimers();
            report({ leftPending, pending, open: isOpen() });
            """
        )
        self.assertEqual(result, {"leftPending": 1, "pending": 0, "open": True})


class SessionMenuWorkspaceSectionTestCase(SessionMenuTestCase):
    """The Workspace section: its gate, its lists, and what they cost."""

    def test_the_workspace_lists_are_fetched_when_that_section_is_expanded(self):
        """Not when the menu opens. The two lists are the only thing in here
        that costs a request, and a menu opened for Save Session should not pay
        for them."""
        result = self._run_node(
            """
            openMenu();
            const onOpen = calls.refreshLists;
            hoverSection('sessions');
            const afterSessions = calls.refreshLists;
            hoverSection('workspace');
            report({
                onOpen,
                afterSessions,
                afterWorkspace: calls.refreshLists,
                containers: listContainers()
            });
            """
        )
        self.assertEqual(result["onOpen"], 0)
        self.assertEqual(result["afterSessions"], 0)
        self.assertEqual(result["afterWorkspace"], 1)
        # The lists stay nested inside the section rather than growing chevrons
        # of their own — two levels of disclosure is the reachability problem
        # this menu exists to remove.
        self.assertEqual(
            result["containers"], ["openWorkspaceList", "moveWorkspaceList"]
        )

    def test_the_multi_workspace_rows_are_gated_and_save_workspace_is_not(self):
        result = self._run_node(
            """
            openMenu({ multiWorkspace: false });
            hoverSection('workspace');
            const off = { actions: items().map(row => row.action), containers: listContainers() };
            closeSessionMenu();
            openMenu({ multiWorkspace: true });
            hoverSection('workspace');
            const on = { actions: items().map(row => row.action), containers: listContainers() };
            report({ off, on });
            """
        )
        self.assertEqual(result["off"]["actions"], ["saveWorkspace"])
        self.assertEqual(result["off"]["containers"], [])
        self.assertEqual(
            result["on"]["actions"],
            [
                "saveWorkspace",
                "renameWorkspace",
                "newWorkspace",
                # Close *window* and close *workspace* are separate verbs with
                # separate persistence effects, so the menu offers both.
                "closeWorkspaceWindow",
                "closeWorkspace",
            ],
        )

    def test_save_workspace_is_unavailable_with_nothing_live_to_save(self):
        """Disabled means unavailable, not busy — and a disabled row sends
        nothing when it is pressed."""
        result = self._run_node(
            """
            openMenu({ groups: [] });
            hoverSection('workspace');
            const row = items().find(candidate => candidate.action === 'saveWorkspace');
            press(row);
            report({ disabled: row.disabled, actions: calls.actions.map(call => call.name) });
            """
        )
        self.assertEqual(result["disabled"], True)
        self.assertEqual(result["actions"], [])

    def test_the_save_workspace_row_is_painted_rather_than_rebuilt(self):
        """`syncSessionMenuState()` runs on every session-tab render, so it
        writes the one attribute instead of rebuilding the panel — a rebuild
        would refetch the live workspace list each time and take the row the
        pointer is on out from under it."""
        result = self._run_node(
            """
            openMenu();
            hoverSection('workspace');
            const built = calls.refreshLists;
            byId.set('saveWorkspaceItem', fakeElement('saveWorkspaceItem'));
            sessionGroups = [];
            syncSessionMenuState();
            const emptied = byId.get('saveWorkspaceItem').disabled;
            sessionGroups = [{ group_id: 'g1' }];
            syncSessionMenuState();
            report({
                built,
                emptied,
                refilled: byId.get('saveWorkspaceItem').disabled,
                refreshesAfter: calls.refreshLists
            });
            """
        )
        self.assertEqual(result["emptied"], True)
        self.assertEqual(result["refilled"], False)
        self.assertEqual(result["refreshesAfter"], result["built"])


class SessionMenuActionsTestCase(SessionMenuTestCase):
    """Every actionable row states its action, and the menu goes first."""

    def test_each_session_row_dispatches_its_own_handler(self):
        result = self._run_node(
            """
            const seen = [];
            for (const action of ['importSession', 'saveSession', 'saveSessionAs', 'saveAllSessions']) {
                calls.actions.length = 0;
                openMenu();
                hoverSection('sessions');
                pressItem(action);
                seen.push({ action, ran: calls.actions.map(call => call.name), open: isOpen() });
            }
            report(seen);
            """
        )
        self.assertEqual(
            [(row["action"], row["ran"]) for row in result],
            [
                ("importSession", ["openNewSessionSelector"]),
                ("saveSession", ["saveActiveWorkspaceSession"]),
                ("saveSessionAs", ["saveActiveWorkspaceSessionAs"]),
                ("saveAllSessions", ["saveAllWorkspaceSessions"]),
            ],
        )
        # The menu closes before the handler runs: these open modals and write
        # the session line, and a panel still standing over them is the state
        # this menu replaced.
        self.assertEqual([row["open"] for row in result], [False, False, False, False])

    def test_each_workspace_row_dispatches_its_own_handler(self):
        result = self._run_node(
            """
            const seen = [];
            for (const action of [
                'saveWorkspace', 'renameWorkspace', 'newWorkspace',
                'closeWorkspaceWindow', 'closeWorkspace'
            ]) {
                calls.actions.length = 0;
                openMenu();
                hoverSection('workspace');
                pressItem(action);
                seen.push({ action, ran: calls.actions.map(call => call.name), open: isOpen() });
            }
            report(seen);
            """
        )
        self.assertEqual(
            [(row["action"], row["ran"]) for row in result],
            [
                ("saveWorkspace", ["saveWorkspace"]),
                ("renameWorkspace", ["renameCurrentWorkspace"]),
                ("newWorkspace", ["createAndOpenWorkspace"]),
                ("closeWorkspaceWindow", ["closeThisWorkspaceWindow"]),
                ("closeWorkspace", ["closeCurrentWorkspace"]),
            ],
        )
        self.assertEqual([row["open"] for row in result], [False] * 5)

    def test_the_rows_that_report_their_own_progress_are_handed_their_button(self):
        """`saveActiveWorkspaceSession` and its neighbours set `aria-busy` on
        the control that was pressed, so the row is the argument — a row that
        sent nothing would leave the busy state on nothing."""
        result = self._run_node(
            """
            openMenu();
            hoverSection('sessions');
            pressItem('saveSession');
            const withButton = calls.actions[0].args;
            calls.actions.length = 0;
            openMenu();
            hoverSection('workspace');
            pressItem('renameWorkspace');
            report({ withButton, withoutButton: calls.actions[0].args });
            """
        )
        self.assertEqual(result["withButton"], 1)
        self.assertEqual(result["withoutButton"], 0)

    def test_reaching_a_section_row_is_not_an_action(self):
        """A section row opens its own submenu and sends nothing, by hover or by
        press — the menu stays open either way."""
        result = self._run_node(
            """
            openMenu();
            hoverSection('workspace');
            pressSection('sessions');
            report({ actions: calls.actions.map(call => call.name), open: isOpen() });
            """
        )
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["open"], True)


class SessionMenuMarkupTestCase(SessionMenuTestCase):
    """The hooks the page and the stylesheet key on."""

    def test_the_rows_keep_the_ids_the_page_still_looks_up(self):
        result = self._run_node(
            """
            openMenu();
            hoverSection('sessions');
            const sessions = items().map(row => row.id);
            hoverSection('workspace');
            report({ sessions, workspace: items().map(row => row.id), lists: listContainers() });
            """
        )
        # An id only where something still looks the row up; Import Session is
        # reached by nothing, so it carries none.
        self.assertEqual(
            result["sessions"],
            ["", "saveSessionMenuItem", "saveSessionAsMenuItem", "saveAllSessionsMenuItem"],
        )
        self.assertEqual(
            result["workspace"],
            [
                "saveWorkspaceItem",
                "renameWorkspaceItem",
                "newWorkspaceItem",
                "closeWorkspaceWindowItem",
                "closeWorkspaceItem",
            ],
        )
        self.assertEqual(result["lists"], ["openWorkspaceList", "moveWorkspaceList"])

    def test_the_move_heading_carries_the_scope_span_terminals_js_fills(self):
        result = self._run_node(
            """
            openMenu();
            hoverSection('workspace');
            const html = panel().innerHTML;
            report({
                scope: html.includes('<span class="workspace-submenu-scope" id="moveWorkspaceScope" hidden>'),
                hint: html.includes('<span class="workspace-submenu-hint">Alt+W</span>'),
                items: items().every(row => row.attrs.role === 'menuitem'),
                submenus: (html.match(/class="session-menu-section-body" role="menu"/g) || []).length,
                groups: (html.match(/role="group"/g) || []).length
            });
            """
        )
        self.assertEqual(result["scope"], True)
        self.assertEqual(result["hint"], True)
        self.assertEqual(result["items"], True)
        # The revealed section is a submenu of the panel it opens beside, and
        # only the one section is open.
        self.assertEqual(result["submenus"], 1)
        # The two dynamic lists inside it stay groups.
        self.assertEqual(result["groups"], 2)

    def test_the_two_section_rows_hold_still_whichever_is_open(self):
        """The reason the items open beside the panel rather than inside it: an
        accordion pushed the other row down, so swapping between the two moved
        the row being aimed at out from under the pointer."""
        result = self._run_node(
            """
            openMenu();
            const collapsed = heads().map(row => row.section);
            hoverSection('sessions');
            const withSessions = { order: heads().map(row => row.section), count: heads().length };
            hoverSection('workspace');
            const withWorkspace = { order: heads().map(row => row.section), count: heads().length };
            report({ collapsed, withSessions, withWorkspace });
            """
        )
        # Two rows, in one order, for the whole life of one opening — the
        # section's items are never among the panel's own children.
        self.assertEqual(result["collapsed"], ["sessions", "workspace"])
        self.assertEqual(result["withSessions"], {"order": ["sessions", "workspace"], "count": 2})
        self.assertEqual(result["withWorkspace"], {"order": ["sessions", "workspace"], "count": 2})

    def test_a_label_carrying_markup_cannot_rewrite_the_panel(self):
        result = self._run_node(
            """
            openMenu();
            hoverSection('sessions');
            const before = items().length;
            SESSION_MENU_SECTIONS[0].rows = () => ([
                { kind: 'item', id: '', label: '</button><button data-session-menu-action="saveWorkspace">x', action: 'importSession' }
            ]);
            renderSessionMenu();
            report({ before, actions: items().map(row => row.action) });
            """
        )
        self.assertEqual(result["before"], 4)
        self.assertEqual(result["actions"], ["importSession"])


if __name__ == "__main__":
    unittest.main()
