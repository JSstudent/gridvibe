"""The mark a dashboard row wears, executed in Node.

`agent-glyphs.js` is a mapping and a fallback, so it is tested by running it:
the module is required and asked the questions the dashboard asks it.

What is pinned is what a mark is *for*:

- **It says which agent.** Every agent the registry ships gets a mark of its
  own, and no two of them are the same drawing -- a list of identical glyphs
  says only "these are rows", which is what the reader could already see.
- **An unknown agent still gets one.** A custom agent, an `other` selection and
  a registry entry GridVibe has not drawn yet all land on the shared fallback,
  under a key the stylesheet can match, rather than on an empty chip.
- **Known marks use the supplied local artwork.** Custom agents retain a
  currentColor stroke SVG fallback.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENT_GLYPHS_JS = REPO_ROOT / "web" / "static" / "js" / "agent-glyphs.js"
AGENT_REGISTRY = REPO_ROOT / "agent_registry.json"

NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required for the agent glyph tests")
class AgentGlyphTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            f"const glyphs = require({json.dumps(str(AGENT_GLYPHS_JS))});\n"
            + body
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

    def test_every_agent_in_the_registry_has_a_mark_of_its_own(self):
        """The registry is the list the launcher and the pane menu offer, so an
        agent added there without a mark would ship a row wearing the fallback
        while every other row wore its own."""
        registry = json.loads(AGENT_REGISTRY.read_text(encoding="utf-8"))
        drawn = self._run_node(
            "process.stdout.write(JSON.stringify(glyphs.AGENT_GLYPH_KEYS));"
        )
        self.assertEqual(sorted(drawn), sorted(registry))

    def test_no_two_agents_are_the_same_drawing(self):
        marks = self._run_node(
            "process.stdout.write(JSON.stringify("
            "glyphs.AGENT_GLYPH_KEYS.map(key => glyphs.agentGlyphMarkup(key))"
            "));"
        )
        self.assertEqual(len(set(marks)), len(marks))

    def test_an_agent_with_no_mark_still_gets_one(self):
        result = self._run_node(
            """
            const unknown = ['', 'other', 'house-agent', null, undefined, '  '];
            process.stdout.write(JSON.stringify({
                keys: unknown.map(value => glyphs.agentGlyphKey(value)),
                sameMark: unknown.every(value =>
                    glyphs.agentGlyphMarkup(value) === glyphs.agentGlyphMarkup('nope')),
                drawn: glyphs.agentGlyphMarkup('house-agent').includes('<svg')
            }));
            """
        )
        self.assertEqual(result["keys"], ["default"] * 6)
        self.assertTrue(result["sameMark"])
        self.assertTrue(result["drawn"])
        # The fallback key is not one of the drawn agents, so the stylesheet's
        # per-agent tints and its default can never collide.
        drawn = self._run_node(
            "process.stdout.write(JSON.stringify(glyphs.AGENT_GLYPH_KEYS));"
        )
        self.assertNotIn("default", drawn)

    def test_a_key_is_matched_however_it_was_written(self):
        result = self._run_node(
            "process.stdout.write(JSON.stringify("
            "['CODEX', ' Codex ', 'codex'].map(value => glyphs.agentGlyphKey(value))"
            "));"
        )
        self.assertEqual(result, ["codex", "codex", "codex"])

    def test_known_marks_load_bundled_artwork_and_fallback_takes_the_theme(self):
        marks = self._run_node(
            "process.stdout.write(JSON.stringify("
            "glyphs.AGENT_GLYPH_KEYS.map(key => glyphs.agentGlyphMarkup(key))"
            "));"
        )
        for mark in marks:
            self.assertTrue(mark.startswith('<img class="dash-agent-glyph"'))
            self.assertIn('alt=""', mark)
            self.assertIn('aria-hidden="true"', mark)
            src = mark.split('src="', 1)[1].split('"', 1)[0]
            self.assertTrue(src.startswith("/docs/images/agent/"))
            self.assertTrue((REPO_ROOT / src.lstrip("/")).is_file())
        fallback = self._run_node(
            "process.stdout.write(JSON.stringify(glyphs.agentGlyphMarkup('other')));"
        )
        self.assertIn('stroke="currentColor"', fallback)
        self.assertIn('fill="none"', fallback)
        self.assertIn('aria-hidden="true"', fallback)


if __name__ == "__main__":
    unittest.main()
