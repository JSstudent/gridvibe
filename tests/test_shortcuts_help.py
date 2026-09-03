"""The shortcut reference panel, driven through its own button.

`shortcuts-help.js` is one array and one builder, so it is exercised by
*running* it: the real module is loaded in Node behind a stub page, the panel is
opened the way the top-bar button opens it, and its markup is parsed back into
the rows a reader would see.

What is pinned here is the decision the panel embodies — a read-only list that
tells the truth about what the app answers to:

- nothing is emitted until the button is pressed, and closing takes the rows
  back out again;
- every row states a chord (or, for the one configurable binding, says where it
  is configured) *and* what it does — a chord with no action is not a reference;
- a multi-key chord renders one `<kbd>` per key, so "Ctrl+Shift+F" is three
  keys and not one string;
- alternatives and ranges are separated differently, because `Alt+1` – `Alt+9`
  is not a choice between two chords;
- **mode-dependent rows are marked, never hidden**: `Alt+X` is native-window
  only and it is still listed, wearing a parenthetical, because a row that
  quietly vanishes reads as a missing feature;
- the panel is not a modal, so focus moves into it on open and returns to the
  button on close — and only when the panel still had it, or a close provoked
  by a press somewhere else would yank focus off whatever was pressed;
- values reach the markup escaped, so a label can never rewrite the rows;
- `shortcutHelpChordNames()` answers with the keyboard chords and nothing else
  — it is what `README.md`'s table is filled from, and a pointer gesture and a
  user-configured chord are neither of them a chord this app hardcodes.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
README = REPO_ROOT / "README.md"
SHORTCUTS_HELP_JS = STATIC_JS / "shortcuts-help.js"

NODE = shutil.which("node")

# The whole page surface the module can reach: three elements looked up by id,
# one escaper, and a document that knows which element has focus. The panel is
# one `innerHTML` write and a class toggle, so there is nothing else to stub.
HARNESS_STUBS = r"""
/* shared.js's own escaper, copied rather than neutered: every value reaches the
   parser below through it, so a stub that did not escape would let a label
   containing markup rewrite the rows the assertions read. */
function escHtml(value) {
    return String(value === null || value === undefined ? '' : value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function fakeClassList() {
    const names = new Set();
    return {
        add: name => names.add(name),
        remove: name => names.delete(name),
        contains: name => names.has(name)
    };
}

/* Listener bookkeeping, not a dispatcher: the assertions care that a handler
   was added *and* taken off again, so the stub keeps the set rather than a
   count and `fire()` simply refuses to run one that was removed. */
function fakeListeners() {
    const handlers = new Map();
    return {
        addEventListener(type, handler) {
            if (!handlers.has(type)) { handlers.set(type, new Set()); }
            handlers.get(type).add(handler);
        },
        removeEventListener(type, handler) {
            handlers.get(type)?.delete(handler);
        },
        listenerCount(type) { return handlers.get(type)?.size || 0; },
        fire(type) { [...(handlers.get(type) || [])].forEach(handler => handler()); }
    };
}

function fakeElement(id) {
    return {
        id,
        innerHTML: '',
        attributes: {},
        style: {},
        classList: fakeClassList(),
        setAttribute(name, value) { this.attributes[name] = value; },
        focus() { document.activeElement = this; },
        /* The panel holds no focusable rows, so the only node it ever contains
           for this purpose is itself -- which the module checks separately. */
        contains: node => false,
        /* No parent, so shortcutsHelpClipBounds() has nothing to walk -- and no
           getBoundingClientRect either, which is what makes fitting stand down
           on a page that cannot be measured. */
        parentElement: null,
        ...fakeListeners()
    };
}

const byId = new Map();
['shortcutsHelpRoot', 'shortcutsHelpBtn', 'shortcutsHelp'].forEach(id => byId.set(id, fakeElement(id)));

const document = {
    activeElement: null,
    hidden: false,
    getElementById: id => byId.get(id) || null,
    ...fakeListeners()
};

/* The module guards every window access, so the stub is only what the
   dismissers reach for. */
const window = fakeListeners();

function root() { return byId.get('shortcutsHelpRoot'); }
function button() { return byId.get('shortcutsHelpBtn'); }
function panel() { return byId.get('shortcutsHelp'); }
function isOpen() { return root().classList.contains('open'); }

function press() { toggleShortcutsHelp({ preventDefault() {}, stopPropagation() {} }); }

function stripTags(html) {
    return html.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
}

/* The rendered panel, read back as the reader meets it. */
function parseGroups() {
    const groups = [];
    const section = /<section class="shortcuts-help-group">([\s\S]*?)<\/section>/g;
    let found;
    while ((found = section.exec(panel().innerHTML)) !== null) {
        const body = found[1];
        const title = /<h3 class="shortcuts-help-group-title">([\s\S]*?)<\/h3>/.exec(body);
        const rows = [];
        const row = /<div class="shortcuts-help-chord">([\s\S]*?)<\/div>\s*<div class="shortcuts-help-action">([\s\S]*?)<\/div>/g;
        let hit;
        while ((hit = row.exec(body)) !== null) {
            const chordHtml = hit[1];
            const actionHtml = hit[2];
            const noteMatch = /<span class="shortcuts-help-note">\(([\s\S]*?)\)<\/span>/.exec(actionHtml);
            const labelMatch = /<span class="shortcuts-help-chord-label">([\s\S]*?)<\/span>/.exec(chordHtml);
            rows.push({
                chordText: stripTags(chordHtml),
                keys: [...chordHtml.matchAll(/<kbd class="shortcuts-help-key">([\s\S]*?)<\/kbd>/g)].map(m => m[1]),
                joins: [...chordHtml.matchAll(/<span class="shortcuts-help-chord-join">([\s\S]*?)<\/span>/g)].map(m => m[1]),
                plusCount: (chordHtml.match(/class="shortcuts-help-plus"/g) || []).length,
                chordLabel: labelMatch ? labelMatch[1] : '',
                note: noteMatch ? noteMatch[1] : '',
                action: stripTags(actionHtml.replace(/<span class="shortcuts-help-note">[\s\S]*?<\/span>/, ''))
            });
        }
        groups.push({ title: title ? title[1].trim() : '', rows });
    }
    return groups;
}

function allRows() { return parseGroups().flatMap(group => group.rows); }

function rowFor(chordText) {
    const row = allRows().find(candidate => candidate.chordText === chordText);
    if (!row) { throw new Error(`no row for ${chordText}`); }
    return row;
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for shortcut panel tests")
class ShortcutsHelpTestCase(unittest.TestCase):
    """Shared runner: the real module, a stub page, and one panel."""

    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + SHORTCUTS_HELP_JS.read_text(encoding="utf-8")
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


class ShortcutsHelpDisclosureTestCase(ShortcutsHelpTestCase):
    """Pressing the button is what builds the list, and closing takes it away."""

    def test_nothing_is_emitted_until_the_button_is_pressed(self):
        result = self._run_node(
            """
            const before = { open: isOpen(), html: panel().innerHTML };
            press();
            report({
                before,
                open: isOpen(),
                expanded: button().attributes['aria-expanded'],
                groups: parseGroups().length
            });
            """
        )
        self.assertFalse(result["before"]["open"])
        self.assertEqual(result["before"]["html"], "")
        self.assertTrue(result["open"])
        self.assertEqual(result["expanded"], "true")
        self.assertGreater(result["groups"], 0)

    def test_pressing_again_closes_it_and_drops_the_rows(self):
        result = self._run_node(
            """
            press();
            const opened = parseGroups().length;
            press();
            report({
                opened,
                open: isOpen(),
                expanded: button().attributes['aria-expanded'],
                html: panel().innerHTML
            });
            """
        )
        self.assertGreater(result["opened"], 0)
        self.assertFalse(result["open"])
        self.assertEqual(result["expanded"], "false")
        self.assertEqual(result["html"], "")

    def test_closing_an_already_closed_panel_changes_nothing(self):
        result = self._run_node(
            """
            document.activeElement = { name: 'somewhere else' };
            closeShortcutsHelp();
            report({
                open: isOpen(),
                expanded: button().attributes['aria-expanded'],
                focusMoved: document.activeElement !== null && document.activeElement.name !== 'somewhere else'
            });
            """
        )
        self.assertFalse(result["open"])
        # Never touched, so the button was never told it had been collapsed.
        self.assertIsNone(result.get("expanded"))
        self.assertFalse(result["focusMoved"])


class ShortcutsHelpFocusTestCase(ShortcutsHelpTestCase):
    """Not a modal: focus is moved, never trapped, and handed back honestly."""

    def test_opening_moves_focus_into_the_panel(self):
        result = self._run_node(
            """
            press();
            report({ focused: document.activeElement === panel() });
            """
        )
        self.assertTrue(result["focused"])

    def test_closing_hands_focus_back_to_the_button(self):
        result = self._run_node(
            """
            press();
            press();
            report({ focused: document.activeElement === button() });
            """
        )
        self.assertTrue(result["focused"])

    def test_a_close_from_elsewhere_does_not_steal_focus(self):
        """The outside-press listener closes it; focus belongs to what was pressed."""
        result = self._run_node(
            """
            press();
            const elsewhere = { name: 'some other control' };
            document.activeElement = elsewhere;
            closeShortcutsHelp();
            report({
                open: isOpen(),
                focusHeld: document.activeElement === elsewhere
            });
            """
        )
        self.assertFalse(result["open"])
        self.assertTrue(result["focusHeld"])


class ShortcutsHelpContentTestCase(ShortcutsHelpTestCase):
    """What the list says, read back off the rendered panel."""

    def _groups(self):
        return self._run_node("press();\nreport(parseGroups());")

    def test_the_groups_are_the_ones_a_reader_looks_in(self):
        groups = self._groups()
        self.assertEqual(
            [group["title"] for group in groups],
            ["Navigation", "Terminal", "Explorer", "Editor", "Window", "Mouse"],
        )
        for group in groups:
            self.assertTrue(group["rows"], f"{group['title']} lists nothing")

    def test_every_row_states_a_chord_and_an_action(self):
        for group in self._groups():
            for row in group["rows"]:
                self.assertTrue(
                    row["keys"] or row["chordLabel"],
                    f"{group['title']} row has no chord: {row}",
                )
                self.assertTrue(row["action"], f"{group['title']} row has no action: {row}")

    def test_a_multi_key_chord_renders_one_key_per_kbd(self):
        result = self._run_node(
            """
            press();
            report({
                repoSearch: rowFor('Ctrl+Shift+F'),
                paste: rowFor('Ctrl+V')
            });
            """
        )
        self.assertEqual(result["repoSearch"]["keys"], ["Ctrl", "Shift", "F"])
        self.assertEqual(result["repoSearch"]["plusCount"], 2)
        self.assertEqual(result["paste"]["keys"], ["Ctrl", "V"])
        self.assertEqual(result["paste"]["plusCount"], 1)

    def test_a_range_is_not_rendered_as_a_choice(self):
        """`Alt+1` – `Alt+9` is nine chords, not a pick between two."""
        result = self._run_node(
            """
            press();
            const rows = allRows();
            const range = rows.find(row => row.keys.join(',') === 'Alt,1,Alt,9');
            const alternative = rows.find(row => row.keys.join(',') === 'Alt,W,Alt,Shift,W');
            report({ range, alternative });
            """
        )
        self.assertEqual(result["range"]["joins"], [" – "])
        self.assertEqual(result["alternative"]["joins"], [" / "])

    def test_mode_dependent_rows_are_marked_and_never_hidden(self):
        result = self._run_node(
            """
            press();
            report({
                minimizeAll: rowFor('Alt+X'),
                workspaceCycle: allRows().find(row => row.keys.join(',') === 'Alt,W,Alt,Shift,W')
            });
            """
        )
        self.assertEqual(result["minimizeAll"]["note"], "native window only")
        self.assertEqual(result["workspaceCycle"]["note"], "multiple workspaces only")

    def test_the_configurable_binding_is_listed_without_claiming_a_chord(self):
        """The push-to-talk key is the user's own; the panel points at where it lives."""
        result = self._run_node(
            """
            press();
            const row = allRows().find(candidate => candidate.chordLabel);
            report(row);
            """
        )
        self.assertEqual(result["keys"], [])
        self.assertIn("push-to-talk", result["chordLabel"])
        self.assertEqual(result["note"], "set in App Settings")

    def test_the_pointer_gestures_are_listed_apart_from_the_chords(self):
        groups = self._groups()
        mouse = next(group for group in groups if group["title"] == "Mouse")
        gestures = {row["chordText"] for row in mouse["rows"]}
        self.assertIn("Alt+click", gestures)

    def test_values_reach_the_markup_escaped(self):
        result = self._run_node(
            """
            report(shortcutsHelpRowHtml({
                chords: [['<b>Alt</b>']],
                action: '<img src=x>',
                note: '"quoted"'
            }));
            """
        )
        self.assertNotIn("<b>", result)
        self.assertNotIn("<img", result)
        self.assertIn("&lt;b&gt;Alt&lt;/b&gt;", result)
        self.assertIn("&quot;quoted&quot;", result)


class ShortcutsHelpChordNamesTestCase(ShortcutsHelpTestCase):
    """The one list README.md is filled from."""

    def _names(self):
        return self._run_node("report(shortcutHelpChordNames());")

    def test_it_answers_with_the_hardcoded_keyboard_chords(self):
        names = self._names()
        for chord in ("Alt+Q", "Alt+X", "Ctrl+Shift+E", "Ctrl+Shift+C", "Ctrl+V", "F5", "Tab"):
            self.assertIn(chord, names)

    def test_pointer_gestures_and_configured_chords_are_not_chords(self):
        names = self._names()
        self.assertNotIn("Alt+click", names)
        self.assertNotIn("Ctrl+click", names)
        self.assertNotIn("Shift+click", names)
        self.assertNotIn("", names)


class ShortcutsHelpFitTestCase(ShortcutsHelpTestCase):
    """The panel is sized against the box it is drawn in, not against the window.

    On the launcher that box is `.column`, a scroll container, so a panel
    anchored above a button in the bottom action bar grew through the top of
    the setup card and had its first group clipped away. The policy is pure
    arithmetic and is exercised as such."""

    def _fit(self, **measurements):
        return self._run_node(
            "report(shortcutsHelpFittedHeight(%s));" % json.dumps(measurements)
        )

    def test_a_panel_opening_upward_is_cut_to_the_room_above_its_button(self):
        # The launcher's case: 540px of cap, 300px of room above the button.
        fitted = self._fit(
            opensUp=True,
            buttonTop=300,
            buttonBottom=340,
            boundsTop=0,
            boundsBottom=900,
            styleCap=540,
        )
        self.assertLess(fitted, 540)
        self.assertLessEqual(fitted, 300)

    def test_the_clip_is_the_scroll_box_and_not_the_window_top(self):
        """Same button, same window - only the box it is drawn in moves."""
        window_only = self._fit(
            opensUp=True,
            buttonTop=600,
            buttonBottom=640,
            boundsTop=0,
            boundsBottom=900,
            styleCap=540,
        )
        inside_a_scroller = self._fit(
            opensUp=True,
            buttonTop=600,
            buttonBottom=640,
            boundsTop=380,
            boundsBottom=900,
            styleCap=540,
        )
        self.assertEqual(window_only, 540)
        self.assertLess(inside_a_scroller, window_only)

    def test_room_to_spare_never_grows_the_panel_past_its_own_cap(self):
        """The cap is a reading decision; this measurement is a fitting one."""
        fitted = self._fit(
            opensUp=False,
            buttonTop=40,
            buttonBottom=80,
            boundsTop=0,
            boundsBottom=4000,
            styleCap=540,
        )
        self.assertEqual(fitted, 540)

    def test_a_panel_opening_downward_measures_the_room_below(self):
        fitted = self._fit(
            opensUp=False,
            buttonTop=40,
            buttonBottom=80,
            boundsTop=0,
            boundsBottom=300,
            styleCap=540,
        )
        self.assertLessEqual(fitted, 220)

    def test_a_box_too_short_for_any_answer_gets_the_floor_not_a_sliver(self):
        fitted = self._fit(
            opensUp=True,
            buttonTop=30,
            buttonBottom=70,
            boundsTop=0,
            boundsBottom=200,
            styleCap=540,
        )
        self.assertGreaterEqual(fitted, 140)

    def test_a_page_that_cannot_be_measured_is_left_alone(self):
        """The stub page has no rects, so fitting stands down rather than
        writing a height it guessed."""
        result = self._run_node(
            """
            press();
            report({ maxHeight: panel().style.maxHeight ?? null, open: isOpen() });
            """
        )
        self.assertTrue(result["open"])
        self.assertIsNone(result["maxHeight"])


class ShortcutsHelpDismissalTestCase(ShortcutsHelpTestCase):
    """Two ways of stopping looking at it close it, and both are taken off again.

    A reference is glanced at rather than operated, so the pointer leaving it
    and the window losing focus each dismiss it - and a listener that outlived
    its surface would be the thing that closed the *next* panel unbidden."""

    def test_leaving_with_the_pointer_closes_it(self):
        result = self._run_node(
            """
            press();
            const openBeforeLeaving = isOpen();
            root().fire('mouseleave');
            const openDuringGrace = isOpen();
            await new Promise(resolve => setTimeout(resolve, 400));
            report({ openBeforeLeaving, openDuringGrace, open: isOpen(), html: panel().innerHTML });
            """
        )
        self.assertTrue(result["openBeforeLeaving"])
        # Not on the frame the pointer clips a corner: the grace is the point.
        self.assertTrue(result["openDuringGrace"])
        self.assertFalse(result["open"])
        self.assertEqual(result["html"], "")

    def test_coming_back_within_the_grace_keeps_it_open(self):
        result = self._run_node(
            """
            press();
            root().fire('mouseleave');
            root().fire('mouseenter');
            await new Promise(resolve => setTimeout(resolve, 400));
            report({ open: isOpen() });
            """
        )
        self.assertTrue(result["open"])

    def test_the_window_losing_focus_closes_it(self):
        result = self._run_node(
            """
            press();
            window.fire('blur');
            report({ open: isOpen(), html: panel().innerHTML });
            """
        )
        self.assertFalse(result["open"])
        self.assertEqual(result["html"], "")

    def test_the_tab_going_to_the_background_closes_it(self):
        result = self._run_node(
            """
            press();
            document.hidden = true;
            document.fire('visibilitychange');
            const hiddenClosed = !isOpen();
            document.hidden = false;
            press();
            document.fire('visibilitychange');
            report({ hiddenClosed, stillOpenWhenVisible: isOpen() });
            """
        )
        self.assertTrue(result["hiddenClosed"])
        # Coming back to the foreground is not a reason to close.
        self.assertTrue(result["stillOpenWhenVisible"])

    def test_closing_takes_every_listener_off_again(self):
        result = self._run_node(
            """
            const counts = () => ({
                leave: root().listenerCount('mouseleave'),
                blur: window.listenerCount('blur'),
                visibility: document.listenerCount('visibilitychange')
            });
            const before = counts();
            press();
            const armed = counts();
            press();
            report({ before, armed, released: counts() });
            """
        )
        self.assertEqual(result["before"], {"leave": 0, "blur": 0, "visibility": 0})
        self.assertEqual(result["armed"], {"leave": 1, "blur": 1, "visibility": 1})
        self.assertEqual(result["released"], {"leave": 0, "blur": 0, "visibility": 0})

    def test_reopening_does_not_stack_a_second_set_of_listeners(self):
        result = self._run_node(
            """
            press(); press(); press(); press(); press();
            report({
                open: isOpen(),
                leave: root().listenerCount('mouseleave'),
                blur: window.listenerCount('blur')
            });
            """
        )
        self.assertTrue(result["open"])
        self.assertEqual(result["leave"], 1)
        self.assertEqual(result["blur"], 1)

    def test_a_grace_left_running_cannot_close_the_next_panel(self):
        """The pointer leaves, the panel is closed another way, and it is
        opened again inside the grace window - the stale timer must be gone."""
        result = self._run_node(
            """
            press();
            root().fire('mouseleave');
            press();
            press();
            await new Promise(resolve => setTimeout(resolve, 400));
            report({ open: isOpen() });
            """
        )
        self.assertTrue(result["open"])


class ShortcutsHelpReadmeTestCase(ShortcutsHelpTestCase):
    """README.md's table is the one place a chord is written down twice.

    Its own list was already wrong in three rows before this panel existed — it
    omitted `Ctrl+Shift+C`, `Ctrl+V` and the editor's `Tab` — which is exactly
    the drift a keybind registry would have been built to prevent. This costs
    one assertion instead: the table is filled from the array, and the two chord
    sets have to agree in **both** directions, so neither a chord added to the
    panel and left out of the docs nor a chord documented and never bound can
    survive a test run.
    """

    def _readme_chords(self):
        text = README.read_text(encoding="utf-8")
        start = text.index("| Shortcut | Action |")
        table = text[start:].split("\n\n", 1)[0]
        chords = set()
        for line in table.splitlines()[2:]:
            first_column = line.split("|")[1]
            chords.update(re.findall(r"`([^`]+)`", first_column))
        return chords

    def test_the_readme_table_and_the_panel_list_the_same_chords(self):
        panel_chords = set(self._run_node("report(shortcutHelpChordNames());"))
        self.assertTrue(panel_chords)
        self.assertEqual(self._readme_chords(), panel_chords)


if __name__ == "__main__":
    unittest.main()
