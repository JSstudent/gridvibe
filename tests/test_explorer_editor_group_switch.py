"""Switching session groups must not cost an open in-place edit.

A group switch caches the visible grid by detaching its cards into a document
fragment, and the restore re-applies the Source view through the search
machinery. Two things went wrong there, and both are executed here in Node
against the real modules rather than asserted as source text:

* ``renderExplorerSource`` rebuilt the Source panel from the on-disk content
  while the editor's textarea was living in it, dropping the draft and leaving
  Save/Cancel pointing at nothing — the user's only way out being Cancel.
* Every control lookup in the editor and in voice capture goes through
  ``document.getElementById``, which cannot see a detached card. A capture
  ``_stopAllVoice()`` ended during the switch therefore left a stale recording
  ring and a Save button disabled by a binding that had already expired.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
# The Source render reads the active tab's Markdown folds, and the tab records
# live in explorer-tabs.js since the tab-domain split — the page loads the pair
# together, so the harness evaluates the pair together.
TABS_JS = STATIC_JS / "explorer-tabs.js"
EDITOR_JS = STATIC_JS / "explorer-editor.js"

NODE = shutil.which("node")

# A DOM small enough to read and large enough for the two functions under
# test: elements are looked up by id, and every mutation they make is
# observable on the returned object.
DOM_STUB = """
function makeElement(id) {
    return {
        id,
        innerHTML: '',
        title: '',
        hidden: false,
        disabled: false,
        dataset: {},
        style: { setProperty() {}, removeProperty() {} },
        classes: new Set(),
        classList: {
            add(name) { this.owner.classes.add(name); },
            remove(name) { this.owner.classes.delete(name); },
            contains(name) { return this.owner.classes.has(name); },
            toggle(name, force) {
                const on = force === undefined ? !this.owner.classes.has(name) : force;
                if (on) { this.owner.classes.add(name); } else { this.owner.classes.delete(name); }
                return on;
            }
        },
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        setAttribute() {},
        appendChild() {},
        focus() {}
    };
}

function makeSandbox(nodes) {
    const sandbox = {
        console,
        document: {
            getElementById: id => nodes[id] || null,
            querySelector: selector => nodes[selector] || null,
            querySelectorAll: () => [],
            addEventListener() {},
            body: { dataset: {}, addEventListener() {} }
        },
        window: {
            addEventListener() {},
            setTimeout,
            clearTimeout,
            matchMedia: () => ({ matches: false }),
            localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
            requestAnimationFrame: () => 0
        },
        navigator: {},
        setTimeout,
        clearTimeout,
        requestAnimationFrame: () => 0,
        terminals: [],
        sessionIds: [],
        escHtml: value => String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    };
    sandbox.globalThis = sandbox;
    return sandbox;
}
"""


class NodeHarnessMixin:
    def _run_node(self, harness: str, *args: str):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(DOM_STUB + harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


@unittest.skipUnless(NODE, "Node.js is required for explorer editor tests")
class RenderExplorerSourceEditGuardTestCase(NodeHarnessMixin, unittest.TestCase):
    """The Source panel belongs to the editor until the edit is torn down."""

    def _render(self, *, editing: bool):
        return self._run_node(
            """
            const fs = require('fs');
            const vm = require('vm');
            const editing = process.argv[4] === 'true';

            const code = makeElement('explorer-code-0');
            const nodes = { 'explorer-code-0': code };
            const sandbox = makeSandbox(nodes);
            // Owned by explorer-overview.js; repaints marks onto fresh rows.
            sandbox.applyExplorerChangeMarks = () => {};
            vm.createContext(sandbox);
            [process.argv[2], process.argv[3]].forEach(path => {
                vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
            });

            sandbox.terminals[0] = {
                _explorerMode: 'file',
                _explorerFilePath: 'notes.txt',
                _explorerFileContent: 'on disk\\n',
                _explorerFileLanguage: '',
                _explorerFilePlain: true,
                _explorerEdit: editing
                    ? { draft: 'typed but not saved', dirty: true }
                    : null
            };

            // What a live edit session leaves in the Source panel.
            code.innerHTML = '<textarea id="explorer-edit-textarea-0"></textarea>';
            sandbox.renderExplorerSource(0);
            process.stdout.write(JSON.stringify({ panel: code.innerHTML }));
            """,
            str(VIEWER_JS),
            str(TABS_JS),
            "true" if editing else "false",
        )

    def test_an_open_edit_keeps_its_textarea(self):
        # This is the group-switch case: the cached-view restore re-applies the
        # Source view, which lands here with the editor still open.
        result = self._render(editing=True)
        self.assertIn("explorer-edit-textarea-0", result["panel"])
        self.assertNotIn("on disk", result["panel"])

    def test_leaving_edit_mode_rebuilds_the_panel_from_the_file(self):
        result = self._render(editing=False)
        self.assertNotIn("explorer-edit-textarea-0", result["panel"])
        self.assertIn("on disk", result["panel"])


@unittest.skipUnless(NODE, "Node.js is required for explorer editor tests")
class ExplorerEditorReattachResyncTestCase(NodeHarnessMixin, unittest.TestCase):
    """Controls come back from a detached cache derived from live state."""

    def _resync(self, *, edit_state: str, recording: bool):
        return self._run_node(
            """
            const fs = require('fs');
            const vm = require('vm');
            const editState = process.argv[3];
            const recording = process.argv[4] === 'true';

            const host = makeElement('actions-0');
            const list = makeElement('explorer-list-0');
            // The controls the freshly rendered markup resolves to.
            const micWrapper = makeElement('voice-control-0');
            const mic = makeElement('explorer-voice-0');
            const nodes = {
                '[data-explorer-editor-actions="0"]': host,
                '[data-explorer-editor-voice-control="0"]': micWrapper,
                'explorer-voice-0': mic,
                'explorer-list-0': list
            };
            const sandbox = makeSandbox(nodes);

            // Collaborators owned by other modules, recorded rather than run.
            const calls = { updateVoiceBtn: [], setBtnsDisabled: [] };
            sandbox.VOICE_MIC_ICON = '<svg data-mic></svg>';
            sandbox.EXPLORER_SAVE_ICON = '<svg data-save></svg>';
            sandbox.EXPLORER_CANCEL_ICON = '<svg data-cancel></svg>';
            sandbox.EXPLORER_EDIT_ICON = '<svg data-edit></svg>';
            sandbox._voiceServiceStatus = { enabled: true };
            sandbox._voiceState = recording ? { 0: { recording: true } } : {};
            sandbox._voiceActiveIndex = recording ? 0 : -1;
            sandbox._voicePrefs = { pttEnabled: false, pttKeybind: '' };
            sandbox._updateVoiceBtn = (index, on) => calls.updateVoiceBtn.push([index, on]);
            sandbox._setVoiceBtnsDisabled = active => calls.setBtnsDisabled.push(active);
            sandbox._wireVoiceHoldToTalkElements = () => {};
            sandbox._toggleVoice = () => {};
            sandbox._stopVoice = () => Promise.resolve();
            sandbox.showTerminalToast = () => {};
            sandbox.applyExplorerSearch = () => {};
            sandbox.updateExplorerEditTabDirty = () => {};
            sandbox.openGenericConfirmModal = () => Promise.resolve(false);

            vm.createContext(sandbox);
            vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

            sandbox.terminals[0] = {
                _explorerFileEditable: true,
                _explorerEdit: editState === 'none' ? null : {
                    dirty: true,
                    saving: false,
                    // 'settled' is a capture whose binding already expired
                    // while the card was detached.
                    voice: editState === 'settled'
                        ? null
                        : { epoch: 1, phase: editState, settleTimer: null }
                }
            };

            host.innerHTML = 'stale markup from before the switch';
            sandbox.resyncExplorerEditorOnAttach(0);
            process.stdout.write(JSON.stringify({
                markup: host.innerHTML,
                calls,
                micDisabled: mic.disabled,
                micTitle: mic.title
            }));
            """,
            str(EDITOR_JS),
            edit_state,
            "true" if recording else "false",
        )

    def _save_button(self, markup: str) -> str:
        tail = markup[markup.index("explorer-edit-save"):]
        return tail[: tail.index("</button>")]

    def test_a_settled_binding_comes_back_savable_and_not_recording(self):
        # The capture was stopped by _stopAllVoice() during the switch and its
        # binding expired while the card was detached, so the mic must not
        # return wearing its recording ring and Save must be live again.
        result = self._resync(edit_state="settled", recording=False)
        self.assertIn("explorer-voice-0", result["markup"])
        self.assertNotIn("stale markup", result["markup"])
        self.assertNotIn("disabled", self._save_button(result["markup"]))
        self.assertEqual(result["calls"]["updateVoiceBtn"], [[0, False]])
        self.assertFalse(result["micDisabled"])

    def test_a_still_recording_capture_keeps_its_ring_and_holds_save(self):
        result = self._resync(edit_state="recording", recording=True)
        self.assertIn("disabled", self._save_button(result["markup"]))
        self.assertEqual(result["calls"]["updateVoiceBtn"], [[0, True]])

    def test_a_settling_capture_holds_save_and_parks_the_mic(self):
        result = self._resync(edit_state="settling", recording=False)
        self.assertIn("disabled", self._save_button(result["markup"]))
        self.assertTrue(result["micDisabled"])
        self.assertIn("transcript", result["micTitle"])

    def test_a_pane_with_no_open_edit_is_left_alone(self):
        result = self._resync(edit_state="none", recording=False)
        self.assertEqual(result["markup"], "stale markup from before the switch")
        self.assertEqual(result["calls"]["updateVoiceBtn"], [])


@unittest.skipUnless(NODE, "Node.js is required for explorer editor tests")
class DiscardAllExplorerEditsTestCase(NodeHarnessMixin, unittest.TestCase):
    """The group-level guard must leave edit mode, not just drop the state.

    Its callers cache these cards as they stand, so an edit state cleared
    without rebuilding the view is what came back looking stuck.
    """

    def _discard(self, *, confirm: bool):
        return self._run_node(
            """
            const fs = require('fs');
            const vm = require('vm');
            const confirm = process.argv[3] === 'true';

            const nodes = {};
            [0, 1].forEach(index => {
                nodes[`[data-explorer-editor-actions="${index}"]`] = makeElement(`actions-${index}`);
                nodes[`explorer-list-${index}`] = makeElement(`explorer-list-${index}`);
            });
            const sandbox = makeSandbox(nodes);

            const calls = { renderSource: [], chromeDisabled: [], focused: [] };
            sandbox.VOICE_MIC_ICON = '<svg data-mic></svg>';
            sandbox.EXPLORER_SAVE_ICON = '<svg data-save></svg>';
            sandbox.EXPLORER_CANCEL_ICON = '<svg data-cancel></svg>';
            sandbox.EXPLORER_EDIT_ICON = '<svg data-edit></svg>';
            sandbox._voiceServiceStatus = { enabled: true };
            sandbox._voiceState = {};
            sandbox._voiceActiveIndex = -1;
            sandbox._voicePrefs = { pttEnabled: false, pttKeybind: '' };
            sandbox._updateVoiceBtn = () => {};
            sandbox._setVoiceBtnsDisabled = () => {};
            sandbox._wireVoiceHoldToTalkElements = () => {};
            sandbox._toggleVoice = () => {};
            sandbox._stopVoice = () => Promise.resolve();
            sandbox.showTerminalToast = () => {};
            sandbox.captureScrollMetrics = () => null;
            sandbox.applyScrollMetrics = () => {};
            sandbox.applyExplorerSearch = () => {};
            sandbox.renderExplorerSource = index => calls.renderSource.push(index);
            sandbox.openGenericConfirmModal = () => Promise.resolve(confirm);
            // The Edit button the exit path would focus, if it focused one.
            sandbox.document.querySelector = selector => {
                if (selector.startsWith('[data-explorer-edit=')) {
                    return { focus: () => calls.focused.push(selector) };
                }
                return nodes[selector] || null;
            };

            vm.createContext(sandbox);
            vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

            const wrapped = sandbox.setExplorerEditChromeDisabled;
            sandbox.setExplorerEditChromeDisabled = (index, disabled) => {
                calls.chromeDisabled.push([index, disabled]);
                return wrapped(index, disabled);
            };

            // Pane 0 has unsaved changes, pane 1 is an untouched editor.
            sandbox.terminals[0] = {
                _explorerFileEditable: true,
                _explorerFileName: 'dirty.txt',
                _explorerEdit: { dirty: true, saving: false, voice: null }
            };
            sandbox.terminals[1] = {
                _explorerFileEditable: true,
                _explorerFileName: 'clean.txt',
                _explorerEdit: { dirty: false, saving: false, voice: null }
            };

            sandbox.confirmDiscardAllExplorerEdits('Switching sessions').then(proceed => {
                process.stdout.write(JSON.stringify({
                    proceed,
                    stillEditing: sandbox.terminals.map(pane => Boolean(pane._explorerEdit)),
                    calls
                }));
            });
            """,
            str(EDITOR_JS),
            "true" if confirm else "false",
        )

    def test_confirming_leaves_edit_mode_on_every_open_editor(self):
        result = self._discard(confirm=True)
        self.assertTrue(result["proceed"])
        # Both panes — the clean one too, which is what the dialog promises.
        self.assertEqual(result["stillEditing"], [False, False])
        self.assertEqual(sorted(result["calls"]["renderSource"]), [0, 1])
        self.assertEqual(
            sorted(result["calls"]["chromeDisabled"]), [[0, False], [1, False]]
        )
        # The caller is about to detach this grid; no focus is stolen.
        self.assertEqual(result["calls"]["focused"], [])

    def test_declining_leaves_every_editor_exactly_as_it_was(self):
        result = self._discard(confirm=False)
        self.assertFalse(result["proceed"])
        self.assertEqual(result["stillEditing"], [True, True])
        self.assertEqual(result["calls"]["renderSource"], [])


if __name__ == "__main__":
    unittest.main()
