"""Behavioral coverage for what a commit row lets you copy.

`explorer-git-menu.js` is DOM-free and require()-able, so the affordances a
commit row offers and the exact text each one puts on the clipboard are
executed in Node rather than asserted as source text.

These two were a context menu of their own until the commit card absorbed them
(see `test_explorer_git_card.py` for the gesture and the surface). The module
stayed because *what* a commit copies is a decision, not a paint, and it has to
have exactly one answer: the card cannot be allowed to hand over a hash the
menu would have refused.

What matters about them is what they are *not*: a commit names a repository
object, not a path under the explorer root, so they issue no request, never
join the multi-entry selection, and stay a pure read of data the sidebar
already fetched.
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

# Every affordance is exercised through the injected clipboard function, so a
# test observes the string a click would actually copy.
HARNESS_PREAMBLE = """
const copied = [];
const copy = value => { copied.push(value); return value; };
const build = commit => git_menu.commitCopyActions(commit, copy);
const order = ['message', 'hash'];
const clickAll = actions => {
    order.map(key => actions[key])
        .filter(action => !action.disabled)
        .forEach(action => action.action());
    return copied;
};
const describe = actions => order.map(key => ({
    key: actions[key].key,
    label: actions[key].label,
    disabled: Boolean(actions[key].disabled),
    title: actions[key].title || ''
}));
"""


@unittest.skipUnless(NODE, "Node.js is required for commit copy tests")
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


class ExplorerCommitCopyTestCase(ExplorerGitMenuHarness):
    def test_a_commit_row_offers_a_hash_and_a_message_affordance(self):
        actions = self._run_node(
            "const actions = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship the reveal handle'"
            "});"
            "console.log(JSON.stringify(describe(actions)));"
        )

        # Keyed by the slot each one sits in, so the card's adapter looks them
        # up rather than matching on a label it also prints.
        self.assertEqual([item["key"] for item in actions], ["message", "hash"])
        self.assertEqual(
            [item["label"] for item in actions],
            ["Copy commit message", "Copy commit hash"],
        )
        self.assertFalse(any(item["disabled"] for item in actions))

    def test_copying_the_hash_yields_the_full_object_id(self):
        # The row displays the abbreviation; the clipboard gets the id you can
        # paste into a command or an issue without it ever going ambiguous.
        copied = self._run_node(
            "build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship it'"
            "}).hash.action();"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, [FULL_HASH])

    def test_hash_falls_back_to_the_abbreviation_when_no_full_id_travels(self):
        # A payload from before the backend carried %H still identifies the
        # commit, so the control copies the short id rather than going dead.
        copied = self._run_node(
            "build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            "   message: 'Ship it'"
            "}).hash.action();"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, [SHORT_HASH])

    def test_a_non_hash_value_never_reaches_the_clipboard(self):
        # Whatever this row is, it is not a commit — copying the string would
        # hand over a value no git command accepts.
        actions = self._run_node(
            "const actions = build({ hash: 'not-a-hash', fullHash: 'zzzz', message: 'Ship it' });"
            "console.log(JSON.stringify(describe(actions)));"
        )

        by_key = {item["key"]: item for item in actions}
        self.assertTrue(by_key["hash"]["disabled"])
        self.assertFalse(by_key["message"]["disabled"])

    def test_copying_the_message_drops_the_ref_decoration(self):
        # `(HEAD -> main, tag: v1.2)` is how the row renders, never part of
        # what the author wrote.
        copied = self._run_node(
            "build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship the reveal handle',"
            "   subject: '(HEAD -> main, tag: v1.2) Ship the reveal handle'"
            "}).message.action();"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, ["Ship the reveal handle"])

    def test_a_conventional_prefix_is_part_of_the_message(self):
        # This project's own subjects open with "(feat)", "(fix)", "(opt)".
        # Nothing may mistake one for a ref decoration and strip it.
        copied = self._run_node(
            "build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            "   message: '(opt) The handle sits centred',"
            "   subject: '(HEAD -> main) (opt) The handle sits centred'"
            "}).message.action();"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, ["(opt) The handle sits centred"])

    def test_the_decorated_subject_is_never_the_message_source(self):
        # A row carrying only the rendered subject has no trustworthy message:
        # guessing which leading parenthesised group is a decoration would
        # copy the wrong text, so the control goes disabled instead.
        actions = self._run_node(
            "const actions = build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            "   subject: '(HEAD -> main) Ship the reveal handle'"
            "});"
            "console.log(JSON.stringify(describe(actions)));"
        )

        by_key = {item["key"]: item for item in actions}
        self.assertTrue(by_key["message"]["disabled"])

    def test_a_row_with_no_message_offers_the_control_disabled(self):
        # A stable card shape: the control says the data is missing rather than
        # the card quietly changing size between rows.
        actions = self._run_node(
            f"const actions = build({{ hash: {json.dumps(SHORT_HASH)} }});"
            "console.log(JSON.stringify(describe(actions)));"
        )

        by_key = {item["key"]: item for item in actions}
        self.assertEqual(len(actions), 2)
        self.assertFalse(by_key["hash"]["disabled"])
        self.assertTrue(by_key["message"]["disabled"])
        self.assertIn("no commit message", by_key["message"]["title"])

    def test_nothing_copies_anything_until_it_is_clicked(self):
        copied = self._run_node(
            "build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship it'"
            "});"
            "console.log(JSON.stringify(copied));"
        )

        self.assertEqual(copied, [])

    def test_every_enabled_control_copies_exactly_one_string(self):
        # A pure read: two clicks, two clipboard writes, and no request,
        # confirmation, or refresh anywhere in between.
        copied = self._run_node(
            "console.log(JSON.stringify(clickAll(build({"
            f"  hash: {json.dumps(SHORT_HASH)},"
            f"  fullHash: {json.dumps(FULL_HASH)},"
            "   message: 'Ship it'"
            "}))));"
        )

        self.assertEqual(copied, ["Ship it", FULL_HASH])


if __name__ == "__main__":
    unittest.main()
