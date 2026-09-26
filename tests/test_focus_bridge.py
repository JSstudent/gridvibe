"""The workspace page's half of "show this session": the focus bridge.

Lifted out of ``terminals.js`` and executed in Node against a stubbed page,
because what matters is behaviour a source assertion cannot see:

- **A tool never discards unsaved work to switch a tab.** Where a click would
  ask the person, a tool's request is refused with the reason, and the tab it
  was on stays on screen.
- **The answer is what the page shows, not what it was asked.** A switch the
  page's own `switchGroup` declined, a load that never finished, and a pane
  that is no longer in the group are all refusals.
- **Focus is read back, never assumed.** A pane that is on screen but did
  not take keyboard focus (an explorer or browser card, a terminal whose focus
  threw) is reported as visible and unfocused.
- **Only the workspace page holding the tab says it holds it.**
"""

import unittest

from tests.test_dashboard_targeting import (
    NODE,
    STATIC_JS,
    NodeHarnessTestCase,
    _js_const_source,
    _js_function_source,
)

TERMINALS_JS = (STATIC_JS / "terminals.js").read_text(encoding="utf-8")


def _js_object_source(script: str, name: str) -> str:
    """One top-level ``const name = { ... };`` object literal, brace-matched."""
    start = script.index(f"const {name} = {{")
    depth = 0
    for index in range(script.index("{", start), len(script)):
        if script[index] == "{":
            depth += 1
        elif script[index] == "}":
            depth -= 1
            if depth == 0:
                return script[start:index + 1] + ";"
    raise AssertionError(f"unbalanced braces in {name}")


BRIDGE_SOURCE = "\n".join(
    [
        _js_const_source(TERMINALS_JS, "FOCUS_BRIDGE_POLL_MS"),
        _js_function_source(TERMINALS_JS, "focusBridgeBlocker"),
        _js_function_source(TERMINALS_JS, "waitForVisiblePane"),
        _js_object_source(TERMINALS_JS, "focusBridge"),
    ]
)

STUBS = """
/* Short, so a load that never finishes answers in milliseconds. */
const FOCUS_BRIDGE_SETTLE_MS = 50;
const currentWorkspaceId = 'ws-1';
let sessionGroups = [{ group_id: 'g-1' }, { group_id: 'g-2' }];
let activeGroupId = 'g-1';
let visibleGroupId = 'g-1';
let sessionIds = ['pane-a'];
let dirty = false;
let copying = false;
let switchDeclines = false;
let paneInGroup = true;
let paneTakesFocus = true;
const calls = { switched: [], focused: [] };
const card = { contains: element => element === 'xterm-textarea' };
const document = {
    activeElement: 'body',
    getElementById: id => (id === 'tc-3' ? card : null)
};

function hasActiveExplorerFilesystemOperationForSessions() { return copying; }
function hasAnyDirtyExplorerEdit() { return dirty; }
async function switchGroup(groupId) {
    calls.switched.push(groupId);
    if (switchDeclines) return;
    activeGroupId = groupId;
    visibleGroupId = groupId;
}
function resolveSessionTarget(sessionId) {
    if (!paneInGroup) return null;
    return { groupId: visibleGroupId, index: 3, active: true, sessionId };
}
function focusPaneForArrival(index) {
    calls.focused.push(index);
    if (paneTakesFocus) document.activeElement = 'xterm-textarea';
}
"""


@unittest.skipUnless(NODE, "Node.js is required for the focus bridge tests")
class FocusBridgeTestCase(NodeHarnessTestCase):
    def _run(self, body: str):
        return self._run_node(
            STUBS
            + BRIDGE_SOURCE
            + "\n(async () => {\n"
            + body
            + "\nprocess.stdout.write(JSON.stringify({ answer, calls, activeGroupId }));\n})();"
        )

    def test_a_held_tab_is_switched_to_and_its_pane_focused(self):
        result = self._run("const answer = await focusBridge.activate('g-2', 'pane-b');")

        self.assertEqual(
            result["answer"],
            {"ok": True, "activeGroupId": "g-2", "paneVisible": True, "focused": True},
        )
        self.assertEqual(result["calls"], {"switched": ["g-2"], "focused": [3]})

    def test_a_pane_that_did_not_take_focus_is_visible_and_unfocused(self):
        """An explorer or browser card is not focusable; saying so is the answer."""
        result = self._run(
            "paneTakesFocus = false;\nconst answer = await focusBridge.activate('g-2', 'pane-b');"
        )

        self.assertEqual(
            result["answer"],
            {"ok": True, "activeGroupId": "g-2", "paneVisible": True, "focused": False},
        )

    def test_the_tab_already_showing_is_not_switched_again(self):
        result = self._run("const answer = await focusBridge.activate('g-1', 'pane-a');")

        self.assertTrue(result["answer"]["ok"])
        self.assertEqual(result["calls"]["switched"], [])
        self.assertEqual(result["calls"]["focused"], [3])

    def test_a_group_alone_switches_the_tab_and_focuses_nothing(self):
        result = self._run("const answer = await focusBridge.activate('g-2');")

        self.assertEqual(
            result["answer"],
            {"ok": True, "activeGroupId": "g-2", "paneVisible": False, "focused": False},
        )
        self.assertEqual(result["calls"]["focused"], [])

    def test_unsaved_editor_work_blocks_the_switch_rather_than_asking(self):
        result = self._run(
            "dirty = true;\nconst answer = await focusBridge.activate('g-2', 'pane-b');"
        )

        self.assertFalse(result["answer"]["ok"])
        self.assertIn("unsaved changes", result["answer"]["error"])
        # Nothing was switched, so nothing could have been discarded.
        self.assertEqual(result["calls"]["switched"], [])
        self.assertEqual(result["activeGroupId"], "g-1")

    def test_a_copy_in_flight_blocks_the_switch(self):
        result = self._run(
            "copying = true;\nconst answer = await focusBridge.activate('g-2');"
        )

        self.assertFalse(result["answer"]["ok"])
        self.assertIn("copy or delete", result["answer"]["error"])
        self.assertEqual(result["calls"]["switched"], [])

    def test_a_switch_the_page_declined_is_not_reported_as_done(self):
        result = self._run(
            "switchDeclines = true;\nconst answer = await focusBridge.activate('g-2');"
        )

        self.assertFalse(result["answer"]["ok"])
        self.assertEqual(result["answer"]["activeGroupId"], "g-1")
        self.assertIn("did not switch", result["answer"]["error"])

    def test_a_pane_that_left_the_group_is_a_refusal_not_a_focus(self):
        result = self._run(
            "paneInGroup = false;\nconst answer = await focusBridge.activate('g-2', 'pane-gone');"
        )

        self.assertFalse(result["answer"]["ok"])
        self.assertIn("not in this session", result["answer"]["error"])
        self.assertEqual(result["calls"]["focused"], [])

    def test_a_tab_this_window_does_not_hold_is_refused(self):
        result = self._run("const answer = await focusBridge.activate('g-9');")

        self.assertFalse(result["answer"]["ok"])
        self.assertEqual(result["calls"]["switched"], [])

    def test_only_this_workspace_page_holds_its_tabs(self):
        result = self._run(
            "const answer = ["
            "focusBridge.holds('ws-1', 'g-2'),"
            "focusBridge.holds('ws-2', 'g-2'),"
            "focusBridge.holds('ws-1', 'g-9')];"
        )

        self.assertEqual(result["answer"], [True, False, False])


if __name__ == "__main__":
    unittest.main()
