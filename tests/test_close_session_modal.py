"""The three-outcome close prompt, run rather than read.

`close-session-modal.js` is require()-able and takes its DOM from an injected
runtime, so both halves execute in Node: what the prompt says, when it is not
shown at all, and what each way out of it resolves to.

It is shared by two pages -- the workspace window's session tab and the agent
dashboard's session card -- and that is the reason these are behavioural rather
than source-text assertions. The two surfaces read from different payloads and
sit in different windows; the only thing keeping them asking the same question
about the same irreversible act is this module, so what it decides is what has
to be pinned.

Four things are pinned because getting them wrong is silent:

- **Cancel is the default.** Escape, the backdrop and a second open all resolve
  to "keep the session". A prompt that resolved to anything else on a dismissal
  would end live shells on a stray keystroke.
- **An empty session list means "ask".** It is also what a *failed* status
  lookup produces, so reading it as "nothing is connected" would skip the
  prompt precisely when the page knows least.
- **The copy names the terminals**, because "3 terminals (2 connected)" is the
  fact that makes the prompt worth reading.
- **A page without the modal still resolves**, rather than leaving the caller
  awaiting a promise nothing will settle.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
CLOSE_SESSION_MODAL_JS = STATIC_JS / "close-session-modal.js"

NODE = shutil.which("node")

# The whole DOM the adapter can reach: five elements, a class list and a
# listener registry. Nothing here renders; the controller only toggles
# `visible`, writes one line of copy and resolves a promise.
HARNESS_STUBS = """
function fakeClassList() {
    const names = new Set();
    return {
        names,
        add(name) { names.add(name); },
        remove(name) { names.delete(name); },
        contains(name) { return names.has(name); }
    };
}

function fakeElement(id) {
    const handlers = new Map();
    return {
        id,
        textContent: '',
        attributes: {},
        focused: 0,
        classList: fakeClassList(),
        setAttribute(name, value) { this.attributes[name] = value; },
        addEventListener(type, handler) {
            if (!handlers.has(type)) handlers.set(type, new Set());
            handlers.get(type).add(handler);
        },
        fire(type, event) { [...(handlers.get(type) || [])].forEach(h => h(event)); },
        focus() { this.focused += 1; }
    };
}

function harness(options) {
    const settings = options || {};
    const ids = [
        'closeSessionConfirmModal',
        'closeSessionConfirmCopy',
        'closeSessionConfirmCancel',
        'closeSessionConfirmSave',
        'closeSessionConfirmAccept'
    ];
    const byId = new Map();
    if (!settings.pageWithoutModal) {
        ids.forEach(id => byId.set(id, fakeElement(id)));
    }
    const documentHandlers = new Map();
    const controller = closeSessionModal.create({
        getElement: id => byId.get(id) || null,
        addDocumentListener: (type, handler) => {
            if (!documentHandlers.has(type)) documentHandlers.set(type, new Set());
            documentHandlers.get(type).add(handler);
        },
        /* The real one defers focus a task; here it runs at once so a case can
           assert on it without waiting. */
        defer: callback => callback()
    });
    controller.init();
    return {
        controller,
        byId,
        modal: byId.get('closeSessionConfirmModal'),
        copy: byId.get('closeSessionConfirmCopy'),
        press: id => byId.get(id)?.fire('click', {}),
        key: key => [...(documentHandlers.get('keydown') || [])].forEach(h => h({ key }))
    };
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the close-session prompt tests")
class CloseSessionModalNodeTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + "\nconst closeSessionModal = require(process.argv[2]);\n"
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(CLOSE_SESSION_MODAL_JS)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class CloseSessionPromptPolicyTestCase(CloseSessionModalNodeTestCase):
    """The pure half — no DOM, no promise."""

    def test_a_session_with_nothing_connected_never_asks(self):
        result = self._run_node(
            """
            const { skipDecision, CLOSE } = closeSessionModal.policy;
            report({
                allDead: skipDecision([{ status: 'disconnected' }, { status: 'error' }]),
                oneAlive: skipDecision([{ status: 'disconnected' }, { status: 'connected' }]),
                close: CLOSE
            });
            """
        )
        # Nothing left to lose, so the dialog would only ever have one answer.
        self.assertEqual(result["allDead"], result["close"])
        self.assertIsNone(result["oneAlive"])

    def test_not_knowing_is_asked_about_rather_than_assumed(self):
        """An empty list is what a failed status lookup produces, and it is
        indistinguishable from a genuinely empty group. Reading it as "nothing
        is connected" would skip the prompt exactly when the page knows
        least."""
        result = self._run_node(
            """
            const { skipDecision } = closeSessionModal.policy;
            report({
                empty: skipDecision([]),
                missing: skipDecision(undefined),
                notAList: skipDecision('nonsense')
            });
            """
        )
        self.assertIsNone(result["empty"])
        self.assertIsNone(result["missing"])
        self.assertIsNone(result["notAList"])

    def test_the_question_names_the_terminals_it_is_about(self):
        result = self._run_node(
            """
            const { promptCopy, promptName } = closeSessionModal.policy;
            report({
                several: promptCopy('API work', 2, 3),
                one: promptCopy('API work', 1, 1),
                unknown: promptCopy('API work', 0, 0),
                named: promptName({ name: 'API work', group_id: 'g1' }),
                byId: promptName({ name: '', group_id: 'g1' }),
                nameless: promptName(null)
            });
            """
        )
        self.assertEqual(
            result["several"], 'Close "API work" and its 3 terminals (2 connected)?'
        )
        self.assertEqual(
            result["one"], 'Close "API work" and its 1 terminal (1 connected)?'
        )
        # A count that could not be established asks the short question rather
        # than inventing a zero.
        self.assertEqual(result["unknown"], 'Close "API work"?')
        self.assertEqual(result["named"], "API work")
        self.assertEqual(result["byId"], "g1")
        self.assertEqual(result["nameless"], "this session")


class CloseSessionPromptControllerTestCase(CloseSessionModalNodeTestCase):
    """The adapter — driven through the same listeners the page installs."""

    def test_each_button_resolves_to_its_own_decision(self):
        result = self._run_node(
            """
            const { CANCEL, CLOSE, SAVE_AND_CLOSE } = closeSessionModal.policy;
            const decisions = {};
            for (const [name, id] of [
                ['cancel', 'closeSessionConfirmCancel'],
                ['save', 'closeSessionConfirmSave'],
                ['accept', 'closeSessionConfirmAccept']
            ]) {
                const page = harness();
                const answer = page.controller.open({ group: { name: 'API work' }, connectedCount: 1, totalCount: 1 });
                page.press(id);
                decisions[name] = await answer;
                decisions[`${name}Visible`] = page.modal.classList.contains('visible');
            }
            report({ decisions, CANCEL, CLOSE, SAVE_AND_CLOSE });
            """
        )
        self.assertEqual(result["decisions"]["cancel"], result["CANCEL"])
        self.assertEqual(result["decisions"]["save"], result["SAVE_AND_CLOSE"])
        self.assertEqual(result["decisions"]["accept"], result["CLOSE"])
        for name in ("cancel", "save", "accept"):
            self.assertFalse(result["decisions"][f"{name}Visible"])

    def test_every_dismissal_keeps_the_session(self):
        result = self._run_node(
            """
            const backdrop = harness();
            const byBackdrop = backdrop.controller.open({ group: { name: 'A' }, connectedCount: 1, totalCount: 1 });
            backdrop.modal.fire('click', { target: backdrop.modal });

            const escape = harness();
            const byEscape = escape.controller.open({ group: { name: 'A' }, connectedCount: 1, totalCount: 1 });
            escape.key('Escape');

            /* A second open must not strand the first: it keeps its session,
               exactly as Escape would. */
            const reopened = harness();
            const first = reopened.controller.open({ group: { name: 'A' }, connectedCount: 1, totalCount: 1 });
            const second = reopened.controller.open({ group: { name: 'B' }, connectedCount: 1, totalCount: 1 });
            reopened.press('closeSessionConfirmAccept');

            report({
                backdrop: await byBackdrop,
                escape: await byEscape,
                superseded: await first,
                second: await second,
                cancel: closeSessionModal.policy.CANCEL,
                close: closeSessionModal.policy.CLOSE,
                copy: reopened.copy.textContent
            });
            """
        )
        self.assertEqual(result["backdrop"], result["cancel"])
        self.assertEqual(result["escape"], result["cancel"])
        self.assertEqual(result["superseded"], result["cancel"])
        self.assertEqual(result["second"], result["close"])
        self.assertIn('"B"', result["copy"])

    def test_a_key_that_is_not_escape_and_a_press_outside_the_card_change_nothing(self):
        result = self._run_node(
            """
            const page = harness();
            const answer = page.controller.open({ group: { name: 'A' }, connectedCount: 2, totalCount: 2 });
            page.key('Enter');
            page.modal.fire('click', { target: page.byId.get('closeSessionConfirmCopy') });
            const stillOpen = page.controller.isVisible();
            page.press('closeSessionConfirmCancel');
            report({ stillOpen, decision: await answer, copy: page.copy.textContent });
            """
        )
        self.assertTrue(result["stillOpen"])
        self.assertEqual(result["decision"], "cancel")
        self.assertEqual(
            result["copy"], 'Close "A" and its 2 terminals (2 connected)?'
        )

    def test_cancel_takes_the_focus_not_the_destructive_button(self):
        """The prompt is reached by pressing a ×, so a focused danger default
        turns a stray Enter into the thing being confirmed."""
        result = self._run_node(
            """
            const page = harness();
            page.controller.open({ group: { name: 'A' }, connectedCount: 1, totalCount: 1 });
            report({
                cancel: page.byId.get('closeSessionConfirmCancel').focused,
                accept: page.byId.get('closeSessionConfirmAccept').focused,
                save: page.byId.get('closeSessionConfirmSave').focused,
                ariaHidden: page.modal.attributes['aria-hidden']
            });
            """
        )
        self.assertEqual(result["cancel"], 1)
        self.assertEqual(result["accept"], 0)
        self.assertEqual(result["save"], 0)
        self.assertEqual(result["ariaHidden"], "false")

    def test_a_page_without_the_dialog_resolves_rather_than_hanging(self):
        result = self._run_node(
            """
            const page = harness({ pageWithoutModal: true });
            report({
                wired: page.controller.isVisible(),
                decision: await page.controller.open({ group: { name: 'A' } })
            });
            """
        )
        self.assertFalse(result["wired"])
        self.assertEqual(result["decision"], "cancel")


if __name__ == "__main__":
    unittest.main()
