"""Behavioral coverage for multi-entry explorer filesystem actions.

`explorer-fs.js` is not DOM-free, so it is evaluated in a Node `vm` against a
stubbed page — the same technique `test_explorer_theme_store.py` uses for
`shared.js`. That makes the menu builder and the batch runners *executed*
rather than pattern-matched: the assertions below are about which menu items
come out, which requests go over the wire, and how many confirmations and
refreshes a batch costs.

The invariants that matter for a batch of N atomic requests:
  * a batch of one is indistinguishable from the single-entry action it replaced
  * one busy hold, one confirmation, and one surface refresh per batch
  * a partial failure reports what landed and retries only what did not
  * nothing crosses a session, a root revision, or the read-only contract
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = ROOT / "web" / "static" / "js"
SELECTION_JS = STATIC_JS / "explorer-selection.js"
CONTROLLER_JS = STATIC_JS / "explorer-fs.js"

NODE = shutil.which("node")

# A minimal terminals page: one live explorer pane at index 0. Everything the
# controller reaches for that belongs to another module is recorded rather than
# simulated, so a test can assert on what the controller asked the page to do.
HARNESS_PREAMBLE = """
const fs = require('fs');
const vm = require('vm');

const calls = {
    requests: [],
    confirms: [],
    toasts: [],
    errors: [],
    directoryLoads: [],
    treeReloads: 0,
    gitInvalidations: 0,
    highlights: [],
    busyLabels: []
};

function fakeElement() {
    const node = {
        classList: { add() {}, remove() {}, contains: () => false },
        dataset: {},
        appendChild() {},
        remove() {},
        replaceChildren() {},
        querySelectorAll: () => [],
        querySelector: () => null,
        setAttribute() {},
        addEventListener() {},
        insertBefore() {},
        focus() {},
        select() {}
    };
    return node;
}

const pane = {
    _session: { type: 'file-explorer' },
    _explorerRootRevision: 'root-1',
    _explorerPath: 'src',
    _explorerFilePath: '',
    _explorerFileName: '',
    _explorerMode: 'directory',
    _explorerFsBusy: null,
    _explorerTabs: [{ id: 'preview', path: '', dirPath: 'src' }],
    _explorerActiveTabId: 'preview',
    _explorerTreeSidebarOpen: false,
    _explorerGitSidebarOpen: false,
    _explorerTreeExpanded: new Set(),
    _explorerTreeChildren: new Map(),
    _explorerTreeErrors: new Map(),
    _closing: false
};

// Queue of canned responses, consumed one per request in order.
let responseQueue = [];

const sandbox = {
    console,
    Date,
    Promise,
    Set,
    Map,
    JSON,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Math,
    terminals: [pane],
    sessionIds: ['session-1'],
    EXPLORER_PREVIEW_TAB_ID: 'preview',
    document: {
        getElementById: () => fakeElement(),
        querySelectorAll: () => [],
        createElement: () => fakeElement(),
        addEventListener() {}
    },
    window: { setTimeout: (fn) => fn() },
    CSS: { escape: (value) => value },
    isExplorerSession: () => true,
    explorerEditState: () => null,
    confirmDiscardExplorerEdit: async () => true,
    showTerminalToast: (text, kind) => { calls.toasts.push({ text, kind }); },
    openGenericConfirmModal: async (options) => {
        calls.confirms.push(options);
        return sandbox.__confirmAnswer;
    },
    closeGenericConfirmModalForOwner: () => {},
    ensureExplorerTabState: () => {},
    explorerActiveTab: (p) => p._explorerTabs[0],
    explorerPreviewTab: (p) => p._explorerTabs[0],
    persistExplorerTabsToSession: () => {},
    renderExplorerTabStrip: () => {},
    renderExplorerPathBreadcrumb: () => {},
    explorerRootDirectory: () => '/repo',
    loadExplorerPane: async (index, target) => { calls.directoryLoads.push(target); },
    reloadExplorerTree: async () => { calls.treeReloads += 1; },
    invalidateExplorerGitRepo: () => {},
    loadExplorerGitRepo: async () => {},
    highlightExplorerFilesystemPath: (index, path) => { calls.highlights.push(path); },
    refreshExplorerSelectionHighlight: () => {},
    dropExplorerSelectionPaths: () => {},
    dismissExplorerContextMenu: () => {},
    __confirmAnswer: true,
    fetch: async (url, options) => {
        calls.requests.push({ url, body: JSON.parse(options.body) });
        const canned = responseQueue.shift() || { ok: true, status: 200, data: {} };
        return {
            ok: canned.ok,
            status: canned.status,
            json: async () => canned.data
        };
    }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox);

/* Overrides go on *after* evaluation: the controller's own function
   declarations would otherwise replace same-named stubs put on the sandbox
   up front. The busy label is worth observing because one hold per batch is
   the invariant that stops a second entry being refused by its predecessor. */
const realSetBusy = sandbox.setExplorerFilesystemBusy;
sandbox.setExplorerFilesystemBusy = (context, label) => {
    const held = realSetBusy(context, label);
    if (held) { calls.busyLabels.push(label); }
    return held;
};
sandbox.highlightExplorerFilesystemPath = (index, path) => { calls.highlights.push(path); };
sandbox.showExplorerFilesystemError = (context, data) => { calls.errors.push(data); };

/* Top-level `const` in a classic script lands in the global *lexical*
   environment, not on the global object, so the controller's module state is
   reachable only by evaluating its name inside the context. */
const evalIn = (expr) => vm.runInContext(expr, sandbox);
const clipboard = () => evalIn('explorerFilesystemClipboards').get('session-1');
const hasClipboard = () => evalIn('explorerFilesystemClipboards').has('session-1');

/* Each context carries a one-use token that opening a menu supersedes, so a
   test that opens a menu between two actions needs a fresh one. */
const context = () => sandbox.explorerFilesystemActionContext(0, {
    path: 'src/a.js', kind: 'file', revision: 'r1', surface: 'preview'
});
const entry = (path, kind, revision) => ({ path, kind, revision });
const emit = (payload) => process.stdout.write(JSON.stringify(payload));
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer controller tests")
class ExplorerControllerHarness(unittest.TestCase):
    def _run_node(self, body: str):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(HARNESS_PREAMBLE + body, encoding="utf-8")
            completed = subprocess.run(
                [
                    NODE,
                    str(script_path),
                    str(SELECTION_JS),
                    str(CONTROLLER_JS),
                ],
                capture_output=True,
                text=True,
                # Menu labels carry ellipses and em dashes; Node writes UTF-8
                # regardless of the host's console codepage.
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class MenuItemsTestCase(ExplorerControllerHarness):
    """What the context menu offers for one row versus several."""

    def test_single_row_menu_is_unchanged_by_multi_select(self):
        result = self._run_node(
            """
            const items = sandbox.explorerFilesystemMenuItems(
                0, { path: 'src/a.js', kind: 'file', revision: 'r1', surface: 'preview' }
            );
            emit({
                labels: items.map(item => item.label),
                disabled: items.filter(item => item.disabled).map(item => item.label),
                afterPath: items.filter(
                    item => item.placement === 'after-path'
                ).map(item => item.label)
            });
            """
        )
        # The pre-existing single-entry menu, item for item and in order.
        self.assertEqual(
            result["labels"],
            [
                "New file…",
                "New folder…",
                "Copy",
                "Cut",
                "Paste — nothing copied",
                "Rename…",
                "Delete…",
            ],
        )
        # Nothing is on the clipboard yet, so only Paste is unavailable.
        self.assertEqual(result["disabled"], ["Paste — nothing copied"])
        self.assertEqual(result["afterPath"], ["Rename…", "Delete…"])

    def test_multi_row_menu_names_the_count_and_disables_rename(self):
        result = self._run_node(
            """
            const targets = [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2'),
                entry('src/c.js', 'file', 'r3')
            ];
            const items = sandbox.explorerFilesystemMenuItems(
                0,
                { path: 'src/a.js', kind: 'file', revision: 'r1', surface: 'preview' },
                targets
            );
            emit({
                labels: items.map(item => item.label),
                disabled: items.filter(item => item.disabled).map(item => item.label)
            });
            """
        )
        self.assertIn("Copy 3 files", result["labels"])
        self.assertIn("Cut 3 files", result["labels"])
        self.assertIn("Delete 3 files…", result["labels"])
        # Rename takes one exact leaf in the entry's own parent, so it has no
        # multi-entry meaning; it stays visible so the menu does not reshuffle.
        self.assertIn("Rename…", result["labels"])
        self.assertIn("Rename…", result["disabled"])

    def test_a_selected_descendant_is_folded_into_its_selected_ancestor(self):
        result = self._run_node(
            """
            const targets = [
                entry('src/lib', 'directory', 'r4'),
                entry('src/lib/deep.js', 'file', 'r6')
            ];
            const items = sandbox.explorerFilesystemMenuItems(
                0,
                { path: 'src/lib', kind: 'directory', revision: 'r4', surface: 'preview' },
                targets
            );
            emit({ labels: items.map(item => item.label) });
            """
        )
        # Two selected rows, but only one entry survives pruning, so the menu
        # reads as the single-entry menu rather than claiming "2 items".
        self.assertIn("Copy", result["labels"])
        self.assertNotIn("Copy 2 items", result["labels"])

    def test_paste_is_refused_when_a_cut_folder_would_swallow_the_destination(self):
        result = self._run_node(
            """
            const ctx = context();
            sandbox.cutExplorerFilesystemEntry(ctx, [entry('src/lib', 'directory', 'r4')]);
            const into = sandbox.explorerFilesystemMenuItems(
                0,
                { path: 'src/lib/nested', kind: 'directory', revision: 'r9', surface: 'tree' }
            );
            const elsewhere = sandbox.explorerFilesystemMenuItems(
                0,
                { path: 'docs', kind: 'directory', revision: 'r7', surface: 'tree' }
            );
            const find = (items) => items.find(item => /^(Move|Paste)/.test(item.label));
            emit({
                intoLabel: find(into).label,
                intoDisabled: Boolean(find(into).disabled),
                intoTitle: find(into).title,
                elsewhereDisabled: Boolean(find(elsewhere).disabled)
            });
            """
        )
        self.assertTrue(result["intoDisabled"])
        self.assertEqual(result["intoTitle"], "A folder cannot be moved into itself")
        self.assertFalse(result["elsewhereDisabled"])

    def test_a_multi_entry_cut_offers_one_paste_naming_the_whole_clipboard(self):
        result = self._run_node(
            """
            const ctx = context();
            sandbox.cutExplorerFilesystemEntry(ctx, [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2')
            ]);
            const items = sandbox.explorerFilesystemMenuItems(
                0, { path: 'docs', kind: 'directory', revision: 'r7', surface: 'tree' }
            );
            const paste = items.filter(item => /^(Move|Paste)/.test(item.label));
            emit({
                count: paste.length,
                label: paste[0].label,
                disabled: Boolean(paste[0].disabled)
            });
            """
        )
        # One paste entry for the whole clipboard, never one per cut entry.
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["label"], 'Move 2 files into "docs"')
        self.assertFalse(result["disabled"])

    def test_a_cut_already_sitting_in_the_destination_is_refused(self):
        result = self._run_node(
            """
            sandbox.cutExplorerFilesystemEntry(context(), [
                entry('docs/a.js', 'file', 'r1'),
                entry('docs/b.js', 'file', 'r2')
            ]);
            const sameFolder = sandbox.explorerFilesystemMenuItems(
                0, { path: 'docs', kind: 'directory', revision: 'r7', surface: 'tree' }
            );
            // A fresh context: the menu above superseded the previous token.
            sandbox.cutExplorerFilesystemEntry(context(), [
                entry('docs/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2')
            ]);
            const mixed = sandbox.explorerFilesystemMenuItems(
                0, { path: 'docs', kind: 'directory', revision: 'r7', surface: 'tree' }
            );
            const find = (items) => items.find(item => /^(Move|Paste)/.test(item.label));
            emit({
                same: Boolean(find(sameFolder).disabled),
                mixed: Boolean(find(mixed).disabled)
            });
            """
        )
        # Everything already in place is a no-op; a mixed clipboard still has
        # work to do and stays enabled.
        self.assertTrue(result["same"])
        self.assertFalse(result["mixed"])


class BatchDeleteTestCase(ExplorerControllerHarness):
    """Delete is the destructive path, so its batching is pinned hardest."""

    def test_a_batch_issues_one_atomic_request_per_entry(self):
        result = self._run_node(
            """
            responseQueue = [
                { ok: true, status: 200, data: { deleted_path: 'src/a.js' } },
                { ok: true, status: 200, data: { deleted_path: 'src/b.js' } },
                { ok: true, status: 200, data: { deleted_path: 'src/lib' } }
            ];
            sandbox.deleteExplorerFilesystemEntries(context(), [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2'),
                entry('src/lib', 'directory', 'r4')
            ]).then(() => emit({
                requests: calls.requests.map(call => ({
                    route: call.url.split('/').pop(),
                    path: call.body.path,
                    revision: call.body.base_revision,
                    recursive: call.body.recursive,
                    root: call.body.root_revision
                })),
                confirms: calls.confirms.length,
                busyHolds: calls.busyLabels,
                directoryLoads: calls.directoryLoads.length,
                treeReloads: calls.treeReloads,
                toasts: calls.toasts.map(t => t.text)
            }));
            """
        )
        # Three separate revision-checked requests against the existing
        # single-entry endpoint — no batch endpoint, no widened contract.
        self.assertEqual(len(result["requests"]), 3)
        self.assertEqual(
            [call["path"] for call in result["requests"]],
            ["src/a.js", "src/b.js", "src/lib"],
        )
        self.assertEqual(
            [call["revision"] for call in result["requests"]], ["r1", "r2", "r4"]
        )
        # Each entry carries its own recursive flag; only the folder gets one.
        self.assertEqual(
            [call["recursive"] for call in result["requests"]], [False, False, True]
        )
        self.assertTrue(all(call["route"] == "delete" for call in result["requests"]))
        # One confirmation and one busy hold for the whole batch.
        self.assertEqual(result["confirms"], 1)
        self.assertEqual(result["busyHolds"], ["Deleting 3…"])
        # One refresh at the end, not one per entry.
        self.assertEqual(result["directoryLoads"], 1)
        self.assertEqual(result["toasts"], ["Deleted 3"])

    def test_declining_the_confirmation_sends_nothing(self):
        result = self._run_node(
            """
            sandbox.__confirmAnswer = false;
            sandbox.deleteExplorerFilesystemEntries(context(), [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2')
            ]).then(() => emit({
                requests: calls.requests.length,
                confirms: calls.confirms.length,
                loads: calls.directoryLoads.length
            }));
            """
        )
        self.assertEqual(result["requests"], 0)
        self.assertEqual(result["confirms"], 1)
        self.assertEqual(result["loads"], 0)

    def test_the_confirmation_names_the_batch_once(self):
        result = self._run_node(
            """
            sandbox.__confirmAnswer = false;
            sandbox.deleteExplorerFilesystemEntries(context(), [
                entry('src/a.js', 'file', 'r1'),
                entry('src/lib', 'directory', 'r4')
            ]).then(() => emit({ confirm: calls.confirms[0] }));
            """
        )
        confirm = result["confirm"]
        self.assertIn("2 items", confirm["title"])
        self.assertIn("including every folder's contents", confirm["title"])
        self.assertEqual(confirm["confirmLabel"], "Delete 2")
        self.assertTrue(confirm["danger"])
        self.assertEqual(confirm["copy"], "a.js, lib")

    def test_a_batch_of_one_reads_exactly_like_the_single_entry_action(self):
        result = self._run_node(
            """
            responseQueue = [{ ok: true, status: 200, data: { deleted_path: 'src/lib' } }];
            sandbox.deleteExplorerFilesystemEntries(
                context(), [entry('src/lib', 'directory', 'r4')]
            ).then(() => emit({
                confirm: calls.confirms[0],
                busy: calls.busyLabels,
                toasts: calls.toasts.map(t => t.text)
            }));
            """
        )
        self.assertEqual(
            result["confirm"]["title"],
            'Permanently delete the folder "lib" and all of its contents?',
        )
        self.assertEqual(result["confirm"]["confirmLabel"], "Delete")
        self.assertEqual(result["confirm"]["copy"], "src/lib")
        self.assertEqual(result["busy"], ["Deleting folder…"])
        self.assertEqual(result["toasts"], ["Deleted 1"])

    def test_a_partial_failure_still_refreshes_and_reports_what_landed(self):
        result = self._run_node(
            """
            responseQueue = [
                { ok: true, status: 200, data: { deleted_path: 'src/a.js' } },
                { ok: false, status: 409, data: {
                    error: 'src/b.js changed; refresh before trying again',
                    mutated: false
                } },
                { ok: true, status: 200, data: { deleted_path: 'src/c.js' } }
            ];
            sandbox.deleteExplorerFilesystemEntries(context(), [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2'),
                entry('src/c.js', 'file', 'r3')
            ]).then(() => emit({
                requests: calls.requests.length,
                loads: calls.directoryLoads.length,
                toasts: calls.toasts.map(t => t.text)
            }));
            """
        )
        # One entry failing must not abandon the rest of the batch...
        self.assertEqual(result["requests"], 3)
        # ...and the successes still have to be reflected on screen.
        self.assertEqual(result["loads"], 1)
        # A partial batch reports through the error bar, never a success toast.
        self.assertEqual(result["toasts"], [])


class BatchClipboardTestCase(ExplorerControllerHarness):
    """Copy/cut hold N entries; paste and move spend them as N requests."""

    def test_pasting_a_multi_entry_clipboard_targets_one_destination(self):
        result = self._run_node(
            """
            const ctx = context();
            sandbox.copyExplorerFilesystemEntry(ctx, [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2')
            ]);
            responseQueue = [
                { ok: true, status: 200, data: { destination_path: 'docs/a.js' } },
                { ok: true, status: 200, data: { destination_path: 'docs/b.js' } }
            ];
            const clip = clipboard();
            sandbox.pasteExplorerFilesystemEntries(ctx, clip, 'docs').then(() => emit({
                requests: calls.requests.map(call => ({
                    route: call.url.split('/').pop(),
                    source: call.body.source_path,
                    revision: call.body.source_revision,
                    destination: call.body.destination_directory
                })),
                highlights: calls.highlights,
                busy: calls.busyLabels,
                toasts: calls.toasts.map(t => t.text)
            }));
            """
        )
        self.assertEqual(
            [call["source"] for call in result["requests"]], ["src/a.js", "src/b.js"]
        )
        self.assertTrue(all(call["route"] == "paste" for call in result["requests"]))
        # Each entry carries the revision captured when it was copied.
        self.assertEqual(
            [call["revision"] for call in result["requests"]], ["r1", "r2"]
        )
        self.assertTrue(
            all(call["destination"] == "docs" for call in result["requests"])
        )
        # Every created path is highlighted, not just the last one.
        self.assertEqual(result["highlights"], ["docs/a.js", "docs/b.js"])
        self.assertEqual(result["busy"], ["Copying 2…"])
        self.assertEqual(result["toasts"][-1], "Copied 2")

    def test_a_completed_move_spends_the_cut_clipboard(self):
        result = self._run_node(
            """
            const ctx = context();
            sandbox.cutExplorerFilesystemEntry(ctx, [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2')
            ]);
            responseQueue = [
                { ok: true, status: 200, data: {
                    moved: true, source_path: 'src/a.js', destination_path: 'docs/a.js'
                } },
                { ok: true, status: 200, data: {
                    moved: true, source_path: 'src/b.js', destination_path: 'docs/b.js'
                } }
            ];
            const clip = clipboard();
            sandbox.moveExplorerFilesystemEntries(ctx, clip, 'docs').then(() => emit({
                routes: calls.requests.map(call => call.url.split('/').pop()),
                clipboardAfter: hasClipboard(),
                toasts: calls.toasts.map(t => t.text)
            }));
            """
        )
        self.assertEqual(result["routes"], ["move", "move"])
        # A cut is consumed by its move; leaving it would let the same entries
        # be pasted again from paths that no longer exist.
        self.assertFalse(result["clipboardAfter"])
        self.assertEqual(result["toasts"][-1], "Moved 2")

    def test_a_wholly_untouched_move_failure_keeps_the_clipboard_for_retry(self):
        result = self._run_node(
            """
            const ctx = context();
            sandbox.cutExplorerFilesystemEntry(ctx, [entry('src/a.js', 'file', 'r1')]);
            responseQueue = [
                { ok: false, status: 409, data: {
                    error: 'docs/a.js already exists', mutated: false
                } }
            ];
            const clip = clipboard();
            sandbox.moveExplorerFilesystemEntries(ctx, clip, 'docs').then(() => emit({
                clipboardAfter: hasClipboard()
            }));
            """
        )
        # Nothing moved and nothing was touched, so the cut is still spendable.
        self.assertTrue(result["clipboardAfter"])

    def test_a_partly_applied_move_clears_the_clipboard(self):
        result = self._run_node(
            """
            const ctx = context();
            sandbox.cutExplorerFilesystemEntry(ctx, [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2')
            ]);
            responseQueue = [
                { ok: true, status: 200, data: {
                    moved: true, source_path: 'src/a.js', destination_path: 'docs/a.js'
                } },
                { ok: false, status: 409, data: {
                    error: 'docs/b.js already exists', mutated: false
                } }
            ];
            const clip = clipboard();
            sandbox.moveExplorerFilesystemEntries(ctx, clip, 'docs').then(() => emit({
                clipboardAfter: hasClipboard(),
                loads: calls.directoryLoads.length
            }));
            """
        )
        # Half the cut is spent: re-pasting it would re-move an entry that has
        # already moved, so the clipboard goes.
        self.assertFalse(result["clipboardAfter"])
        self.assertEqual(result["loads"], 1)

    def test_copying_prunes_a_descendant_before_it_reaches_the_clipboard(self):
        result = self._run_node(
            """
            const ctx = context();
            sandbox.copyExplorerFilesystemEntry(ctx, [
                entry('src/lib', 'directory', 'r4'),
                entry('src/lib/deep.js', 'file', 'r6'),
                entry('src/a.js', 'file', 'r1')
            ]);
            const clip = clipboard();
            emit({
                paths: clipboard().entries.map(e => e.path),
                toast: calls.toasts[0].text
            });
            """
        )
        # The folder's recursive copy already carries the file beneath it;
        # sending both would fail one of them on a revision check.
        self.assertEqual(result["paths"], ["src/lib", "src/a.js"])
        self.assertEqual(result["toast"], "Copied 2 items")


class BatchScopeTestCase(ExplorerControllerHarness):
    """A batch stops the moment its session or root revision moves."""

    def test_a_root_revision_change_mid_batch_abandons_the_rest(self):
        result = self._run_node(
            """
            responseQueue = [
                { ok: true, status: 200, data: { deleted_path: 'src/a.js' } },
                { ok: true, status: 200, data: { deleted_path: 'src/b.js' } }
            ];
            const original = sandbox.fetch;
            let served = 0;
            sandbox.fetch = async (url, options) => {
                served += 1;
                const response = await original(url, options);
                // The pane is re-rooted (a restart, or a restored session)
                // between the first and second request.
                if (served === 1) { pane._explorerRootRevision = 'root-2'; }
                return response;
            };
            sandbox.deleteExplorerFilesystemEntries(context(), [
                entry('src/a.js', 'file', 'r1'),
                entry('src/b.js', 'file', 'r2')
            ]).then(() => emit({
                requests: calls.requests.length,
                loads: calls.directoryLoads.length
            }));
            """
        )
        # The second entry is never sent against a root the user is no longer
        # looking at, and no refresh is forced onto the new root.
        self.assertEqual(result["requests"], 1)
        self.assertEqual(result["loads"], 0)


if __name__ == "__main__":
    unittest.main()
