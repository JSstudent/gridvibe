"""Behavioral coverage for the explorer's multi-entry selection model.

`explorer-selection.js` is DOM-free and require()-able, so the gesture rules,
the scope bindings that drop a stale selection, and the derived confirm copy are
executed in Node rather than asserted as source text.

The one-entry cases matter as much as the many-entry ones: a batch of one is the
path every pre-existing single-entry action now takes, so its wording and its
targets must come out identical to what the single action produced before.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
SELECTION_JS = STATIC_JS / "explorer-selection.js"

NODE = shutil.which("node")

# A pane's worth of rows in render order, as the DOM adapter reports them.
ROWS = """
const rows = [
    { path: 'src/a.js', kind: 'file', revision: 'r1' },
    { path: 'src/b.js', kind: 'file', revision: 'r2' },
    { path: 'src/c.js', kind: 'file', revision: 'r3' },
    { path: 'src/lib', kind: 'directory', revision: 'r4' },
    { path: 'src/d.js', kind: 'file', revision: 'r5' }
];
const scope = { sessionId: 's1', rootRevision: 'root-1', surface: 'preview' };
const click = (selection, path, modifiers = {}) => selection_model.applyPointerSelection(
    selection,
    Object.assign({}, scope, { entry: rows.find(row => row.path === path) }, modifiers),
    rows
);
"""


@unittest.skipUnless(NODE, "Node.js is required for client selection tests")
class ExplorerSelectionHarness(unittest.TestCase):
    def _run_node(self, body: str):
        harness = (
            "const selection_model = require(process.argv[2]);\n" + ROWS + body
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(SELECTION_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class PointerGestureTestCase(ExplorerSelectionHarness):
    """Ctrl toggles, shift ranges, and a plain click keeps the old behavior."""

    def test_plain_click_selects_nothing_and_still_opens_the_row(self):
        result = self._run_node(
            """
            const first = click(null, 'src/a.js');
            const withSelection = click(null, 'src/a.js', { ctrlKey: true }).selection;
            const after = click(withSelection, 'src/c.js');
            process.stdout.write(JSON.stringify({
                firstActivates: first.activate,
                firstSelection: first.selection,
                afterActivates: after.activate,
                afterSelection: after.selection
            }));
            """
        )
        # An explorer with nothing selected must behave exactly as it did
        # before multi-select existed: click opens, and selects nothing.
        self.assertTrue(result["firstActivates"])
        self.assertIsNone(result["firstSelection"])
        # A plain click also clears an existing selection rather than adding.
        self.assertTrue(result["afterActivates"])
        self.assertIsNone(result["afterSelection"])

    def test_ctrl_click_toggles_without_opening(self):
        result = self._run_node(
            """
            let selection = click(null, 'src/a.js', { ctrlKey: true }).selection;
            selection = click(selection, 'src/c.js', { ctrlKey: true }).selection;
            const paths = selection_model.selectionPaths(selection);
            const removed = click(selection, 'src/a.js', { ctrlKey: true });
            const emptied = click(removed.selection, 'src/c.js', { ctrlKey: true });
            process.stdout.write(JSON.stringify({
                paths,
                activates: click(selection, 'src/b.js', { ctrlKey: true }).activate,
                afterRemove: selection_model.selectionPaths(removed.selection),
                emptied: emptied.selection
            }));
            """
        )
        self.assertEqual(result["paths"], ["src/a.js", "src/c.js"])
        self.assertFalse(result["activates"])
        self.assertEqual(result["afterRemove"], ["src/c.js"])
        # Deselecting the last entry collapses to one canonical "nothing".
        self.assertIsNone(result["emptied"])

    def test_shift_click_selects_the_range_in_render_order(self):
        result = self._run_node(
            """
            const anchored = click(null, 'src/b.js', { ctrlKey: true }).selection;
            const ranged = click(anchored, 'src/d.js', { shiftKey: true }).selection;
            const back = click(ranged, 'src/a.js', { shiftKey: true }).selection;
            process.stdout.write(JSON.stringify({
                forward: selection_model.selectionPaths(ranged),
                backward: selection_model.selectionPaths(back),
                anchor: back.anchorPath
            }));
            """
        )
        self.assertEqual(
            result["forward"], ["src/b.js", "src/c.js", "src/lib", "src/d.js"]
        )
        # Re-ranging works from the same anchor in both directions instead of
        # walking the selection along with each shift+click.
        self.assertEqual(result["backward"], ["src/a.js", "src/b.js"])
        self.assertEqual(result["anchor"], "src/b.js")

    def test_shift_click_without_an_anchor_degrades_to_a_toggle(self):
        result = self._run_node(
            """
            const first = click(null, 'src/c.js', { shiftKey: true });
            process.stdout.write(JSON.stringify({
                paths: selection_model.selectionPaths(first.selection),
                activates: first.activate
            }));
            """
        )
        self.assertEqual(result["paths"], ["src/c.js"])
        self.assertFalse(result["activates"])

    def test_a_row_without_a_revision_is_not_selectable(self):
        result = self._run_node(
            """
            const deleted = { path: 'src/gone.js', kind: 'file', revision: '' };
            const plain = selection_model.applyPointerSelection(
                null, Object.assign({}, scope, { entry: deleted }), rows
            );
            const held = click(null, 'src/a.js', { ctrlKey: true }).selection;
            const ctrl = selection_model.applyPointerSelection(
                null,
                Object.assign({}, scope, { entry: deleted, ctrlKey: true }),
                rows
            );
            process.stdout.write(JSON.stringify({
                plainActivates: plain.activate,
                plainSelection: plain.selection,
                ctrlActivates: ctrl.activate,
                ctrlSelection: ctrl.selection,
                heldPaths: selection_model.selectionPaths(held)
            }));
            """
        )
        # It can still be clicked open, but it can never enter a selection that
        # would later send an empty revision to a mutation endpoint.
        self.assertTrue(result["plainActivates"])
        self.assertIsNone(result["plainSelection"])
        self.assertFalse(result["ctrlActivates"])
        self.assertIsNone(result["ctrlSelection"])
        self.assertEqual(result["heldPaths"], ["src/a.js"])


class SelectionScopeTestCase(ExplorerSelectionHarness):
    """Session, root revision, and surface each drop a stale selection."""

    def test_a_changed_root_revision_drops_the_selection(self):
        result = self._run_node(
            """
            const selection = click(null, 'src/a.js', { ctrlKey: true }).selection;
            process.stdout.write(JSON.stringify({
                same: Boolean(selection_model.scopedSelection(selection, scope)),
                revision: Boolean(selection_model.scopedSelection(
                    selection, Object.assign({}, scope, { rootRevision: 'root-2' })
                )),
                session: Boolean(selection_model.scopedSelection(
                    selection, Object.assign({}, scope, { sessionId: 's2' })
                ))
            }));
            """
        )
        self.assertTrue(result["same"])
        self.assertFalse(result["revision"])
        self.assertFalse(result["session"])

    def test_selection_never_spans_the_tree_and_preview_surfaces(self):
        result = self._run_node(
            """
            const inPreview = click(null, 'src/a.js', { ctrlKey: true }).selection;
            const treeScope = Object.assign({}, scope, { surface: 'tree' });
            const inTree = selection_model.applyPointerSelection(
                inPreview,
                Object.assign({}, treeScope, {
                    entry: rows.find(row => row.path === 'src/c.js'), ctrlKey: true
                }),
                rows
            );
            process.stdout.write(JSON.stringify({
                previewPaths: selection_model.selectionPaths(inPreview),
                treePaths: selection_model.selectionPaths(inTree.selection),
                treeSurface: inTree.selection.surface
            }));
            """
        )
        # Ctrl+clicking in the tree starts a fresh tree selection instead of
        # extending the preview one — a mixed selection has no single
        # well-defined paste destination.
        self.assertEqual(result["previewPaths"], ["src/a.js"])
        self.assertEqual(result["treePaths"], ["src/c.js"])
        self.assertEqual(result["treeSurface"], "tree")


class ContextTargetTestCase(ExplorerSelectionHarness):
    """Right-clicking inside the selection keeps it; outside collapses it."""

    def test_right_click_inside_the_selection_targets_all_of_it(self):
        result = self._run_node(
            """
            let selection = click(null, 'src/a.js', { ctrlKey: true }).selection;
            selection = click(selection, 'src/c.js', { ctrlKey: true }).selection;
            const resolved = selection_model.resolveContextTargets(
                selection,
                Object.assign({}, scope, { entry: rows.find(r => r.path === 'src/c.js') })
            );
            process.stdout.write(JSON.stringify({
                targets: resolved.targets.map(entry => entry.path),
                kept: selection_model.selectionPaths(resolved.selection)
            }));
            """
        )
        self.assertEqual(result["targets"], ["src/a.js", "src/c.js"])
        self.assertEqual(result["kept"], ["src/a.js", "src/c.js"])

    def test_right_click_outside_the_selection_collapses_to_that_row(self):
        result = self._run_node(
            """
            let selection = click(null, 'src/a.js', { ctrlKey: true }).selection;
            selection = click(selection, 'src/c.js', { ctrlKey: true }).selection;
            const resolved = selection_model.resolveContextTargets(
                selection,
                Object.assign({}, scope, { entry: rows.find(r => r.path === 'src/d.js') })
            );
            const blank = selection_model.resolveContextTargets(
                selection, Object.assign({}, scope, { entry: null })
            );
            process.stdout.write(JSON.stringify({
                targets: resolved.targets.map(entry => entry.path),
                selection: resolved.selection,
                blankTargets: blank.targets,
                blankSelection: blank.selection
            }));
            """
        )
        # A forgotten selection elsewhere must never be swept into an action the
        # user aimed at a single row.
        self.assertEqual(result["targets"], ["src/d.js"])
        self.assertIsNone(result["selection"])
        self.assertEqual(result["blankTargets"], [])
        self.assertIsNone(result["blankSelection"])

    def test_a_stale_scope_targets_the_clicked_row_alone(self):
        result = self._run_node(
            """
            let selection = click(null, 'src/a.js', { ctrlKey: true }).selection;
            selection = click(selection, 'src/c.js', { ctrlKey: true }).selection;
            const resolved = selection_model.resolveContextTargets(
                selection,
                Object.assign({}, scope, {
                    rootRevision: 'root-2',
                    entry: rows.find(r => r.path === 'src/c.js')
                })
            );
            process.stdout.write(JSON.stringify({
                targets: resolved.targets.map(entry => entry.path)
            }));
            """
        )
        self.assertEqual(result["targets"], ["src/c.js"])


class SelectionUpkeepTestCase(ExplorerSelectionHarness):
    """A completed mutation drops the paths it removed."""

    def test_removed_paths_and_their_descendants_leave_the_selection(self):
        result = self._run_node(
            """
            const nested = [
                { path: 'src/lib', kind: 'directory', revision: 'r4' },
                { path: 'src/lib/deep.js', kind: 'file', revision: 'r6' },
                { path: 'src/a.js', kind: 'file', revision: 'r1' }
            ];
            let selection = null;
            nested.forEach(entry => {
                selection = selection_model.applyPointerSelection(
                    selection, Object.assign({}, scope, { entry, ctrlKey: true }), nested
                ).selection;
            });
            const afterDelete = selection_model.dropPaths(selection, ['src/lib']);
            const emptied = selection_model.dropPaths(afterDelete, ['src/a.js']);
            process.stdout.write(JSON.stringify({
                remaining: selection_model.selectionPaths(afterDelete),
                anchor: afterDelete.anchorPath,
                emptied
            }));
            """
        )
        # Deleting the folder takes the file beneath it out of the selection too.
        self.assertEqual(result["remaining"], ["src/a.js"])
        self.assertEqual(result["anchor"], "src/a.js")
        self.assertIsNone(result["emptied"])

    def test_a_selected_descendant_is_covered_by_its_selected_ancestor(self):
        result = self._run_node(
            """
            const targets = [
                { path: 'src/lib', kind: 'directory', revision: 'r4' },
                { path: 'src/lib/deep.js', kind: 'file', revision: 'r6' },
                { path: 'src/lib/nested/x.js', kind: 'file', revision: 'r7' },
                { path: 'src/a.js', kind: 'file', revision: 'r1' },
                { path: 'src/library.js', kind: 'file', revision: 'r8' }
            ];
            process.stdout.write(JSON.stringify({
                topmost: selection_model.topmostTargets(targets).map(e => e.path),
                unrelated: selection_model.topmostTargets(
                    [targets[3], targets[4]]
                ).map(e => e.path)
            }));
            """
        )
        # Running a batch as N atomic requests means the ancestor's recursive
        # delete/copy/move already covers the descendant; sending both would
        # fail one of them on a revision or existence check for work that did
        # in fact happen.
        self.assertEqual(result["topmost"], ["src/lib", "src/a.js", "src/library.js"])
        # A shared name prefix is not an ancestor — "src/lib" must not swallow
        # the sibling "src/library.js".
        self.assertEqual(result["unrelated"], ["src/a.js", "src/library.js"])

    def test_the_selection_is_bounded(self):
        result = self._run_node(
            """
            const many = [];
            for (let i = 0; i < selection_model.MAX_SELECTED_ENTRIES + 25; i += 1) {
                many.push({ path: `f/${i}.txt`, kind: 'file', revision: `r${i}` });
            }
            let selection = null;
            many.forEach(entry => {
                selection = selection_model.applyPointerSelection(
                    selection, Object.assign({}, scope, { entry, ctrlKey: true }), many
                ).selection;
            });
            process.stdout.write(JSON.stringify({
                size: selection_model.selectionSize(selection),
                max: selection_model.MAX_SELECTED_ENTRIES
            }));
            """
        )
        self.assertEqual(result["size"], result["max"])


class DerivedCopyTestCase(ExplorerSelectionHarness):
    """A batch of one must read exactly like the single action it replaced."""

    def test_single_entry_delete_copy_is_unchanged(self):
        result = self._run_node(
            """
            process.stdout.write(JSON.stringify({
                file: selection_model.deleteConfirmCopy(
                    [{ path: 'src/a.js', kind: 'file', revision: 'r1' }]
                ),
                directory: selection_model.deleteConfirmCopy(
                    [{ path: 'src/lib', kind: 'directory', revision: 'r4' }]
                ),
                empty: selection_model.deleteConfirmCopy([])
            }));
            """
        )
        self.assertEqual(
            result["file"],
            {"title": 'Permanently delete "a.js"?', "confirmLabel": "Delete"},
        )
        self.assertEqual(
            result["directory"],
            {
                "title": 'Permanently delete the folder "lib" and all of its contents?',
                "confirmLabel": "Delete",
            },
        )
        self.assertIsNone(result["empty"])

    def test_multi_entry_delete_copy_names_the_count_and_kinds(self):
        result = self._run_node(
            """
            const files = [
                { path: 'a.js', kind: 'file', revision: 'r1' },
                { path: 'b.js', kind: 'file', revision: 'r2' }
            ];
            const mixed = files.concat([{ path: 'lib', kind: 'directory', revision: 'r3' }]);
            process.stdout.write(JSON.stringify({
                files: selection_model.deleteConfirmCopy(files),
                mixed: selection_model.deleteConfirmCopy(mixed),
                filesLabel: selection_model.targetsLabel(files),
                mixedLabel: selection_model.targetsLabel(mixed),
                oneLabel: selection_model.targetsLabel(files.slice(0, 1))
            }));
            """
        )
        self.assertEqual(result["files"]["title"], "Permanently delete 2 files?")
        self.assertEqual(result["files"]["confirmLabel"], "Delete 2")
        # A mixed batch warns about recursive folder contents, like the single
        # folder wording does.
        self.assertIn("including every folder's contents", result["mixed"]["title"])
        self.assertEqual(result["filesLabel"], "2 files")
        self.assertEqual(result["mixedLabel"], "3 items")
        self.assertEqual(result["oneLabel"], '"a.js"')

    def test_bulk_download_only_confirms_past_the_threshold(self):
        result = self._run_node(
            """
            const make = count => Array.from({ length: count }, (_, i) => (
                { path: `f${i}.txt`, kind: 'file', revision: `r${i}` }
            ));
            const threshold = selection_model.DOWNLOAD_CONFIRM_THRESHOLD;
            process.stdout.write(JSON.stringify({
                at: selection_model.downloadConfirmCopy(make(threshold)),
                over: selection_model.downloadConfirmCopy(make(threshold + 1))
            }));
            """
        )
        self.assertIsNone(result["at"])
        self.assertIsNotNone(result["over"])


class BatchOutcomeTestCase(ExplorerSelectionHarness):
    """Partial failure is the normal case for an N-request batch."""

    def test_a_fully_successful_batch_reports_a_toast_only(self):
        result = self._run_node(
            """
            process.stdout.write(JSON.stringify(selection_model.batchOutcome(
                'Deleted', [{ ok: true }, { ok: true }, { ok: true }]
            )));
            """
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["toast"], "Deleted 3")
        self.assertEqual(result["message"], "")

    def test_a_partial_failure_names_the_counts_and_the_first_reason(self):
        result = self._run_node(
            """
            process.stdout.write(JSON.stringify(selection_model.batchOutcome('Deleted', [
                { ok: true },
                { ok: false, error: 'Entry changed on disk.', mutated: false },
                { ok: true },
                { ok: false, error: 'Permission denied.', mutated: false }
            ])));
            """
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["message"],
            "Deleted 2 of 4; 2 failed. Entry changed on disk.",
        )

    def test_retry_is_offered_only_when_no_failure_touched_the_filesystem(self):
        result = self._run_node(
            """
            const clean = [{ ok: false, error: 'x', mutated: false }];
            const dirty = [
                { ok: false, error: 'x', mutated: false },
                { ok: false, error: 'y', mutated: true }
            ];
            process.stdout.write(JSON.stringify({
                clean: selection_model.batchOutcome('Deleted', clean).retryable,
                dirty: selection_model.batchOutcome('Deleted', dirty).retryable
            }));
            """
        )
        # A partially applied batch has to be re-inspected, never replayed.
        self.assertTrue(result["clean"])
        self.assertFalse(result["dirty"])

    def test_a_lone_failure_reports_its_own_error_verbatim(self):
        result = self._run_node(
            """
            process.stdout.write(JSON.stringify(selection_model.batchOutcome(
                'Deleted', [{ ok: false, error: 'Entry changed on disk.', mutated: false }]
            )));
            """
        )
        # The N=1 path must not gain batch phrasing around a single error.
        self.assertEqual(result["message"], "Entry changed on disk.")


if __name__ == "__main__":
    unittest.main()
