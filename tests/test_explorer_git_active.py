"""Behavioral coverage for the Git sidebar's active-row and reveal model.

`explorer-git-active.js` is DOM-free and require()-able, so which row the
sidebar marks as the one on screen — and whether a commit row has to be opened
to show it — are executed in Node rather than asserted as source text.

The two rules under test are the ones that make the highlight mean something:
a row is matched on the diff identity it would open (a commit id, or a diff
mode) and never on its path alone, and the reveal fires once per pane per
commit so a row the user collapses afterwards stays collapsed.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
GIT_ACTIVE_JS = STATIC_JS / "explorer-git-active.js"
EXPLORER_VIEWER_JS = STATIC_JS / "explorer-viewer.js"
EXPLORER_GIT_SIDEBAR_JS = STATIC_JS / "explorer-git-sidebar.js"
TERMINALS_HTML = REPO_ROOT / "templates" / "terminals.html"

NODE = shutil.which("node")

OLD_COMMIT = "6e01550"
OTHER_COMMIT = "9c28e89"
PATH = "web/api.py"

# JS literals, so the harness snippets below stay plain concatenation and never
# have to escape a brace.
Q_OLD = json.dumps(OLD_COMMIT)
Q_OTHER = json.dumps(OTHER_COMMIT)
Q_PATH = json.dumps(PATH)

HARNESS_PREAMBLE = """
const target = view => git_active.viewerTarget(view);
const fileView = extra => Object.assign({ mode: 'file', filePath: PATH }, extra || {});
/* One sidebar's worth of rows for the same path: the two change rows, and the
   history row inside each of two commits. */
const rows = [
    { id: 'staged', path: PATH, commitHash: '', diffMode: 'staged' },
    { id: 'unstaged', path: PATH, commitHash: '', diffMode: 'worktree' },
    { id: 'old-commit', path: PATH, commitHash: OLD, diffMode: '' },
    { id: 'other-commit', path: PATH, commitHash: OTHER, diffMode: '' }
];
const marked = view => rows
    .filter(row => git_active.rowIsActive(target(view), row))
    .map(row => row.id);
const plan = (view, state) => git_active.revealPlan(target(view), state);
const emit = value => console.log(JSON.stringify(value));
""".replace("PATH", Q_PATH).replace("OTHER", Q_OTHER).replace("OLD", Q_OLD)


@unittest.skipUnless(NODE, "Node.js is required for Git active-row tests")
class ExplorerGitActiveHarness(unittest.TestCase):
    def _run_node(self, body: str):
        harness = (
            "const git_active = require(" + json.dumps(str(GIT_ACTIVE_JS)) + ");\n"
            + HARNESS_PREAMBLE
            + "\n"
            + body
            + "\n"
        )
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "harness.js"
            script.write_text(harness, encoding="utf-8")
            result = subprocess.run(
                [NODE, str(script)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])


class ExplorerGitActiveRowTestCase(ExplorerGitActiveHarness):
    def test_commit_diff_marks_only_that_commits_row(self):
        """The reported bug: a commit diff left every row for the path unmarked."""
        marked = self._run_node(
            "emit(marked(fileView({ diffCommit: " + Q_OLD + " })));"
        )

        self.assertEqual(marked, ["old-commit"])

    def test_worktree_diff_never_marks_a_history_row(self):
        marked = self._run_node("emit(marked(fileView({ diffMode: 'worktree' })));")

        self.assertEqual(marked, ["unstaged"])

    def test_staged_diff_marks_the_staged_row_alone(self):
        marked = self._run_node("emit(marked(fileView({ diffMode: 'staged' })));")

        self.assertEqual(marked, ["staged"])

    def test_open_file_without_a_diff_falls_back_to_the_unstaged_row(self):
        marked = self._run_node("emit(marked(fileView()));")

        self.assertEqual(marked, ["unstaged"])

    def test_a_view_that_is_not_a_file_marks_nothing(self):
        marked = self._run_node(
            "emit(["
            "  marked({ mode: 'directory', filePath: " + Q_PATH + " }),"
            "  marked({ mode: 'file', filePath: '' }),"
            "  marked(null)"
            "]);"
        )

        self.assertEqual(marked, [[], [], []])

    def test_another_files_rows_are_never_marked(self):
        marked = self._run_node(
            "emit(marked({ mode: 'file', filePath: 'web/explorer.py',"
            " diffCommit: " + Q_OLD + " }));"
        )

        self.assertEqual(marked, [])

    def test_commit_row_is_marked_for_the_commit_on_screen(self):
        flags = self._run_node(
            "const t = target(fileView({ diffCommit: " + Q_OLD + " }));"
            "emit(["
            "  git_active.commitIsActive(t, " + Q_OLD + "),"
            "  git_active.commitIsActive(t, " + Q_OTHER + "),"
            "  git_active.commitIsActive(t, ''),"
            "  git_active.commitIsActive(null, " + Q_OLD + ")"
            "]);"
        )

        self.assertEqual(flags, [True, False, False, False])


class ExplorerGitRevealTestCase(ExplorerGitActiveHarness):
    def test_restored_commit_diff_opens_its_collapsed_commit_row(self):
        """The reported bug: after a restore the diff was inside a closed row."""
        result = self._run_node(
            "emit(plan(fileView({ diffCommit: " + Q_OLD + " }),"
            " { expanded: [], revealed: '', commits: [" + Q_OLD + ", " + Q_OTHER + "] }));"
        )

        self.assertEqual(
            result,
            {"expandKey": "explorer:" + OLD_COMMIT, "revealedCommit": OLD_COMMIT},
        )

    def test_an_already_expanded_commit_is_recorded_but_not_re_expanded(self):
        result = self._run_node(
            "emit(plan(fileView({ diffCommit: " + Q_OLD + " }),"
            " { expanded: ['explorer:" + OLD_COMMIT + "'], revealed: '',"
            "   commits: [" + Q_OLD + "] }));"
        )

        self.assertEqual(result, {"expandKey": "", "revealedCommit": OLD_COMMIT})

    def test_a_collapse_after_the_reveal_is_final(self):
        """Reveal is once per pane per commit, so no render can undo a collapse."""
        result = self._run_node(
            "emit(plan(fileView({ diffCommit: " + Q_OLD + " }),"
            " { expanded: [], revealed: " + Q_OLD + ", commits: [" + Q_OLD + "] }));"
        )

        self.assertEqual(result, {"expandKey": "", "revealedCommit": ""})

    def test_a_commit_outside_the_loaded_graph_stays_eligible(self):
        """Recording it would suppress the reveal once the graph does carry it."""
        result = self._run_node(
            "emit(plan(fileView({ diffCommit: " + Q_OLD + " }),"
            " { expanded: [], revealed: '', commits: [" + Q_OTHER + "] }));"
        )

        self.assertEqual(result, {"expandKey": "", "revealedCommit": ""})

    def test_a_worktree_diff_reveals_nothing(self):
        result = self._run_node(
            "emit(plan(fileView({ diffMode: 'worktree' }),"
            " { expanded: [], revealed: '', commits: [" + Q_OLD + "] }));"
        )

        self.assertEqual(result, {"expandKey": "", "revealedCommit": ""})

    def test_the_expansion_key_matches_the_persisted_wire_prefix(self):
        keys = self._run_node(
            "emit(["
            "  git_active.commitKey(" + Q_OLD + "),"
            "  git_active.commitKey('  '),"
            "  git_active.commitKey(null)"
            "]);"
        )

        self.assertEqual(keys, ["explorer:" + OLD_COMMIT, "", ""])


ICONS_JS = STATIC_JS / "terminal-icons.js"

# The DOM half, run against a stubbed page: the paint that marks the row, and
# the sidebar capture that a detached (cached-group) pane goes through.
ADAPTER_HARNESS = """
const fs = require('fs');
const vm = require('vm');

class El {
    constructor(dataset) {
        this.dataset = dataset || {};
        this.classes = new Set();
        this.attributes = {};
        this.revealed = 0;
        const self = this;
        this.classList = {
            toggle(name, force) {
                if (force) { self.classes.add(name); } else { self.classes.delete(name); }
            },
            add: name => self.classes.add(name),
            remove: name => self.classes.delete(name),
            contains: name => self.classes.has(name)
        };
    }
    setAttribute(name, value) { this.attributes[name] = value; }
    removeAttribute(name) { delete this.attributes[name]; }
    scrollIntoView() { this.revealed += 1; }
}

const P = 'web/api.py';
const OLD = '6e01550';
const OTHER = '9c28e89';

const row = (id, commit, mode) => {
    const element = new El({
        explorerCopyPath: P,
        explorerGitRowCommit: commit,
        explorerGitRowMode: mode
    });
    element.id = id;
    return element;
};
const rows = [
    row('staged', '', 'staged'),
    row('unstaged', '', 'worktree'),
    row('old-commit', OLD, ''),
    row('other-commit', OTHER, '')
];
const commitButtons = [OLD, OTHER].map(hash => {
    const element = new El({ explorerGitCommitToggle: hash });
    element.id = hash;
    return element;
});
const panel = {
    querySelectorAll: selector => (
        selector.indexOf('explorer-git-commit-toggle') !== -1 ? commitButtons : rows
    )
};

const sandbox = {
    console,
    document: {
        getElementById: id => (id === 'explorer-git-panel-0' ? panel : null),
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
    sessionIds: ['s0'],
    escHtml: value => String(value == null ? '' : value)
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4], process.argv[5]].forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});
// In a browser `window` *is* the global; the sandbox keeps them apart, so the
// model the adapter reaches for through `window.` is republished here.
sandbox.window.GridVibeExplorerGitActive = sandbox.GridVibeExplorerGitActive;

// Rendering the whole panel needs the whole sidebar DOM; what matters here is
// that the reveal asks for exactly one re-render and one presentation write.
let renders = 0;
let notes = 0;
sandbox.renderExplorerGitPanel = () => { renders += 1; };
sandbox.notePanePresentationChanged = () => { notes += 1; };

const marked = () => ({
    rows: rows.filter(item => item.classes.has('active')).map(item => item.id),
    aria: rows.filter(item => item.attributes['aria-current']).map(item => item.id),
    commits: commitButtons.filter(item => item.classes.has('active')).map(item => item.id)
});

const pane = (view) => {
    const built = Object.assign({
        _explorerMode: 'file',
        _explorerFilePath: P,
        _explorerDiffCommit: '',
        _explorerDiffMode: '',
        _explorerGitRepo: { commits: [{ hash: OLD }, { hash: OTHER }] }
    }, view || {});
    sandbox.terminals[0] = built;
    return built;
};

// `instanceof Set` is realm-sensitive, so anything the page would have built
// for itself is built inside the context rather than handed in from here.
const inSandbox = code => vm.runInContext(code, sandbox);

const emit = value => console.log(JSON.stringify(value));
"""


@unittest.skipUnless(NODE, "Node.js is required for Git active-row tests")
class ExplorerGitActiveAdapterTestCase(unittest.TestCase):
    """The DOM half, executed against the real extracted sidebar adapter."""

    def _run_node(self, body: str):
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "harness.js"
            script.write_text(ADAPTER_HARNESS + "\n" + body + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    NODE,
                    str(script),
                    str(ICONS_JS),
                    str(GIT_ACTIVE_JS),
                    str(EXPLORER_VIEWER_JS),
                    str(EXPLORER_GIT_SIDEBAR_JS),
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_a_commit_diff_marks_its_row_and_its_commit(self):
        result = self._run_node(
            "pane({ _explorerDiffCommit: OLD });"
            "sandbox.paintExplorerGitActiveRows(0);"
            "emit(marked());"
        )

        self.assertEqual(
            result,
            {
                "rows": ["old-commit"],
                "aria": ["old-commit"],
                "commits": [OLD_COMMIT],
            },
        )

    def test_navigating_back_to_a_listing_drops_the_mark(self):
        result = self._run_node(
            "pane({ _explorerDiffCommit: OLD });"
            "sandbox.paintExplorerGitActiveRows(0);"
            "pane({ _explorerMode: 'directory', _explorerFilePath: '' });"
            "sandbox.paintExplorerGitActiveRows(0);"
            "emit(marked());"
        )

        self.assertEqual(result, {"rows": [], "aria": [], "commits": []})

    def test_a_restored_commit_diff_opens_its_row_once_and_saves_it(self):
        result = self._run_node(
            "const p = pane({ _explorerDiffCommit: OLD });"
            "sandbox.syncExplorerGitActiveRows(0);"
            "const first = { renders, notes,"
            "  expanded: [...p._explorerDiffExpandedCommits] };"
            # A second pass must be inert: this is what a poll, a tab switch,
            # or any later render of the panel does.
            "sandbox.syncExplorerGitActiveRows(0);"
            "emit({ first, renders, notes,"
            "  expanded: [...p._explorerDiffExpandedCommits] });"
        )

        self.assertEqual(result["first"]["renders"], 1)
        self.assertEqual(result["first"]["notes"], 1)
        self.assertEqual(result["first"]["expanded"], ["explorer:" + OLD_COMMIT])
        self.assertEqual(result["renders"], 1)
        self.assertEqual(result["notes"], 1)
        self.assertEqual(result["expanded"], ["explorer:" + OLD_COMMIT])

    def test_a_manual_collapse_survives_every_later_render(self):
        result = self._run_node(
            "const p = pane({ _explorerDiffCommit: OLD });"
            "sandbox.syncExplorerGitActiveRows(0);"
            "p._explorerDiffExpandedCommits.clear();"
            "sandbox.syncExplorerGitActiveRows(0);"
            "sandbox.syncExplorerGitActiveRows(0);"
            "emit([...p._explorerDiffExpandedCommits]);"
        )

        self.assertEqual(result, [])

    def test_a_detached_pane_still_reports_its_own_expansion(self):
        """A cached group's panes hold no grid slot: `terminals.indexOf()` is -1.

        Resolving the pane by slot alone reported the function's defaults, so
        every background workspace tab's tree and Git expansion was written
        away as empty on the next save.
        """
        result = self._run_node(
            "const detached = inSandbox(`({"
            "  _explorerSidebarWidth: 320,"
            "  _explorerSidebarScroll: { git: { x: 0, y: 0.5 } },"
            "  _explorerTreeExpanded: new Set(['web', 'web/static']),"
            "  _explorerDiffExpandedCommits: new Set(['explorer:6e01550'])"
            "})`);"
            "sandbox.terminals = [];"
            "const captured = sandbox.explorerSidebarPresentation(-1, detached);"
            "emit({ captured, bySlot: sandbox.explorerSidebarPresentation(-1) });"
        )

        self.assertEqual(result["captured"]["width"], 320)
        self.assertEqual(result["captured"]["expanded"], ["web", "web/static"])
        self.assertEqual(
            result["captured"]["gitExpanded"], ["explorer:" + OLD_COMMIT]
        )
        # Unchanged for a pane nobody can name: the caller has nothing to send.
        self.assertEqual(result["bySlot"]["gitExpanded"], [])

    def test_collapse_render_restores_the_commit_message_caret(self):
        result = self._run_node(
            "const original = { selectionStart: 4, selectionEnd: 9 };"
            "const replacement = {"
            "  focused: 0, selection: null,"
            "  focus() { this.focused += 1; },"
            "  setSelectionRange(start, end) { this.selection = [start, end]; }"
            "};"
            "sandbox.document.activeElement = original;"
            "sandbox.document.getElementById = () => original;"
            "const state = sandbox.explorerGitCommitMessageFocusState(0);"
            "sandbox.document.getElementById = () => replacement;"
            "sandbox.restoreExplorerGitCommitMessageFocus(0, state);"
            "emit({ state, focused: replacement.focused, selection: replacement.selection });"
        )

        self.assertEqual(result["state"], {"start": 4, "end": 9})
        self.assertEqual(result["focused"], 1)
        self.assertEqual(result["selection"], [4, 9])


class ExplorerGitActiveWiringTestCase(unittest.TestCase):
    """The hooks the DOM adapter needs, which only exist as markup/registration."""

    def test_commit_file_rows_carry_their_own_diff_identity(self):
        source = EXPLORER_GIT_SIDEBAR_JS.read_text(encoding="utf-8")

        self.assertIn("data-explorer-git-row-commit=", source)
        self.assertIn("data-explorer-git-row-mode=", source)

    def test_the_model_is_served_to_the_workspace_page(self):
        markup = TERMINALS_HTML.read_text(encoding="utf-8")

        self.assertIn("js/explorer-git-active.js", markup)
        self.assertIn("js/explorer-git-sidebar.js", markup)


if __name__ == "__main__":
    unittest.main()
