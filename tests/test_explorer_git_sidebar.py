"""Behavioral coverage for the extracted Git sidebar request adapter."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SIDEBAR_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-git-sidebar.js"
# The pin button reads its three states from this module, so both
# harnesses load it rather than letting the sidebar fall back.
PIN_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-git-pin.js"
NODE = shutil.which("node")


HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const calls = [];
let presentationChanges = 0;
const pane = {
    _explorerPath: 'repo one/src',
    _explorerMode: 'file',
    _explorerGitRepoLoaded: false,
    _explorerGitRepoLoading: false,
    _explorerGitActionBusy: false
};
const payload = {
    anchor_path: '',
    revision: '0123456789abcdef',
    git: {
        available: true,
        repo_name: 'repo one',
        branch: 'main',
        ahead: 2,
        dirty: true
    },
    changes: [],
    commits: []
};
const sandbox = {
    console,
    URL,
    URLSearchParams,
    encodeURIComponent,
    EXPLORER_GIT_PIN_ICON: '<svg data-icon="pin"></svg>',
    EXPLORER_GIT_FOLLOW_ICON: '<svg data-icon="follow"></svg>',
    EXPLORER_GIT_SEARCH_ICON: '<svg data-icon="search"></svg>',
    EXPLORER_GIT_TOGGLE_ICON: '<svg data-icon="git"></svg>',
    EXPLORER_FOLDER_ICON: '<svg data-icon="folder"></svg>',
    terminals: [pane],
    sessionIds: ['session id'],
    document: {
        activeElement: null,
        getElementById: () => null
    },
    fetch: async (url, options) => {
        calls.push({ url, options: options || null });
        const pathScoped = url.includes('scope=path');
        return {
            ok: true,
            json: async () => ({
                ...payload,
                anchor_path: pathScoped
                    ? new URL(`http://gridvibe.invalid${url}`).searchParams.get('path') || ''
                    : ''
            })
        };
    }
};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

// These collaborators are separately covered; this harness observes only the
// request URL/body, scope caching, and the anchor state applied after a
// successful response.
sandbox.renderExplorerGitPanels = () => {};
sandbox.syncExplorerTabGitFromRepo = () => {};
sandbox.notePanePresentationChanged = () => { presentationChanges += 1; };
sandbox.escHtml = value => String(value == null ? '' : value);
sandbox.wireExplorerCopyPathMenu = () => {};

(async () => {
    const builtRoot = sandbox.explorerGitRequestUrl(
        'session id', 'state', null, { known: 'abc 123' }
    );
    const builtFollow = sandbox.explorerGitRequestUrl(
        'session id', 'state', 'repo one/src', { known: 'abc 123' }
    );
    const label = sandbox.explorerGitBranchLabel(payload.git);
    /* The three things that can stand where a branch name goes: a branch, a
       detachment named as one, and a payload that reported neither (an older
       server) keeping the bare abbreviation it always showed. */
    const detachedLabel = sandbox.explorerGitBranchLabel({
        available: true,
        branch: null,
        detached: true,
        head: '3c2574dabc12',
        dirty: true
    });
    const headOnlyLabel = sandbox.explorerGitBranchLabel({
        available: true,
        branch: null,
        head: '3c2574dabc12'
    });
    const detachedAtRef = {
        available: true,
        branch: null,
        detached: true,
        detached_ref: 'origin/bfla_db_name',
        detached_at: true,
        head: '3c2574dabc12'
    };
    const detachedAtRefLabel = sandbox.explorerGitBranchLabel(detachedAtRef);
    /* Committing on a detached HEAD is `from`, not `at`, and the tooltip is
       where the object id goes once the label is spent on a name. */
    const detachedFromRefLabel = sandbox.explorerGitBranchLabel({
        ...detachedAtRef,
        detached_at: false
    });
    const detachedAtRefTitle = sandbox.explorerGitBranchTitle(detachedAtRef);
    const branchTitle = sandbox.explorerGitBranchTitle(payload.git);
    await sandbox.loadExplorerGitRepo(0);
    pane._explorerPath = 'repo one/docs';
    // Root scope is cached across navigation.
    await sandbox.loadExplorerGitRepo(0);
    await sandbox.toggleExplorerGitPinHere(0);
    pane._explorerPath = 'repo one/docs/deeper';
    // The captured folder is cached across later navigation too.
    await sandbox.loadExplorerGitRepo(0);
    await sandbox.toggleExplorerGitFollowBrowsing(0);
    await sandbox.performExplorerGitAction(0, 'publish', {});
    await sandbox.toggleExplorerGitFollowBrowsing(0);
    /* The button is "pin here", not "is there a pin": it clears only while the
       browsed folder *is* the pinned one. From anywhere else it re-pins, which
       is why the walk back to the pin comes first -- and why this issues no
       request of its own, navigation being free in this harness. */
    pane._explorerPath = 'repo one/docs';
    await sandbox.toggleExplorerGitPinHere(0);
    pane._explorerGitRepoError = 'Root is not a repository';
    pane._explorerGitRepo = null;
    const errorPanel = {
        innerHTML: '',
        querySelector: () => null,
        // The commit cards' one delegated placement listener is attached to
        // the panel by every render, the error panel included.
        addEventListener: () => {}
    };
    sandbox.document.getElementById = () => errorPanel;
    sandbox.renderExplorerGitPanel(0);
    process.stdout.write(JSON.stringify({
        builtRoot,
        builtFollow,
        label,
        detachedLabel,
        headOnlyLabel,
        detachedAtRefLabel,
        detachedFromRefLabel,
        detachedAtRefTitle,
        branchTitle,
        calls,
        anchorPath: pane._explorerGitAnchorPath,
        following: Boolean(pane._explorerGitFollowBrowsing),
        pinned: typeof pane._explorerGitPinnedPath === 'string',
        presentationChanges,
        errorMarkup: errorPanel.innerHTML
    }));
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
"""


@unittest.skipUnless(NODE, "Node.js is required for Git sidebar tests")
class ExplorerGitSidebarRequestTestCase(unittest.TestCase):
    def test_pin_and_follow_select_independent_git_scopes(self):
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "harness.js"
            script.write_text(HARNESS, encoding="utf-8")
            result = subprocess.run(
                [NODE, str(script), str(SIDEBAR_JS), str(PIN_JS)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["builtRoot"],
            "/api/explorer/session%20id/git/state?known=abc+123",
        )
        self.assertEqual(
            payload["builtFollow"],
            "/api/explorer/session%20id/git/state?scope=path&path=repo+one%2Fsrc&known=abc+123",
        )
        self.assertEqual(payload["label"], "main ↑2 *")
        # A detached HEAD says so; the bare abbreviation it used to print is
        # indistinguishable from a branch named `3c2574d`.
        self.assertEqual(payload["detachedLabel"], "Detached at 3c2574d *")
        self.assertEqual(payload["headOnlyLabel"], "3c2574d")
        # What `git branch` prints for the same repository, in the same words.
        self.assertEqual(
            payload["detachedAtRefLabel"], "Detached at origin/bfla_db_name"
        )
        self.assertEqual(
            payload["detachedFromRefLabel"], "Detached from origin/bfla_db_name"
        )
        self.assertEqual(
            payload["detachedAtRefTitle"],
            "Detached at origin/bfla_db_name (3c2574d)",
        )
        self.assertEqual(payload["branchTitle"], "main ↑2 *")
        self.assertEqual(
            [call["url"] for call in payload["calls"]],
            [
                "/api/explorer/session%20id/git/repo",
                "/api/explorer/session%20id/git/repo?scope=path&path=repo+one%2Fdocs",
                "/api/explorer/session%20id/git/repo?scope=path&path=repo+one%2Fdocs%2Fdeeper",
                "/api/explorer/session%20id/git/publish?scope=path&path=repo+one%2Fdocs%2Fdeeper",
                "/api/explorer/session%20id/git/repo?scope=path&path=repo+one%2Fdocs",
                "/api/explorer/session%20id/git/repo",
            ],
        )
        self.assertEqual(
            json.loads(payload["calls"][3]["options"]["body"]),
            {},
        )
        self.assertEqual(payload["anchorPath"], "")
        self.assertFalse(payload["following"])
        self.assertFalse(payload["pinned"])
        self.assertEqual(payload["presentationChanges"], 4)
        self.assertIn('data-explorer-git-pin-toggle', payload["errorMarkup"])
        self.assertIn('data-explorer-git-follow-toggle', payload["errorMarkup"])
        self.assertIn('aria-pressed="false"', payload["errorMarkup"])
        self.assertLess(
            payload["errorMarkup"].index('data-explorer-git-pin-toggle'),
            payload["errorMarkup"].index('data-explorer-git-follow-toggle'),
        )
        self.assertLess(
            payload["errorMarkup"].index('data-explorer-git-follow-toggle'),
            payload["errorMarkup"].index('explorer-git-commit-search-toggle'),
        )


SCOPE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

/* A minimal element stub: enough to capture innerHTML and to let a test click
   one of the controls the panel just wired. */
function makePanel() {
    const handlers = new Map();
    return {
        innerHTML: '',
        handlers,
        querySelector(selector) {
            const hook = selector.replace(/^\[|\]$/g, '');
            if (!this.innerHTML.includes(hook)) return null;
            /* The pin affordances are repainted attribute-only after a write,
               so a control stub has to be able to take attributes as well as
               a listener. */
            const classes = new Set();
            return {
                attributes: {},
                title: '',
                hidden: false,
                classList: {
                    toggle(name, on) { if (on) { classes.add(name); } else { classes.delete(name); } },
                    contains: name => classes.has(name)
                },
                setAttribute(name, value) { this.attributes[name] = value; },
                getAttribute(name) { return this.attributes[name]; },
                addEventListener: (_type, handler) => handlers.set(hook, handler)
            };
        },
        querySelectorAll: () => [],
        addEventListener: () => {},
        click(hook) {
            const handler = handlers.get(hook);
            if (!handler) throw new Error(`nothing wired for ${hook}`);
            handler();
        }
    };
}

const panel = makePanel();
const pane = {
    _explorerPath: 'web/static/js',
    _explorerMode: 'directory',
    _explorerGitSidebarOpen: true,
    _explorerGitRepoLoaded: false,
    _explorerGitRepoLoading: false,
    _explorerGitActionBusy: false
};
const loadedRepo = {
    anchor_path: '',
    revision: 'rev-1',
    git: { available: true, repo_name: 'gridvibe', branch: 'main', repo_root: '/repo' },
    changes: [],
    commits: []
};

const sandbox = {
    console,
    URL, URLSearchParams, encodeURIComponent,
    Set, Map, Boolean, String, Number, Object, Array, JSON, Promise, Error,
    EXPLORER_GIT_PIN_ICON: '<svg data-icon="pin"></svg>',
    EXPLORER_GIT_FOLLOW_ICON: '<svg data-icon="follow"></svg>',
    EXPLORER_GIT_SEARCH_ICON: '<svg data-icon="search"></svg>',
    EXPLORER_GIT_HASH_ICON: '<svg data-icon="hash"></svg>',
    EXPLORER_GIT_REVERT_ICON: '<svg data-icon="revert"></svg>',
    EXPLORER_GIT_TOGGLE_ICON: '<svg data-icon="git"></svg>',
    EXPLORER_FOLDER_ICON: '<svg data-icon="folder"></svg>',
    UI_PLUS_ICON: '<svg data-icon="plus"></svg>',
    UI_MINUS_ICON: '<svg data-icon="minus"></svg>',
    terminals: [pane],
    sessionIds: ['s1'],
    document: { activeElement: null, getElementById: () => panel },
    window: {
        GridVibeExplorerGitActive: {
            commitKey: hash => `explorer:${hash}`,
            viewerTarget: () => null,
            activeRowPlan: () => ({ rows: [], reveal: null })
        }
    },
    fetch: async () => ({ ok: true, json: async () => loadedRepo })
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
// Onto the harness's own `window`, beside the Git active policy already there.
vm.runInContext(
    fs.readFileSync(process.argv[3], 'utf8') + '\nwindow.GridVibeExplorerGitPin = GridVibeExplorerGitPin;',
    sandbox
);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

sandbox.escHtml = value => String(value == null ? '' : value);
sandbox.wireExplorerCopyPathMenu = () => {};
sandbox.notePanePresentationChanged = () => {};
sandbox.syncExplorerTabGitFromRepo = () => {};
sandbox.syncExplorerGitActiveRows = () => {};
sandbox.renderExplorerGitFileRows = () => '';
sandbox.splitExplorerGitChanges = () => ({ staged: [], unstaged: [] });
sandbox.explorerGitCanBulkDiscard = () => false;

const renderError = (message) => {
    pane._explorerGitRepo = null;
    pane._explorerGitRepoError = message;
    panel.innerHTML = '';
    panel.handlers.clear();
    sandbox.renderExplorerGitPanel(0);
    return panel.innerHTML;
};
const renderLoaded = () => {
    pane._explorerGitRepoError = '';
    pane._explorerGitRepo = loadedRepo;
    panel.innerHTML = '';
    panel.handlers.clear();
    sandbox.renderExplorerGitPanel(0);
    return panel.innerHTML;
};
const emit = (payload) => process.stdout.write(JSON.stringify(payload));
"""


@unittest.skipUnless(NODE, "Node.js is required for Git sidebar tests")
class ExplorerGitScopeSurfaceTestCase(unittest.TestCase):
    """The sidebar says which scope it is showing, and offers a way out of a
    scope that cannot resolve.

    A pin is re-applied faithfully across a restart -- including one made in a
    folder that is not inside any worktree -- so the panel that comes back says
    "Folder is not inside a Git worktree" about a folder it does not name, one
    the user may have navigated away from long ago. Naming the scope, and
    giving the panel the one action that resolves it, is what separates "the
    pin was lost" from "the pin is doing exactly what you set it to".
    """

    def _render(self, body):
        script = (
            SCOPE_HARNESS
            + "\n(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        with TemporaryDirectory() as temp_dir:
            script_path = Path(temp_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(SIDEBAR_JS), str(PIN_JS)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=30,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_an_unresolvable_pin_is_named_and_can_be_cleared_from_the_panel(self):
        result = self._render(
            """
            pane._explorerGitPinnedPath = 'web/static';
            const markup = renderError('Folder is not inside a Git worktree');
            panel.click('data-explorer-git-clear-pin');
            // The clear runs a reload; let its microtasks settle.
            await Promise.resolve();
            emit({
                markup,
                stillPinned: typeof pane._explorerGitPinnedPath === 'string'
            });
            """
        )
        self.assertIn("data-explorer-git-clear-pin", result["markup"])
        self.assertIn("Pinned Git folder", result["markup"])
        # The path itself, not just the word "pinned".
        self.assertIn("web/static", result["markup"])
        self.assertIn('role="status"', result["markup"])
        # Clear pin clears the pin, rather than toggling it back on.
        self.assertFalse(result["stillPinned"])

    def test_an_unpinned_pane_gets_no_clear_pin_control(self):
        """The error is then about the pane's own root, and there is no pin to
        offer to clear -- an inert control would be worse than none."""
        result = self._render(
            """
            emit({ markup: renderError('Folder is not inside a Git worktree') });
            """
        )
        self.assertNotIn("data-explorer-git-clear-pin", result["markup"])
        self.assertNotIn("Pinned Git folder", result["markup"])

    def test_a_root_pin_names_the_root_instead_of_naming_nothing(self):
        """'' and "no pin" ask the server for the same thing, so without a word
        for it a root pin round-trips perfectly and still reads as lost."""
        result = self._render(
            """
            pane._explorerGitPinnedPath = '';
            const pinnedAtRoot = renderLoaded();
            delete pane._explorerGitPinnedPath;
            const unpinned = renderLoaded();
            pane._explorerGitPinnedPath = 'web/static/js';
            const pinnedDeep = renderLoaded();
            delete pane._explorerGitPinnedPath;
            pane._explorerGitFollowBrowsing = true;
            const following = renderLoaded();
            emit({ pinnedAtRoot, unpinned, pinnedDeep, following });
            """
        )
        self.assertIn("explorer-git-repo-scope", result["pinnedAtRoot"])
        self.assertIn(">root<", result["pinnedAtRoot"])
        self.assertIn("Git scope pinned to: root", result["pinnedAtRoot"])
        self.assertIn('data-icon="folder"', result["pinnedAtRoot"])
        self.assertIn('data-icon="git"', result["pinnedAtRoot"])
        self.assertIn('data-icon="pin"', result["pinnedAtRoot"])
        self.assertLess(
            result["pinnedAtRoot"].index('data-icon="folder"'),
            result["pinnedAtRoot"].index('data-icon="git"'),
        )
        self.assertLess(
            result["pinnedAtRoot"].index('data-icon="git"'),
            result["pinnedAtRoot"].index('data-icon="pin"'),
        )
        # The default scope is the pane's root and needs no word for it.
        self.assertNotIn("explorer-git-repo-scope", result["unpinned"])
        self.assertIn(">web/static/js<", result["pinnedDeep"])
        # Follow names its scope too, and says which control chose it.
        self.assertIn("Git scope follows the browsed folder", result["following"])
        self.assertIn('data-icon="follow"', result["following"])

    def test_a_file_scope_names_the_file_and_bulk_tooltips_name_that_scope(self):
        result = self._render(
            """
            pane._explorerMode = 'file';
            pane._explorerFilePath = 'web/static/js/app.js';
            pane._explorerGitPinnedPath = 'web/static/js/app.js';
            pane._explorerGitPinKind = 'file';
            emit({ html: renderLoaded() });
            """
        )["html"]
        # Short on the row, whole on hover: the sidebar column has room for a
        # leaf, but two files called app.js in different folders are one label
        # and two scopes, so nothing that *names* the scope may abbreviate it.
        self.assertIn(">app.js<", result)
        self.assertIn("Git scope pinned to: web/static/js/app.js", result)
        self.assertNotIn("Git scope pinned to: app.js<", result)
        for action in ("Stage", "Unstage", "Discard"):
            self.assertIn(f'{action} all changes in file web/static/js/app.js', result)
        self.assertIn("Clear the pinned Git file: web/static/js/app.js", result)
        self.assertIn("Clear pinned Git file", result)

    def test_a_pin_and_a_live_follow_are_two_rows_and_only_one_can_be_cleared(self):
        """One row showed the *effective* scope, so with Follow on it named the
        browsed folder, wore the chain icon, and still carried Clear pin -- an
        action about a path that was not on the row."""
        result = self._render(
            """
            pane._explorerGitPinnedPath = 'web/static';
            pane._explorerGitFollowBrowsing = true;
            const both = renderLoaded();
            pane._explorerGitFollowBrowsing = false;
            const pinOnly = renderLoaded();
            emit({ both, pinOnly });
            """
        )
        both = result["both"]
        # Two rows, the pin first, each naming its own path.
        self.assertEqual(both.count("explorer-git-repo-scope-"), 2)
        self.assertEqual(both.count("explorer-git-repo-scope-pin"), 1)
        self.assertEqual(both.count("explorer-git-repo-scope-follow"), 1)
        self.assertLess(
            both.index("explorer-git-repo-scope-pin"),
            both.index("explorer-git-repo-scope-follow"),
        )
        self.assertIn(">web/static<", both)
        self.assertIn(">web/static/js<", both)

        # The clear is on the pin row, and there is exactly one of it.
        self.assertEqual(both.count("data-explorer-git-scope-clear"), 1)
        pin_row = both[
            both.index("explorer-git-repo-scope-pin"):
            both.index("explorer-git-repo-scope-follow")
        ]
        self.assertIn("data-explorer-git-scope-clear", pin_row)
        # And it names the path it is about, so the two rows cannot be confused.
        self.assertIn("Clear the pinned Git folder: web/static", pin_row)

        # The pin is overridden while Follow is on, said in words as well as
        # in the class the styling keys on -- and it does not disappear, since
        # turning Follow off lands back on it.
        self.assertIn("is-overridden", both)
        self.assertIn("overridden while Follow is on", both)
        self.assertNotIn("is-overridden", result["pinOnly"])
        self.assertEqual(result["pinOnly"].count("explorer-git-repo-scope-"), 1)
        self.assertNotIn("explorer-git-repo-scope-follow", result["pinOnly"])


if __name__ == "__main__":
    unittest.main()
