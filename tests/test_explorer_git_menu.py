"""Behavioral coverage for the Git sidebar's commit-row context menu.

`explorer-git-menu.js` is DOM-free and require()-able, so the entries a commit
row offers and the exact text each one puts on the clipboard are executed in
Node rather than asserted as source text.

What matters about these entries is what they are *not*: a commit names a
repository object, not a path under the explorer root, so the menu issues no
request, never joins the multi-entry selection, and stays a pure read of data
the sidebar already fetched.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
GIT_MENU_JS = STATIC_JS / "explorer-git-menu.js"

NODE = shutil.which("node")

FULL_HASH = "6e01550a3f2b8c91d47e6f05b2c8a91d3e7f4c02"
SHORT_HASH = "6e01550"

# Every menu entry is exercised through the injected clipboard function, so a
# test observes the string a click would actually copy.
HARNESS_PREAMBLE = """
const copied = [];
const copy = value => { copied.push(value); return value; };
const build = commit => git_menu.commitMenuItems(commit, copy);
const clickAll = items => {
    items.filter(item => !item.disabled).forEach(item => item.action());
    return copied;
};
const describe = items => items.map(item => ({
    label: item.label,
    disabled: Boolean(item.disabled),
    title: item.title || ''
}));
"""


@unittest.skipUnless(NODE, "Node.js is required for commit menu tests")
class ExplorerGitMenuHarness(unittest.TestCase):
    def _run_node(self, body: str):
        harness = (
            f"const git_menu = require({json.dumps(str(GIT_MENU_JS))});\n"
            f"{HARNESS_PREAMBLE}\n"
            f"{body}\n"
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


class ExplorerCommitMenuTestCase(ExplorerGitMenuHarness):
    def test_commit_row_offers_hash_and_message_entries(self):
        items = self._run_node(
            "const items = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship the reveal handle'"
            "});"
            "console.log(JSON.stringify(describe(items)));"
        )

        self.assertEqual(
            [item["label"] for item in items],
            ["Copy commit hash", "Copy commit message"],
        )
        self.assertFalse(any(item["disabled"] for item in items))

    def test_copying_the_hash_yields_the_full_object_id(self):
        # The row displays the abbreviation; the clipboard gets the id you can
        # paste into a command or an issue without it ever going ambiguous.
        copied = self._run_node(
            "const items = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship it'"
            "});"
            "items[0].action();"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, [FULL_HASH])

    def test_hash_falls_back_to_the_abbreviation_when_no_full_id_travels(self):
        # A payload from before the backend carried %H still identifies the
        # commit, so the entry copies the short id rather than going dead.
        copied = self._run_node(
            "const items = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            "   message: 'Ship it'"
            "});"
            "items[0].action();"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, [SHORT_HASH])

    def test_a_non_hash_value_never_reaches_the_clipboard(self):
        # Whatever this row is, it is not a commit — copying the string would
        # hand over a value no git command accepts.
        items = self._run_node(
            "const items = build({ hash: 'not-a-hash', fullHash: 'zzzz', message: 'Ship it' });"
            "console.log(JSON.stringify(describe(items)));"
        )

        self.assertTrue(items[0]["disabled"])
        self.assertFalse(items[1]["disabled"])

    def test_copying_the_message_drops_the_ref_decoration(self):
        # `(HEAD -> main, tag: v1.2)` is how the row renders, never part of
        # what the author wrote.
        copied = self._run_node(
            "const items = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship the reveal handle',"
            "   subject: '(HEAD -> main, tag: v1.2) Ship the reveal handle'"
            "});"
            "items[1].action();"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, ["Ship the reveal handle"])

    def test_a_conventional_prefix_is_part_of_the_message(self):
        # This project's own subjects open with "(feat)", "(fix)", "(opt)".
        # Nothing may mistake one for a ref decoration and strip it.
        copied = self._run_node(
            "const items = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            "   message: '(opt) The handle sits centred',"
            "   subject: '(HEAD -> main) (opt) The handle sits centred'"
            "});"
            "items[1].action();"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, ["(opt) The handle sits centred"])

    def test_the_decorated_subject_is_never_the_message_source(self):
        # A row carrying only the rendered subject has no trustworthy message:
        # guessing which leading parenthesised group is a decoration would
        # copy the wrong text, so the entry goes disabled instead.
        items = self._run_node(
            "const items = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            "   subject: '(HEAD -> main) Ship the reveal handle'"
            "});"
            "console.log(JSON.stringify(describe(items)));"
        )

        self.assertTrue(items[1]["disabled"])

    def test_a_row_with_no_message_offers_the_entry_disabled(self):
        # A stable menu shape: the entry says the data is missing rather than
        # the menu quietly changing size between rows.
        items = self._run_node(
            f"const items = build({{ hash: {json.dumps(SHORT_HASH)} }});"
            "console.log(JSON.stringify(describe(items)));"
        )

        self.assertEqual(len(items), 2)
        self.assertFalse(items[0]["disabled"])
        self.assertTrue(items[1]["disabled"])

    def test_no_entry_copies_anything_until_it_is_clicked(self):
        copied = self._run_node(
            "build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship it'"
            "});"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, [])

    def test_every_enabled_entry_copies_exactly_one_string(self):
        # The menu is a pure read: two clicks, two clipboard writes, and no
        # request, confirmation, or refresh anywhere in between.
        copied = self._run_node(
            "const items = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship it'"
            "});"
            "console.log(JSON.stringify(clickAll(items)));"
        )

        self.assertEqual(copied, [FULL_HASH, "Ship it"])


if __name__ == "__main__":
    unittest.main()
