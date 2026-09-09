"""The one hue a session wears, executed rather than read.

`session-colour.js` is the palette and the hash the workspace window's tab
strip has always used, lifted out of `terminals.js` so the agent dashboard can
paint the same session the same colour from another page. It is DOM-free, so it
is tested by running it.

What is pinned is what a shared identity colour has to be:

- **Stable.** A session keeps its hue across a reorder, a move and a restart,
  because the key is the group id and nothing about where the tab sits.
- **Unchanged by the move.** The hash is the one `terminals.js` shipped,
  signed truncation included — a different one would recolour every session
  that already exists, which is the single thing this must not do.
- **Total.** Every id lands on a palette entry; an empty or missing id is a
  colour rather than `undefined`, so a card can always be drawn.
- **One answer, two callers.** `terminals.js` reads it through the same two
  names it always used, so the tab strip and the dashboard cannot drift apart.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
SESSION_COLOUR_JS = STATIC_JS / "session-colour.js"
TERMINALS_JS = STATIC_JS / "terminals.js"

NODE = shutil.which("node")

# The hash as `terminals.js` shipped it, reimplemented here from its own source
# rather than imported: the point of the case is that the module agrees with the
# behaviour that existed before it, so a shared implementation would prove
# nothing.
LEGACY_HARNESS = r"""
const PALETTE = [
    '#ff6b6b', '#ff922b', '#ffd43b', '#69db7c',
    '#38d9a9', '#4dabf7', '#748ffc', '#da77f2',
    '#f783ac', '#a9e34b',
];

function legacyTabColour(groupId) {
    let hash = 0;
    for (let i = 0; i < groupId.length; i++) {
        hash = (hash * 31 + groupId.charCodeAt(i)) & 0xffffffff;
    }
    return PALETTE[Math.abs(hash) % PALETTE.length];
}

function report(value) { process.stdout.write(JSON.stringify(value)); }
"""


@unittest.skipUnless(NODE, "Node.js is required for the session colour tests")
class SessionColourTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            LEGACY_HARNESS
            + SESSION_COLOUR_JS.read_text(encoding="utf-8")
            + "\nconst colour = module.exports;\n"
            + body
            + "\n"
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

    def test_the_hash_is_the_one_the_tab_strip_already_shipped(self):
        """Group ids in this app are `group-<n>` and `saved-session-<id>`, so
        the sample is those shapes and not random strings."""
        result = self._run_node(
            """
            const ids = [];
            for (let i = 0; i < 40; i++) { ids.push(`group-${i}`); }
            for (let i = 0; i < 40; i++) { ids.push(`saved-session-abc${i}`); }
            ids.push('', 'default', 'g1', 'Workspace Two');
            report(ids.filter(id => colour.sessionColour(id) !== legacyTabColour(id)));
            """
        )
        self.assertEqual(result, [])

    def test_every_id_lands_on_a_palette_entry_including_no_id_at_all(self):
        result = self._run_node(
            """
            const answers = ['', 'g1', 'saved-session-7', null, undefined]
                .map(id => colour.sessionColour(id));
            report({
                answers,
                inPalette: answers.every(hex => colour.SESSION_COLOUR_PALETTE.includes(hex)),
                blankIsAColour: colour.sessionColour('') === colour.sessionColour(null)
            });
            """
        )
        self.assertTrue(result["inPalette"])
        self.assertTrue(result["blankIsAColour"])
        self.assertEqual(len(result["answers"]), 5)

    def test_the_same_id_is_the_same_colour_wherever_it_is_asked(self):
        """The whole reason the module exists: the tab and the dashboard card
        ask separately and must get one answer."""
        result = self._run_node(
            """
            report({
                repeated: new Set(
                    Array.from({ length: 5 }, () => colour.sessionColour('group-3'))
                ).size,
                distinct: new Set(
                    Array.from({ length: 40 }, (_value, i) => colour.sessionColour(`group-${i}`))
                ).size
            });
            """
        )
        self.assertEqual(result["repeated"], 1)
        # Not a proof of even spread, only that the hash is not collapsing every
        # id onto one hue -- which is the failure a bad `& 0xffffffff` produces.
        self.assertGreaterEqual(result["distinct"], 5)

    def test_the_faint_companion_is_the_same_hue(self):
        result = self._run_node(
            """
            report({
                solid: colour.sessionColour('g1'),
                soft: colour.sessionColourRgba('g1', 0.14),
                direct: colour.hexToRgba('#ff6b6b', 0.4)
            });
            """
        )
        self.assertEqual(result["solid"], "#ffd43b")
        self.assertEqual(result["soft"], "rgba(255,212,59,0.14)")
        self.assertEqual(result["direct"], "rgba(255,107,107,0.4)")

    def test_the_palette_is_ten_hexes_and_the_order_is_the_index(self):
        result = self._run_node(
            """
            report({
                palette: colour.SESSION_COLOUR_PALETTE,
                hexes: colour.SESSION_COLOUR_PALETTE.every(hex => /^#[0-9a-f]{6}$/.test(hex))
            });
            """
        )
        self.assertEqual(len(result["palette"]), 10)
        self.assertTrue(result["hexes"])
        # Order is what the hash indexes into, so it is pinned: a hue may be
        # replaced in place, never moved.
        self.assertEqual(result["palette"][0], "#ff6b6b")
        self.assertEqual(result["palette"][-1], "#a9e34b")

    def test_terminals_reads_the_module_rather_than_keeping_a_second_copy(self):
        """A predicate copied into two files is a disagreement waiting to
        happen, which is the whole reason for the extraction."""
        source = TERMINALS_JS.read_text(encoding="utf-8")
        self.assertIn("GridVibeSessionColour.sessionColour", source)
        self.assertIn("GridVibeSessionColour.hexToRgba", source)
        self.assertNotIn("TAB_COLOUR_PALETTE", source)
        self.assertNotIn("#ff6b6b", source)


if __name__ == "__main__":
    unittest.main()
