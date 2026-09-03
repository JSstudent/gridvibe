"""The two window-level chords item 3 settled, driven through their own handlers.

``terminals.js`` cannot be loaded in a ``vm`` -- it reaches most of the page at
the top level -- so the two ``document.addEventListener('keydown', ...)``
registrations under test are lifted out of the source by a scanner and executed
against a stub page. That is still *running* the handler rather than reading it:
what is asserted is which action a keystroke produces, not how the file spells
the condition.

Three things are pinned here, and each is a bug this chord pair was chosen to
avoid rather than a restatement of the code:

- **AltGr is not Ctrl+Alt.** Windows delivers ``AltGr`` to the page with both
  ``ctrlKey`` and ``altKey`` set, and on the Slovenian layout that made this
  item necessary ``AltGr+Q`` is a backslash -- typed constantly in Windows
  paths. Both handlers must let it through untouched.
- **The launcher chord is matched on the physical key**, so a layout that
  prints something else on it still reaches the launcher, and the old dead-key
  chord is gone rather than kept in parallel: it opened the launcher *and* left
  the layout's accent composer armed, so the next character typed came out
  accented.
- **Ctrl+Shift+E is a toggle that never silently does nothing.** It enters the
  in-place editor, cancels it while editing, and on a file that cannot be
  edited says why -- in the same sentence the disabled Edit button's tooltip
  carries, taken from the same function.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "web" / "static" / "js"
TERMINALS_JS = STATIC_JS / "terminals.js"
EDITOR_JS = STATIC_JS / "explorer-editor.js"

NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const [terminalsPath, editorPath, specJson] = process.argv.slice(2);
const spec = JSON.parse(specJson);

/* ── Lifting a handler out of terminals.js ──
   A plain brace count would be fooled by the braces and slashes inside string
   literals, comments and regular expressions, so the scanner walks the source
   in the states that matter. It is used twice: to find the end of a
   `document.addEventListener('keydown', ...)` call, and to find the end of a
   named function declaration. */
function scanTo(source, start, open, close) {
    let depth = 0;
    let i = start;
    let prevSignificant = '';
    while (i < source.length) {
        const ch = source[i];
        const next = source[i + 1];
        if (ch === '/' && next === '/') {
            i = source.indexOf('\n', i);
            if (i === -1) { throw new Error('unterminated line comment'); }
            continue;
        }
        if (ch === '/' && next === '*') {
            i = source.indexOf('*/', i + 2);
            if (i === -1) { throw new Error('unterminated block comment'); }
            i += 2;
            continue;
        }
        if (ch === '"' || ch === "'" || ch === '`') {
            i += 1;
            while (i < source.length && source[i] !== ch) {
                i += source[i] === '\\' ? 2 : 1;
            }
            i += 1;
            prevSignificant = ch;
            continue;
        }
        if (ch === '/' && REGEX_PRECEDERS.includes(prevSignificant)) {
            /* A regex literal: the previous significant character cannot end an
               expression, so this slash cannot be division. */
            i += 1;
            while (i < source.length && source[i] !== '/') {
                if (source[i] === '\\') { i += 1; }
                if (source[i] === '[') {
                    while (i < source.length && source[i] !== ']') { i += 1; }
                }
                i += 1;
            }
            i += 1;
            prevSignificant = '/';
            continue;
        }
        if (ch === open) { depth += 1; }
        if (ch === close) {
            depth -= 1;
            if (depth === 0) { return i; }
        }
        if (!WHITESPACE.test(ch)) { prevSignificant = ch; }
        i += 1;
    }
    throw new Error('unbalanced source');
}

const REGEX_PRECEDERS = ['(', ',', '=', ':', '[', '!', '&', '|', '?', '{', '}', ';', '+', 'n'];
const WHITESPACE = /\s/;

function keydownRegistrations(source) {
    const needle = "document.addEventListener('keydown'";
    const out = [];
    let from = 0;
    for (;;) {
        const at = source.indexOf(needle, from);
        if (at === -1) { return out; }
        const openParen = source.indexOf('(', at);
        const end = scanTo(source, openParen, '(', ')');
        out.push(source.slice(at, end + 1) + ';');
        from = end;
    }
}

function namedFunction(source, name) {
    const at = source.indexOf('function ' + name + '(');
    if (at === -1) { throw new Error('missing function ' + name); }
    const openBrace = source.indexOf('{', source.indexOf(')', at));
    const end = scanTo(source, openBrace, '{', '}');
    return source.slice(at, end + 1);
}

const terminalsSource = fs.readFileSync(terminalsPath, 'utf8');
const editorSource = fs.readFileSync(editorPath, 'utf8');

/* The page surface the two handlers can reach. Everything they call that
   decides something is the real code (the blocking-target guard, the disabled
   reason); everything they call that *acts* is recorded. */
const calls = { launcher: 0, enter: [], cancel: [], toasts: [], defaults: 0, stopped: 0 };

class Element {
    constructor(match, contentEditable) {
        this._match = match || '';
        this.isContentEditable = Boolean(contentEditable);
    }
    closest(selector) {
        return selector.split(',').some(part => part.trim() === this._match) ? this : null;
    }
}

const handlers = [];
const sandbox = {
    Element,
    console,
    terminals: [],
    goToSettings() { calls.launcher += 1; },
    enterExplorerEditMode(index) { calls.enter.push(index); },
    cancelExplorerEdit(index) { calls.cancel.push(index); },
    showTerminalToast(message) { calls.toasts.push(message); },
    findExplorerShortcutTargetIndex: () =>
        (spec.explorerIndex === undefined ? -1 : spec.explorerIndex),
    explorerEditState: pane => (pane && pane._explorerEdit ? pane._explorerEdit : null),
    document: {
        addEventListener(type, fn) { handlers.push(fn); }
    }
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);

/* Named, never stubbed: the disabled-reason sentence and the focused-pane
   exemption are the two decisions these handlers delegate, and a stub would
   only prove the stub was called. */
vm.runInContext(namedFunction(editorSource, 'explorerEditDisabledTooltip'), sandbox);
vm.runInContext(namedFunction(terminalsSource, 'isEditableShortcutTarget'), sandbox);
vm.runInContext(namedFunction(terminalsSource, 'isPaneShortcutBlockingTarget'), sandbox);

const wanted = keydownRegistrations(terminalsSource)
    .filter(text => text.includes(spec.marker));
if (wanted.length !== 1) {
    throw new Error('expected exactly one keydown handler containing ' + spec.marker
        + ', found ' + wanted.length);
}
vm.runInContext(wanted[0], sandbox);

if (spec.pane && spec.explorerIndex !== undefined) {
    sandbox.terminals[spec.explorerIndex] = Object.assign({}, spec.pane);
}

const event = Object.assign({
    altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, repeat: false,
    key: '', code: ''
}, spec.event);
event.target = spec.target ? new Element(spec.target.match, spec.target.contentEditable) : null;
event.preventDefault = () => { calls.defaults += 1; };
event.stopPropagation = () => { calls.stopped += 1; };

handlers[handlers.length - 1](event);

process.stdout.write(JSON.stringify(calls));
"""


@unittest.skipUnless(NODE, "Node.js is required for keybind handler tests")
class ShortcutHandlerTestCase(unittest.TestCase):
    marker = ""

    def _press(self, **spec):
        spec.setdefault("marker", self.marker)
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "press.js"
            script_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    str(TERMINALS_JS),
                    str(EDITOR_JS),
                    json.dumps(spec),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"handler harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class LauncherChordTestCase(ShortcutHandlerTestCase):
    """Alt+Q opens the launcher, and AltGr+Q still types a backslash."""

    marker = "goToSettings()"

    def test_alt_q_opens_the_launcher(self):
        calls = self._press(event={"altKey": True, "code": "KeyQ"})

        self.assertEqual(calls["launcher"], 1)
        # The page claims the chord, so nothing else acts on it.
        self.assertEqual(calls["defaults"], 1)

    def test_altgr_q_is_not_the_launcher_chord(self):
        # AltGr reaches the page as Ctrl+Alt, and AltGr+Q is a backslash on the
        # layout this chord was picked for -- a Windows path must stay typeable.
        calls = self._press(event={"altKey": True, "ctrlKey": True, "code": "KeyQ"})

        self.assertEqual(calls["launcher"], 0)
        self.assertEqual(calls["defaults"], 0)

    def test_the_old_dead_key_chord_is_gone(self):
        # It was removed outright rather than kept in parallel: it opened the
        # launcher and left the layout's accent composer armed behind it.
        calls = self._press(event={"altKey": True, "code": "Backquote"})

        self.assertEqual(calls["launcher"], 0)

    def test_a_focused_terminal_still_reaches_the_launcher(self):
        # xterm's helper textarea is the keyboard target of every focused pane;
        # the plain editable guard would strand the user in it.
        calls = self._press(
            event={"altKey": True, "code": "KeyQ"},
            target={"match": ".xterm-helper-textarea"},
        )

        self.assertEqual(calls["launcher"], 1)

    def test_a_real_text_field_swallows_it(self):
        calls = self._press(
            event={"altKey": True, "code": "KeyQ"},
            target={"match": "input"},
        )

        self.assertEqual(calls["launcher"], 0)

    def test_a_held_key_does_not_repeat_the_launcher(self):
        calls = self._press(event={"altKey": True, "code": "KeyQ", "repeat": True})

        self.assertEqual(calls["launcher"], 0)


class EditChordTestCase(ShortcutHandlerTestCase):
    """Ctrl+Shift+E toggles the in-place editor, and never silently no-ops."""

    marker = "enterExplorerEditMode(index)"

    EDITABLE = {"_explorerMode": "file", "_explorerFileEditable": True}

    def _chord(self, **overrides):
        event = {"ctrlKey": True, "shiftKey": True, "code": "KeyE"}
        event.update(overrides)
        return event

    def test_it_enters_edit_mode_on_an_editable_file(self):
        calls = self._press(event=self._chord(), explorerIndex=2, pane=self.EDITABLE)

        self.assertEqual(calls["enter"], [2])
        self.assertEqual(calls["cancel"], [])
        self.assertEqual(calls["defaults"], 1)

    def test_it_cancels_while_editing(self):
        pane = dict(self.EDITABLE, _explorerEdit={"dirty": False})
        calls = self._press(event=self._chord(), explorerIndex=0, pane=pane)

        self.assertEqual(calls["cancel"], [0])
        self.assertEqual(calls["enter"], [])

    def test_a_file_that_cannot_be_edited_says_why(self):
        pane = {
            "_explorerMode": "file",
            "_explorerFileEditable": False,
            "_explorerFileEditBlockReason": "mixed_line_endings",
        }
        calls = self._press(event=self._chord(), explorerIndex=0, pane=pane)

        self.assertEqual(calls["enter"], [])
        # The sentence the disabled Edit button's own tooltip carries -- both
        # read explorerEditDisabledTooltip(), so they cannot drift apart.
        self.assertEqual(
            calls["toasts"], ["Mixed line endings are view-only in this version"]
        )

    def test_a_directory_listing_claims_nothing(self):
        # There is no Edit button on a listing either, so there is nothing to
        # report and no reason to take the chord away from the browser.
        calls = self._press(
            event=self._chord(), explorerIndex=0, pane={"_explorerMode": "directory"}
        )

        self.assertEqual(calls["enter"], [])
        self.assertEqual(calls["toasts"], [])
        self.assertEqual(calls["defaults"], 0)

    def test_no_explorer_pane_is_a_no_op(self):
        calls = self._press(event=self._chord())

        self.assertEqual(calls["enter"], [])
        self.assertEqual(calls["defaults"], 0)

    def test_altgr_shift_e_is_not_the_edit_chord(self):
        calls = self._press(
            event=self._chord(altKey=True), explorerIndex=0, pane=self.EDITABLE
        )

        self.assertEqual(calls["enter"], [])
        self.assertEqual(calls["defaults"], 0)

    def test_ctrl_e_without_shift_is_not_the_edit_chord(self):
        calls = self._press(
            event=self._chord(shiftKey=False), explorerIndex=0, pane=self.EDITABLE
        )

        self.assertEqual(calls["enter"], [])


if __name__ == "__main__":
    unittest.main()
