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
const pane = {
    _explorerPath: 'repo one/src',
    _explorerMode: 'file',
    _explorerGitRepoLoaded: false,
    _explorerGitRepoLoading: false,
    _explorerGitActionBusy: false
};
const payload = {
    anchor_path: 'repo one/src',
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
    URLSearchParams,
    encodeURIComponent,
    terminals: [pane],
    sessionIds: ['session id'],
    document: {
        activeElement: null,
        getElementById: () => null
    },
    window: {},
    fetch: async (url, options) => {
        calls.push({ url, options: options || null });
        return { ok: true, json: async () => payload };
    }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);

// These collaborators are separately covered; this harness observes only the
// request URL/body and the anchor state applied after a successful response.
sandbox.renderExplorerGitPanels = () => {};
sandbox.syncExplorerTabGitFromRepo = () => {};

(async () => {
    const built = sandbox.explorerGitRequestUrl(
        'session id', 'state', 'repo one/src', { known: 'abc 123' }
    );
    const label = sandbox.explorerGitRepoLabel(payload.git);
    await sandbox.loadExplorerGitRepo(0);
    await sandbox.performExplorerGitAction(0, 'publish', {});
    process.stdout.write(JSON.stringify({
        built,
        label,
        calls,
        anchorPath: pane._explorerGitAnchorPath
    }));
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
"""


@unittest.skipUnless(NODE, "Node.js is required for Git sidebar tests")
class ExplorerGitSidebarRequestTestCase(unittest.TestCase):
    def test_reads_and_mutations_carry_the_same_browsed_anchor(self):
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
            payload["built"],
            "/api/explorer/session%20id/git/state?path=repo+one%2Fsrc&known=abc+123",
        )
        self.assertEqual(payload["label"], "repo one · main ↑2 *")
        self.assertEqual(
            [call["url"] for call in payload["calls"]],
            [
                "/api/explorer/session%20id/git/repo?path=repo+one%2Fsrc",
                "/api/explorer/session%20id/git/publish?path=repo+one%2Fsrc",
            ],
        )
        self.assertEqual(
            json.loads(payload["calls"][1]["options"]["body"]),
            {},
        )
        self.assertEqual(payload["anchorPath"], "repo one/src")


if __name__ == "__main__":
    unittest.main()
