"""Behavioral coverage for the two legacy localStorage cache families.

Stage 7 of `docs/session_group_persistence_restore_audit_2026-08-07.md`
(SGP-09) bounds the explorer-theme override object to live pane keys and drops
a forgotten workspace's top-bar cache key. Both halves run in Node against the
real modules — `explorer-theme-store.js` is require()-able, `shared.js` is
evaluated in a vm with stubbed globals — so the bounding rules are executed,
not asserted as source text.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
THEME_STORE_JS = STATIC_JS / "explorer-theme-store.js"
SHARED_JS = STATIC_JS / "shared.js"

NODE = shutil.which("node")

STORAGE_STUB = """
function memoryStorage(initial) {
    const backing = new Map(Object.entries(initial || {}));
    return {
        backing,
        getItem: key => (backing.has(key) ? backing.get(key) : null),
        setItem: (key, value) => { backing.set(key, String(value)); },
        removeItem: key => { backing.delete(key); }
    };
}
"""


@unittest.skipUnless(NODE, "Node.js is required for client cache tests")
class ExplorerThemeStoreBoundTestCase(unittest.TestCase):
    """SGP-09 — the override object can never accumulate dead pane keys."""

    def _run_node(self, harness: str):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(THEME_STORE_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_write_drops_entries_whose_panes_are_gone(self):
        result = self._run_node(
            STORAGE_STUB
            + """
            const store = require(process.argv[2]);
            const storage = memoryStorage({
                [store.STORAGE_KEY]: JSON.stringify({
                    'session-dead': 'light',
                    'session-live-a': 'dark',
                    'session-live-b': 'light'
                })
            });
            store.saveTheme(
                storage, 'session-live-a', 'light', ['session-live-a', 'session-live-b']
            );
            const written = JSON.parse(storage.backing.get(store.STORAGE_KEY));
            process.stdout.write(JSON.stringify({
                keys: Object.keys(written).sort(),
                toggled: written['session-live-a'],
                untouched: written['session-live-b']
            }));
            """
        )
        # The toggle landed, the other live pane kept its override, and the
        # dead session id left the object in the same write.
        self.assertEqual(result["keys"], ["session-live-a", "session-live-b"])
        self.assertEqual(result["toggled"], "light")
        self.assertEqual(result["untouched"], "light")

    def test_grid_build_prune_drops_a_restarts_dead_session_ids(self):
        result = self._run_node(
            STORAGE_STUB
            + """
            const store = require(process.argv[2]);
            const storage = memoryStorage({
                [store.STORAGE_KEY]: JSON.stringify({
                    'session-before-restart': 'light',
                    'group:0': 'dark',
                    'session-after-restart': 'dark'
                })
            });
            const pruned = store.pruneStore(
                storage, ['group:0', 'session-after-restart']
            );
            process.stdout.write(JSON.stringify({
                keys: Object.keys(pruned).sort(),
                stored: storage.backing.get(store.STORAGE_KEY)
            }));
            """
        )
        self.assertEqual(result["keys"], ["group:0", "session-after-restart"])
        self.assertNotIn("session-before-restart", result["stored"])

    def test_prune_with_no_live_panes_removes_the_key_entirely(self):
        result = self._run_node(
            STORAGE_STUB
            + """
            const store = require(process.argv[2]);
            const storage = memoryStorage({
                [store.STORAGE_KEY]: JSON.stringify({ 'session-dead': 'light' })
            });
            store.pruneStore(storage, []);
            process.stdout.write(JSON.stringify({
                present: storage.backing.has(store.STORAGE_KEY)
            }));
            """
        )
        self.assertFalse(result["present"])

    def test_legacy_bare_string_migrates_to_an_empty_store(self):
        result = self._run_node(
            STORAGE_STUB
            + """
            const store = require(process.argv[2]);
            const storage = memoryStorage({ [store.STORAGE_KEY]: 'light' });
            process.stdout.write(JSON.stringify({
                has: store.hasOverride(storage, 'session-1'),
                theme: store.getTheme(storage, 'session-1')
            }));
            """
        )
        # No pane key exists to hang the old global value on, so a pane with
        # no override of its own falls back to the default.
        self.assertFalse(result["has"])
        self.assertEqual(result["theme"], "dark")

    def test_reads_normalize_and_write_without_a_key_is_a_noop(self):
        result = self._run_node(
            STORAGE_STUB
            + """
            const store = require(process.argv[2]);
            const storage = memoryStorage({
                [store.STORAGE_KEY]: JSON.stringify({ 'session-1': 'blue' })
            });
            const before = storage.backing.get(store.STORAGE_KEY);
            store.saveTheme(storage, '', 'light', ['session-1']);
            process.stdout.write(JSON.stringify({
                theme: store.getTheme(storage, 'session-1'),
                unchanged: storage.backing.get(store.STORAGE_KEY) === before
            }));
            """
        )
        self.assertEqual(result["theme"], "light")
        self.assertTrue(result["unchanged"])


@unittest.skipUnless(NODE, "Node.js is required for client cache tests")
class WorkspaceTopbarCacheTestCase(unittest.TestCase):
    """SGP-09 — forgetting a workspace drops its top-bar cache key only."""

    def test_clear_drops_only_that_workspaces_key(self):
        harness = """
            const fs = require('fs');
            const vm = require('vm');
            const backing = new Map();
            const sandbox = {
                document: { getElementById: () => null, addEventListener: () => {} },
                window: {},
                localStorage: {
                    getItem: key => (backing.has(key) ? backing.get(key) : null),
                    setItem: (key, value) => { backing.set(key, String(value)); },
                    removeItem: key => { backing.delete(key); }
                },
                console
            };
            vm.createContext(sandbox);
            vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
            const keyFor = id => `gridvibe.terminalTopbarVisibility.${id}`;
            backing.set(keyFor('default'), 'hidden');
            backing.set(keyFor('research'), 'visible');
            sandbox.clearStoredWorkspaceTopbarVisible('default');
            sandbox.clearStoredWorkspaceTopbarVisible('');  // falls back to default
            process.stdout.write(JSON.stringify({
                removed: !backing.has(keyFor('default')),
                siblingKept: backing.get(keyFor('research')) === 'visible'
            }));
        """
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(SHARED_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        result = json.loads(completed.stdout)
        self.assertTrue(result["removed"])
        self.assertTrue(result["siblingKept"])


if __name__ == "__main__":
    unittest.main()
