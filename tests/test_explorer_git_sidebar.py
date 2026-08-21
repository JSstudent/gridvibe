"""Behavioral coverage for the extracted Git sidebar request adapter."""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
SIDEBAR_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-git-sidebar.js"
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
    terminals: [pane],
    sessionIds: ['session id'],
    document: {
        activeElement: null,
        getElementById: () => null
    },
    window: {},
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
vm.createContext(sandbox);
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
    const label = sandbox.explorerGitRepoLabel(payload.git);
    await sandbox.loadExplorerGitRepo(0);
    pane._explorerPath = 'repo one/docs';
    // Root scope is cached across navigation.
    await sandbox.loadExplorerGitRepo(0);
    await sandbox.toggleExplorerGitPinnedScope(0);
    pane._explorerPath = 'repo one/docs/deeper';
    // The captured folder is cached across later navigation too.
    await sandbox.loadExplorerGitRepo(0);
    await sandbox.toggleExplorerGitFollowBrowsing(0);
    await sandbox.performExplorerGitAction(0, 'publish', {});
    await sandbox.toggleExplorerGitFollowBrowsing(0);
    await sandbox.toggleExplorerGitPinnedScope(0);
    pane._explorerGitRepoError = 'Root is not a repository';
    pane._explorerGitRepo = null;
    const errorPanel = {
        innerHTML: '',
        querySelector: () => null
    };
    sandbox.document.getElementById = () => errorPanel;
    sandbox.renderExplorerGitPanel(0);
    process.stdout.write(JSON.stringify({
        builtRoot,
        builtFollow,
        label,
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
                [NODE, str(script), str(SIDEBAR_JS)],
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
        self.assertEqual(payload["label"], "repo one · main ↑2 *")
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


if __name__ == "__main__":
    unittest.main()
