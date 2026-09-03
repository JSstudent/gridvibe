"""Behavioral coverage for the native "minimize every window" control.

`minimize-all.js` is require()-able and takes its DOM and its pywebview bridge
from an injected runtime, so both halves run in Node against the real module:
the chord rule is *executed* rather than asserted as source text, and the
adapter is driven through the same `handleKeydown` the page installs.

Three things are pinned here because getting them wrong is silent:

- `!event.ctrlKey` — AltGr reaches the page as Ctrl+Alt on Windows, so a
  chord that does not exclude it fires on characters the user is typing.
- The chord is matched on `event.code`, not `event.key`, so it stays on the
  same physical key whatever the layout prints on it.
- The control is native-mode-only, and a browser page must not even swallow
  the keystroke.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
MINIMIZE_ALL_JS = STATIC_JS / "minimize-all.js"

NODE = shutil.which("node")

# The whole DOM surface the adapter can reach: it asks the runtime for one
# element and only ever flips that element's `hidden` property.
HARNESS_STUBS = """
function stubButton() {
    return { hidden: true };
}

function harness(options) {
    const settings = options || {};
    const button = settings.button === null ? null : stubButton();
    const calls = [];
    const errors = [];
    const bridge = settings.native === false ? null : {
        minimize_all_windows: () => {
            calls.push('minimize_all_windows');
            if (settings.bridgeThrows) throw new Error('bridge exploded');
            return settings.answer === undefined ? { ok: true, minimized: 3 } : settings.answer;
        }
    };
    const control = minimizeAll.create({
        getElement: id => (id === minimizeAll.policy.BUTTON_ID ? button : null),
        getBridge: () => bridge,
        isBlockingTarget: target => Boolean(settings.blocked) && target === 'input',
        logError: message => errors.push(message)
    });
    return { control, button, calls, errors };
}

function keyEvent(overrides) {
    const event = Object.assign({
        code: 'KeyX',
        key: 'x',
        altKey: true,
        ctrlKey: false,
        metaKey: false,
        shiftKey: false,
        repeat: false,
        target: 'body',
        prevented: false
    }, overrides || {});
    event.preventDefault = () => { event.prevented = true; };
    return event;
}
"""


@unittest.skipUnless(NODE, "Node.js is required for the minimize-all tests")
class MinimizeAllNodeTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + "\nconst minimizeAll = require(process.argv[2]);\n"
            + body
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(MINIMIZE_ALL_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class MinimizeAllChordTestCase(MinimizeAllNodeTestCase):
    """The pure half — no DOM, no bridge."""

    def test_the_chord_is_alt_plus_the_physical_x_key(self):
        result = self._run_node(
            """
            const { matchesChord } = minimizeAll.policy;
            process.stdout.write(JSON.stringify({
                plain: matchesChord(keyEvent()),
                code: minimizeAll.policy.CHORD_CODE,
                label: minimizeAll.policy.CHORD_LABEL,
                // event.code is QWERTY-named and reports the physical key, so
                // a layout that prints something else there still matches.
                foreignKeyLabel: matchesChord(keyEvent({ key: '#' })),
                otherKey: matchesChord(keyEvent({ code: 'KeyZ' })),
                noModifier: matchesChord(keyEvent({ altKey: false })),
                nothing: matchesChord(null)
            }));
            """
        )
        self.assertTrue(result["plain"])
        self.assertEqual(result["code"], "KeyX")
        self.assertEqual(result["label"], "Alt+X")
        self.assertTrue(result["foreignKeyLabel"])
        self.assertFalse(result["otherKey"])
        self.assertFalse(result["noModifier"])
        self.assertFalse(result["nothing"])

    def test_altgr_never_reaches_the_control(self):
        result = self._run_node(
            """
            const { matchesChord } = minimizeAll.policy;
            process.stdout.write(JSON.stringify({
                altGr: matchesChord(keyEvent({ ctrlKey: true })),
                meta: matchesChord(keyEvent({ metaKey: true })),
                shifted: matchesChord(keyEvent({ shiftKey: true })),
                held: matchesChord(keyEvent({ repeat: true }))
            }));
            """
        )
        # AltGr arrives as Ctrl+Alt on Windows, and AltGr+X types `#` on the
        # layout this chord was picked for — so excluding Ctrl is what stops
        # a character the user typed from sweeping every window off screen.
        self.assertFalse(result["altGr"])
        self.assertFalse(result["meta"])
        self.assertFalse(result["shifted"])
        # Holding the key must not re-run the batch on every repeat.
        self.assertFalse(result["held"])


class MinimizeAllControlTestCase(MinimizeAllNodeTestCase):
    """The adapter, driven through the handlers the page installs."""

    def test_the_button_appears_only_when_the_native_bridge_is_there(self):
        result = self._run_node(
            """
            const native = harness({});
            const browser = harness({ native: false });
            const shown = native.control.syncControl();
            const hidden = browser.control.syncControl();
            process.stdout.write(JSON.stringify({
                shown,
                hidden,
                nativeHidden: native.button.hidden,
                browserHidden: browser.button.hidden,
                noButton: harness({ button: null }).control.syncControl()
            }));
            """
        )
        self.assertTrue(result["shown"])
        self.assertFalse(result["nativeHidden"])
        # Not rendered rather than rendered-and-disabled: a browser tab cannot
        # minimize its own window.
        self.assertFalse(result["hidden"])
        self.assertTrue(result["browserHidden"])
        self.assertFalse(result["noButton"])

    def test_the_chord_runs_the_batch_once_and_claims_the_keystroke(self):
        result = self._run_node(
            """
            const native = harness({});
            const event = keyEvent();
            const handled = native.control.handleKeydown(event);
            process.stdout.write(JSON.stringify({
                handled,
                prevented: event.prevented,
                calls: native.calls
            }));
            """
        )
        self.assertTrue(result["handled"])
        self.assertTrue(result["prevented"])
        self.assertEqual(result["calls"], ["minimize_all_windows"])

    def test_a_browser_page_neither_acts_nor_swallows_the_keystroke(self):
        result = self._run_node(
            """
            const browser = harness({ native: false });
            const event = keyEvent();
            const handled = browser.control.handleKeydown(event);
            process.stdout.write(JSON.stringify({
                handled,
                prevented: event.prevented,
                calls: browser.calls
            }));
            """
        )
        self.assertFalse(result["handled"])
        # Nothing happened, so nothing was claimed from whatever else on the
        # page might want Alt+X.
        self.assertFalse(result["prevented"])
        self.assertEqual(result["calls"], [])

    def test_a_page_that_declares_a_shortcut_target_blocks_the_chord(self):
        result = self._run_node(
            """
            const blocking = harness({ blocked: true });
            const inField = keyEvent({ target: 'input' });
            const inPage = keyEvent({ target: 'body' });
            process.stdout.write(JSON.stringify({
                field: blocking.control.handleKeydown(inField),
                fieldPrevented: inField.prevented,
                page: blocking.control.handleKeydown(inPage),
                calls: blocking.calls
            }));
            """
        )
        self.assertFalse(result["field"])
        self.assertFalse(result["fieldPrevented"])
        self.assertTrue(result["page"])
        self.assertEqual(result["calls"], ["minimize_all_windows"])

    def test_a_bridge_that_fails_is_reported_rather_than_thrown(self):
        result = self._run_node(
            """
            const thrower = harness({ bridgeThrows: true });
            const rejecter = harness({ answer: Promise.reject(new Error('nope')) });
            const refused = harness({ answer: { ok: false, error: 'no windows' } });
            Promise.all([
                thrower.control.minimizeAll(),
                rejecter.control.minimizeAll(),
                refused.control.minimizeAll()
            ]).then(answers => {
                process.stdout.write(JSON.stringify({
                    answers,
                    thrownErrors: thrower.errors.length,
                    rejectedErrors: rejecter.errors.length,
                    refusedErrors: refused.errors.length
                }));
            });
            """
        )
        self.assertEqual(result["answers"], [False, False, False])
        self.assertEqual(result["thrownErrors"], 1)
        self.assertEqual(result["rejectedErrors"], 1)
        # A server-side refusal is an answer, not a client-side error.
        self.assertEqual(result["refusedErrors"], 0)


APP_SETTINGS_JS = STATIC_JS / "app-settings.js"


@unittest.skipUnless(NODE, "Node.js is required for the minimize-all tests")
class MinimizeCascadeSettingTestCase(MinimizeAllNodeTestCase):
    """App Settings' native-only cascade field, executed rather than read.

    The real `collectWorkspaceSettingsForm` is sliced out of `app-settings.js`
    and run against a stubbed page, because the rule that matters is what the
    save *omits*: an unchecked box in a browser window must not write a native
    setting off behind the user's back.
    """

    def _slice(self, source: str, start: str, end: str) -> str:
        begin = source.index(start)
        return source[begin:source.index(end, begin)]

    def _collect(self, native: bool, checked: bool, has_field: bool = True):
        source = APP_SETTINGS_JS.read_text(encoding="utf-8")
        functions = self._slice(
            source,
            "    function isNativeWindowModeAvailable()",
            "    function syncAutosaveIntervalLabel(",
        )
        harness = (
            """
            const NATIVE = process.argv[2] === 'native';
            const CHECKED = process.argv[3] === 'checked';
            const HAS_FIELD = process.argv[4] === 'field';
            const DEFAULT_APP_SETTINGS = {
                workspace: { autosave_interval_minutes: 5 }
            };
            /* The one predicate minimize-all.js exports, stubbed at exactly the
               name app-settings.js looks it up under. */
            const gridVibeMinimizeAllAvailable = NATIVE ? (() => true) : undefined;
            const elements = {
                appSurfaceMode: { value: 'normal' },
                appWorkspaceAutosaveInterval: { value: '7' }
            };
            if (HAS_FIELD) {
                elements.appWorkspaceMinimizeCascade = { checked: CHECKED };
            }
            const window = { pywebview: null };
            const document = { getElementById: id => elements[id] || null };
            """
            + functions
            + """
            process.stdout.write(JSON.stringify(collectWorkspaceSettingsForm()));
            """
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    "native" if native else "browser",
                    "checked" if checked else "unchecked",
                    "field" if has_field else "none",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_native_window_sends_the_box_it_shows(self):
        self.assertTrue(self._collect(native=True, checked=True)["minimize_cascade"])
        self.assertFalse(self._collect(native=True, checked=False)["minimize_cascade"])

    def test_a_browser_window_omits_the_key_it_never_showed(self):
        # An omitted key keeps whatever the server already has
        # (_normalize_app_config_update), so saving any other setting from a
        # browser tab can never turn the cascade off.
        self.assertNotIn("minimize_cascade", self._collect(native=False, checked=True))
        self.assertNotIn("minimize_cascade", self._collect(native=False, checked=False))

    def test_the_rest_of_the_workspace_section_is_unchanged(self):
        collected = self._collect(native=False, checked=False, has_field=False)
        self.assertEqual(collected["surface_mode"], "normal")
        self.assertEqual(collected["autosave_interval_minutes"], 7)
        # multi_workspace_enabled stays absent: the launcher's Workspaces
        # switch owns it, and this dialog must never move it.
        self.assertNotIn("multi_workspace_enabled", collected)


if __name__ == "__main__":
    unittest.main()
