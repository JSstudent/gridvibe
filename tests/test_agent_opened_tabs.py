"""A session tab an agent opens does not take the window over.

A window used to switch to every tab that appeared in it, which is right when
the person launched it and wrong when an agent did: `launch_panes` into an open
workspace, or `move_session` without `show`, moved the person off the tab they
were looking at. The group record now says who opened it (`opened_by`), and a
window switches only for the person's.

- **The page's rule is executed**, from the real `loadSessionGroups` in Node: a
  new agent-opened tab joins the strip and the window stays; a new tab the
  person opened, or one from a server that predates the field, is switched to;
  a window showing nothing still picks the newest tab.
- **The server sets the field** from what every tool request already carries:
  a launch's tool flag, a move's calling pane. A body cannot state it for
  itself; only a restore replays it, from its snapshot.
- **It survives a restart**: captured into the snapshot, re-validated on read
  (anything but ``"agent"`` is the person's), and carried by the restore launch.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.test_runtime_state import _LiveAppMixin
from web import api
from web import navigation as web_navigation
from web import runtime_state as web_runtime_state
from web import workspaces as web_workspaces

ROOT = Path(__file__).resolve().parent.parent
TERMINALS_JS = ROOT / "web" / "static" / "js" / "terminals.js"
NODE = shutil.which("node")


def _function_source(script: str, name: str) -> str:
    """One top-level function's source, brace-matched, `async` included."""
    start = script.index(f"function {name}(")
    if script[max(0, start - 6):start] == "async ":
        start -= 6
    depth = 0
    for index in range(script.index("{", script.index(")", start)), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1]
    raise AssertionError(f"unbalanced braces in {name}")


LOAD_GROUPS_HARNESS = r"""
/* The real `loadSessionGroups`, against a window that has already read
   `previous` and is showing `active`, when the server now lists `groups`. */
async function run({ previous, active, groups }) {
    const context = {
        currentWorkspaceId: 'wsp000000001',
        knownGroupIds: previous.slice(),
        activeGroupId: active,
        visibleGroupId: active,
        sessionGroups: [],
        fetch: async () => ({ ok: true, json: async () => ({ groups }) }),
        handleWorkspaceGone: async () => {},
        applyTopbarVisibility: () => {},
        applyAgentDashboardSidebar: () => {},
        agentSidebarIsOpen: () => false,
        setExplorerWorkspaceAppearance: () => {},
        adoptPresentationRevisions: () => {},
        dropCachedGroupView: () => {},
        syncLocationToGroup: () => {},
        renderSessionTabs: () => {},
        encodeURIComponent
    };
    context.getGroupById = id => context.sessionGroups.find(group => group.group_id === id) || null;
    vm.runInNewContext(`${LOAD_SOURCE}\nglobalThis.api = { loadSessionGroups };`, context);
    const changed = await context.api.loadSessionGroups();
    return { changed, active: context.activeGroupId };
}

const G = (group_id, opened_by) => (opened_by === undefined ? { group_id } : { group_id, opened_by });

(async () => {
    const out = {};
    out.agentOpened = await run({
        previous: ['g-1'], active: 'g-1',
        groups: [G('g-1', 'person'), G('g-agent', 'agent')]
    });
    out.personOpened = await run({
        previous: ['g-1'], active: 'g-1',
        groups: [G('g-1', 'person'), G('g-person', 'person')]
    });
    out.predatesTheField = await run({
        previous: ['g-1'], active: 'g-1',
        groups: [G('g-1'), G('g-old')]
    });
    out.bothInOneRead = await run({
        previous: ['g-1'], active: 'g-1',
        groups: [G('g-1', 'person'), G('g-person', 'person'), G('g-agent', 'agent')]
    });
    out.emptyWindow = await run({
        previous: [], active: '',
        groups: [G('g-agent', 'agent')]
    });
    out.activeTabClosed = await run({
        previous: ['g-1', 'g-2'], active: 'g-2',
        groups: [G('g-1', 'person'), G('g-agent', 'agent')]
    });
    console.log(JSON.stringify(out));
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


RENDER_TABS_HARNESS = r"""
/* Just enough DOM for the real `renderSessionTabs`: elements with children,
   classes, a dataset, `closest`, `contains`, `querySelector` by class, and a
   document that tracks which element has focus. */
class FakeElement {
    constructor(tag) {
        this.tagName = tag;
        this.children = [];
        this.parent = null;
        this.dataset = {};
        this.attributes = {};
        this.className = '';
        this.textContent = '';
        const element = this;
        this.classList = {
            contains: name => element.className.split(/\s+/).includes(name),
            add: () => {},
            remove: () => {}
        };
        this.style = {};
    }
    set innerHTML(_value) {
        this.children.forEach(child => { child.parent = null; });
        this.children = [];
    }
    appendChild(child) { child.parent = this; this.children.push(child); return child; }
    setAttribute(name, value) { this.attributes[name] = value; }
    addEventListener() {}
    contains(other) {
        for (let node = other; node; node = node.parent) if (node === this) return true;
        return false;
    }
    closest(selector) {
        const name = selector.replace(/^\./, '');
        for (let node = this; node; node = node.parent) if (node.classList.contains(name)) return node;
        return null;
    }
    querySelector(selector) {
        const name = selector.replace(/^\./, '');
        for (const child of this.children) {
            if (child.classList.contains(name)) return child;
            const found = child.querySelector(selector);
            if (found) return found;
        }
        return null;
    }
    focus() { context.document.activeElement = this; }
}

const container = new FakeElement('div');
const context = {
    sessionGroups: [{ group_id: 'g-1', name: 'One' }, { group_id: 'g-2', name: 'Two' }],
    activeGroupId: 'g-1',
    suppressSessionTabClickUntil: 0,
    syncSessionMenuState: () => {},
    applyTabColour: () => {},
    wireSessionTabDragAndDrop: () => {},
    switchGroup: () => {},
    closeSessionGroup: () => {},
    openSessionTabContextMenu: () => {},
    Date,
    document: {
        activeElement: null,
        getElementById: id => (id === 'sessionTabs' ? container : null),
        createElement: tag => new FakeElement(tag)
    }
};
vm.runInNewContext(`${RENDER_SOURCE}\nglobalThis.api = { renderSessionTabs };`, context);

const describe = element => (element && element.parent
    ? `${element.parent.dataset.groupId}:${element.className}`
    : null);
const out = {};
context.api.renderSessionTabs();
// The person has the second tab's close button focused when an agent's tab
// arrives and the strip is rebuilt around them.
const before = container.children[1].querySelector('.session-tab-close');
before.focus();
context.sessionGroups.push({ group_id: 'g-agent', name: 'Agent', opened_by: 'agent' });
context.api.renderSessionTabs();
out.closeFocus = {
    replaced: context.document.activeElement !== before,
    focused: describe(context.document.activeElement)
};
// Then on the first tab's main button.
container.children[0].querySelector('.session-tab-main').focus();
context.api.renderSessionTabs();
out.mainFocus = describe(context.document.activeElement);
// Focus outside the strip -- the terminal -- is left alone.
const terminal = new FakeElement('textarea');
terminal.focus();
context.api.renderSessionTabs();
out.outside = context.document.activeElement === terminal;
// A focused tab that closed has no replacement to take the focus.
container.children[2].querySelector('.session-tab-main').focus();
const gone = context.document.activeElement;
context.sessionGroups.pop();
context.api.renderSessionTabs();
out.closedTab = context.document.activeElement === gone;
console.log(JSON.stringify(out));
"""


def _run_node_file(script: str) -> dict:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "harness.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [NODE, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout.strip().splitlines()[-1])


@unittest.skipIf(NODE is None, "Node.js is required for the page's tab rule")
class NewTabSwitchTestCase(unittest.TestCase):
    """Executed from the real `loadSessionGroups`."""

    @classmethod
    def setUpClass(cls):
        load = _function_source(TERMINALS_JS.read_text(encoding="utf-8"), "loadSessionGroups")
        cls.out = _run_node_file(
            "const vm = require('vm');\n"
            f"const LOAD_SOURCE = {json.dumps(load)};\n" + LOAD_GROUPS_HARNESS
        )

    def test_a_tab_an_agent_opened_joins_the_strip_and_the_window_stays(self):
        self.assertEqual(self.out["agentOpened"], {"changed": False, "active": "g-1"})

    def test_a_tab_the_person_opened_is_switched_to(self):
        self.assertEqual(self.out["personOpened"], {"changed": True, "active": "g-person"})

    def test_a_record_without_the_field_is_the_persons(self):
        self.assertEqual(self.out["predatesTheField"], {"changed": True, "active": "g-old"})

    def test_the_persons_new_tab_wins_over_an_agents_newer_one(self):
        self.assertEqual(self.out["bothInOneRead"], {"changed": True, "active": "g-person"})

    def test_a_window_showing_nothing_picks_the_newest_tab_whoever_opened_it(self):
        self.assertEqual(self.out["emptyWindow"], {"changed": True, "active": "g-agent"})

    def test_a_window_whose_tab_closed_still_shows_something(self):
        """The tab the person was looking at is gone, so something else has
        to show; that is not a takeover."""
        self.assertEqual(self.out["activeTabClosed"], {"changed": True, "active": "g-agent"})


@unittest.skipIf(NODE is None, "Node.js is required for the tab strip")
class TabStripKeepsFocusTestCase(unittest.TestCase):
    """The window staying on its tab is not enough: the strip is rebuilt when
    an agent's tab arrives, and a tab control the person had focused must not
    lose it. Executed from the real `renderSessionTabs`."""

    @classmethod
    def setUpClass(cls):
        script = TERMINALS_JS.read_text(encoding="utf-8")
        render = "\n\n".join(
            _function_source(script, name)
            for name in (
                "captureSessionTabFocus",
                "restoreSessionTabFocus",
                "renderSessionTabs",
            )
        )
        cls.out = _run_node_file(
            "const vm = require('vm');\n"
            f"const RENDER_SOURCE = {json.dumps(render)};\n" + RENDER_TABS_HARNESS
        )

    def test_a_focused_tab_control_gets_the_focus_back_on_its_replacement(self):
        self.assertEqual(
            self.out["closeFocus"],
            {"replaced": True, "focused": "g-2:session-tab-close"},
        )
        self.assertEqual(self.out["mainFocus"], "g-1:session-tab-main")

    def test_focus_outside_the_strip_is_left_alone(self):
        self.assertTrue(self.out["outside"])

    def test_a_tab_that_closed_hands_the_focus_to_nothing(self):
        self.assertTrue(self.out["closedTab"])


_TERMINAL_PANE = {"title": "Shell", "startup_mode": "terminal"}


class OpenedByRouteTestCase(_LiveAppMixin, unittest.TestCase):
    """The launch and move routes record who opened the tab."""

    OTHER_WORKSPACE = "cccccccccccc"

    def _launch(self, workspace_id="default", **body):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "workspace_id": workspace_id,
                "layout": "single",
                "sessions": [{**_TERMINAL_PANE, "directory": str(self.repo_dir)}],
                **body,
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return api.session_manager.get_group(response.get_json()["group_id"])

    def test_a_launch_from_the_launcher_or_a_window_is_the_persons(self):
        group = self._launch()

        self.assertEqual(group.opened_by, "person")
        self.assertEqual(group.to_dict()["opened_by"], "person")

    def test_a_tool_launch_is_an_agents(self):
        group = self._launch(tool_launch=True)

        self.assertEqual(group.opened_by, "agent")
        listed = self.client.get("/api/session-groups?workspace_id=default").get_json()
        self.assertEqual(
            {item["group_id"]: item["opened_by"] for item in listed["groups"]},
            {group.group_id: "agent"},
        )

    def test_a_body_cannot_state_who_opened_it(self):
        self.assertEqual(self._launch(opened_by="agent").opened_by, "person")
        self.assertEqual(self._launch(tool_launch=True, opened_by="person").opened_by, "agent")

    def test_the_workspace_front_tab_hint_stays_on_the_persons_tab(self):
        person = self._launch()
        self._launch(tool_launch=True)

        workspace = api.session_manager.get_workspace("default")
        self.assertEqual(workspace.active_group_id, person.group_id)

    def test_an_agents_tab_in_an_empty_workspace_is_its_front_tab(self):
        agent = self._launch(tool_launch=True)

        workspace = api.session_manager.get_workspace("default")
        self.assertEqual(workspace.active_group_id, agent.group_id)

    def test_a_move_from_a_window_is_the_persons(self):
        api.session_manager.create_workspace("Other", self.OTHER_WORKSPACE)
        group = self._launch(tool_launch=True)

        response = self.client.post(
            f"/api/session-groups/{group.group_id}/move",
            json={"target_workspace_id": self.OTHER_WORKSPACE},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(api.session_manager.get_group(group.group_id).opened_by, "person")

    def test_a_tools_move_is_an_agents(self):
        api.session_manager.create_workspace("Other", self.OTHER_WORKSPACE)
        group = self._launch()
        caller = api.session_manager.get_group_sessions(group.group_id)[0]

        # The caller's own tab: the lineage gate lets that move without a waiver.
        answer, status = web_navigation.move_group_for_agent(
            group.group_id,
            {
                "target_workspace_id": self.OTHER_WORKSPACE,
                "requested_by_session_id": caller.session_id,
            },
        )

        self.assertEqual(status, 200, answer)
        self.assertTrue(answer["moved"])
        moved = api.session_manager.get_group(group.group_id)
        self.assertEqual(moved.workspace_id, self.OTHER_WORKSPACE)
        self.assertEqual(moved.opened_by, "agent")


class OpenedByRestoreTestCase(_LiveAppMixin, unittest.TestCase):
    """Captured, re-validated and replayed with the rest of the tab."""

    RESTORED_WORKSPACE = "dddddddddddd"

    def _launch(self, **body):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "workspace_id": "default",
                "layout": "single",
                "sessions": [{**_TERMINAL_PANE, "directory": str(self.repo_dir)}],
                **body,
            },
        )
        self.assertEqual(response.status_code, 201, response.get_json())
        return api.session_manager.get_group(response.get_json()["group_id"])

    def _restore(self, snapshot_group):
        body, _warning = web_workspaces._restore_group_request(
            snapshot_group, self.RESTORED_WORKSPACE
        )
        api.session_manager.create_workspace("Restored", self.RESTORED_WORKSPACE)
        response = self.client.post("/api/sessions", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return api.session_manager.get_group(response.get_json()["group_id"])

    def test_an_agents_tab_comes_back_as_an_agents(self):
        group = self._launch(tool_launch=True)
        captured = web_runtime_state._snapshot_group(
            group, api.session_manager.get_group_sessions(group.group_id)
        )
        self.assertEqual(captured["opened_by"], "agent")

        validated = web_runtime_state._validate_group(json.loads(json.dumps(captured)))
        self.assertEqual(validated["opened_by"], "agent")

        self.assertEqual(self._restore(validated).opened_by, "agent")

    def test_the_persons_tab_comes_back_as_the_persons(self):
        group = self._launch()
        captured = web_runtime_state._snapshot_group(
            group, api.session_manager.get_group_sessions(group.group_id)
        )

        restored = self._restore(web_runtime_state._validate_group(captured))

        self.assertEqual(restored.opened_by, "person")

    def test_a_slot_written_before_the_field_or_holding_nonsense_is_the_persons(self):
        group = self._launch(tool_launch=True)
        captured = web_runtime_state._snapshot_group(
            group, api.session_manager.get_group_sessions(group.group_id)
        )
        for stored in (None, "robot", 1, ["agent"]):
            with self.subTest(stored=stored):
                raw = dict(captured)
                if stored is None:
                    raw.pop("opened_by")
                else:
                    raw["opened_by"] = stored

                validated = web_runtime_state._validate_group(raw)

                self.assertIsNotNone(validated)
                self.assertEqual(validated["opened_by"], "person")


if __name__ == "__main__":
    unittest.main()
