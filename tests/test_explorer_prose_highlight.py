"""An apostrophe in prose is an apostrophe, and everything else still colours.

``highlightExplorerCode`` is the fallback lexer the Source view uses for every
language the pinned Highlight.js build does not carry -- ``text`` and
``markdown`` among them.  Its generic string rule opened a literal on ``'``,
so a line of ordinary prose containing a contraction ("version's interval")
was coloured as a string from the apostrophe to the end of the line, and in
the one call site that hands the lexer more than a single line -- the Markdown
preview's fenced code blocks -- to the end of the block.

The cure is scoped to the one ambiguous character.  ``'`` is the only quote
that is also a letter of ordinary prose; double quotes, backticks and numbers
are not, and a text file keeps their colours.

Executed, not read: the lexer runs in a Node ``vm`` against a stubbed page and
the assertions are about the markup it returns.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

VIEWER_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js" / "explorer-viewer.js"
NODE = shutil.which("node")

QUOTE = chr(34)

LEXER_HARNESS = """
const fs = require('fs');
const vm = require('vm');

const sandbox = {
    console: { log: console.log, error() {} },
    document: {
        getElementById: () => null,
        querySelector: () => null,
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
    applyExplorerChangeMarks: () => {},
    escHtml: value => String(value == null ? '' : value)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

const NL = String.fromCharCode(10);
const APOS = String.fromCharCode(39);
const QUOTE = String.fromCharCode(34);
const TICK = String.fromCharCode(96);
const lex = (text, language) => sandbox.highlightExplorerCode(text, language, [], 0);

/* The Markdown preview's own call site, driven through a stub of just the
   nodes it touches: what lands in each block's innerHTML is the observation. */
const previewBlocks = specs => {
    const blocks = specs.map(([className, text]) => ({
        className,
        textContent: text,
        innerHTML: null,
        parentElement: { classList: { add() {} }, dataset: {} }
    }));
    sandbox.highlightExplorerPreviewCode({ querySelectorAll: () => blocks });
    return blocks.map(block => block.innerHTML);
};

process.stdout.write(JSON.stringify({
    // The reported line, in the file kind it was reported from.
    textProse: lex('a version' + APOS + 's interval and 42 more', 'text'),
    markdownProse: lex('a version' + APOS + 's interval and 42 more', 'markdown'),
    // The unambiguous quotes are untouched: prose is still marked up.
    textDoubleQuoted: lex('the ' + QUOTE + 'quoted' + QUOTE + ' word', 'text'),
    markdownBacktick: lex('use ' + TICK + 'inline' + TICK + ' here', 'markdown'),
    // Nothing else lost its colours.
    pythonKeyword: lex('def spam(', 'python'),
    pythonString: lex('x = ' + APOS + 'hello' + APOS, 'python'),
    pythonComment: lex('# the version' + APOS + 's interval', 'python'),
    // The multi-line call site: an unclosed quote stops at its own line.
    iniBlock: lex('a = don' + APOS + 't' + NL + 'b = 1' + NL + 'c = 2' + NL, 'ini'),
    // A deliberate multi-line construct still spans lines.
    pythonTriple: lex(
        'x = ' + APOS.repeat(3) + 'one' + NL + 'two' + APOS.repeat(3) + NL + 'y = 1' + NL,
        'python'
    ),
    // The Markdown preview's fenced blocks, through the preview's own path.
    preview: previewBlocks([
        ['language-text', 'a version' + APOS + 's interval'],
        ['language-markdown', 'a version' + APOS + 's interval'],
        ['language-python', 'x = ' + APOS + 'hello' + APOS]
    ])
}));
"""


def _span(class_name: str, inner: str) -> str:
    return "<span class=" + QUOTE + class_name + QUOTE + ">" + inner + "</span>"


@unittest.skipUnless(NODE, "Node.js is required for the fallback lexer tests")
class ExplorerProseHighlightTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(LEXER_HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(VIEWER_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            raise AssertionError("node harness failed:" + chr(10) + completed.stderr)
        cls.lexed = json.loads(completed.stdout)

    def test_an_apostrophe_in_prose_opens_nothing(self):
        """The contraction is plain text, and the rest of the line is untouched.

        ``'`` used to open a string literal that ran to the end of the line, so
        every line of prose carrying a contraction was coloured from the
        apostrophe onwards.
        """
        for key in ("textProse", "markdownProse"):
            with self.subTest(case=key):
                self.assertNotIn("explorer-code-string", self.lexed[key])
                self.assertIn("version's interval", self.lexed[key])

    def test_prose_keeps_the_colours_that_were_never_ambiguous(self):
        """Only ``'`` is both a quote and a letter of ordinary prose.

        Numbers, double-quoted phrases and Markdown's backticked inline code
        all still mean what they look like in a text file, and colouring them
        is why this lexer runs over prose at all.
        """
        for key in ("textProse", "markdownProse"):
            with self.subTest(case=key):
                self.assertIn(_span("explorer-code-number", "42"), self.lexed[key])
        self.assertIn(
            _span("explorer-code-string", "&quot;quoted&quot;"),
            self.lexed["textDoubleQuoted"],
        )
        self.assertIn(
            _span("explorer-code-string", "`inline`"),
            self.lexed["markdownBacktick"],
        )

    def test_a_fenced_prose_block_is_still_left_alone(self):
        """One set names the prose languages; each reader draws its own rule.

        A code fence declared ``text`` or ``markdown`` is where the author has
        said the content is *not* prose, so the preview leaves its markup
        exactly as the Markdown renderer wrote it.  A real language is lexed.
        """
        text_block, markdown_block, python_block = self.lexed["preview"]
        self.assertIsNone(text_block)
        self.assertIsNone(markdown_block)
        self.assertIn(_span("explorer-code-string", "'hello'"), python_block)

    def test_a_real_language_keeps_every_colour_it_had(self):
        """``'`` is still a string quote everywhere it is one."""
        self.assertIn(
            _span("explorer-code-keyword", "def"), self.lexed["pythonKeyword"]
        )
        self.assertIn(
            _span("explorer-code-string", "'hello'"), self.lexed["pythonString"]
        )
        self.assertIn(
            _span("explorer-code-comment", "# the version's interval"),
            self.lexed["pythonComment"],
        )

    def test_an_unclosed_quote_ends_at_its_own_line(self):
        """The lexer is handed a whole fenced block by the Markdown preview.

        An apostrophe in the first line used to swallow every line after it,
        which is the "entire block coloured" the report describes.  The Source
        view feeds it one line at a time, so there the cap only makes the two
        surfaces agree.
        """
        block = self.lexed["iniBlock"]
        self.assertIn(_span("explorer-code-string", "'t"), block)
        # The lines below the apostrophe are lexed on their own terms again.
        self.assertIn(_span("explorer-code-number", "1"), block)
        self.assertIn(_span("explorer-code-number", "2"), block)

    def test_a_triple_quoted_string_still_spans_lines(self):
        """The cap is on the accident, not on the deliberate construct."""
        triple = self.lexed["pythonTriple"]
        self.assertIn("one" + chr(10) + "two", triple)
        self.assertIn(_span("explorer-code-number", "1"), triple)


if __name__ == "__main__":
    unittest.main()
