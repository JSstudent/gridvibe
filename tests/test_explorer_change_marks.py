"""The Source gutter's change marks read a zero-context diff.

The marks and the change peeks are made of a hunk's ``+``/``-`` lines and
nothing else: ``explorerDiffChangeBlocks()`` flushes a run either on a context
line or on a hunk header, and ``-U0`` turns every one of the former into one of
the latter. So the narrower read is not an approximation to be spot-checked —
it is supposed to be *mark-identical*, and that is what is executed here:
real Git output for the same worktree, both widths, through the real parser in
Node, compared block for block.

The second half is the reason the width is a server-side allowlist rather than
a number on the query string: the route names a width, it never accepts one.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
DIFF_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-diff.js"

NODE = shutil.which("node")
GIT = shutil.which("git")

HARNESS = """
const fs = require('fs');
const vm = require('vm');
const sandbox = { console };
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const diffs = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
console.log(JSON.stringify(diffs.map(diff => sandbox.explorerDiffChangeBlocks(diff))));
"""


@unittest.skipUnless(NODE and GIT, "Node.js and Git are required for change-mark tests")
class ExplorerChangeMarkContextTestCase(unittest.TestCase):
    """`-U0` and Git's default width describe the same set of marks."""

    def _git(self, repo: Path, *args: str) -> str:
        completed = subprocess.run(
            [GIT, *args],
            cwd=str(repo),
            capture_output=True,
            check=True,
            timeout=30,
        )
        return completed.stdout.decode("utf-8", errors="replace")

    def _change_blocks(self, diffs):
        """Run the page's own parser over each diff and return its blocks."""
        with TemporaryDirectory() as work_dir:
            script = Path(work_dir) / "harness.js"
            script.write_text(HARNESS, encoding="utf-8")
            payload = Path(work_dir) / "diffs.json"
            payload.write_text(json.dumps(diffs), encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script), str(DIFF_JS), str(payload)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def _repo_with_separated_changes(self, work_dir: Path) -> Path:
        """A worktree whose changes are far enough apart to sit in two hunks."""
        repo = work_dir / "repo"
        repo.mkdir()
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "gridvibe@example.invalid")
        self._git(repo, "config", "user.name", "GridVibe Test")
        committed = [f"line {number}" for number in range(1, 41)]
        target = repo / "notes.txt"
        target.write_text("\n".join(committed) + "\n", encoding="utf-8")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-m", "initial")

        worktree = committed[:]
        # One modification, one pure addition, one pure deletion — the three
        # block kinds the mark model distinguishes — spaced so Git's default
        # three lines of context never merges them into one hunk.
        worktree[2] = "line 3 edited"
        worktree[19:19] = ["inserted a", "inserted b"]
        del worktree[32]  # "line 31" — the two insertions above shifted it
        target.write_text("\n".join(worktree) + "\n", encoding="utf-8")
        return repo

    def test_zero_context_diff_yields_identical_change_blocks(self):
        with TemporaryDirectory() as temp_dir:
            repo = self._repo_with_separated_changes(Path(temp_dir))
            default_diff = self._git(repo, "diff", "HEAD", "--no-ext-diff", "--no-color", "--", "notes.txt")
            zero_diff = self._git(
                repo, "diff", "HEAD", "--unified=0", "--no-ext-diff", "--no-color", "--", "notes.txt"
            )

        # The premise: -U0 really did drop the context and really did keep
        # every changed line, so this is a narrower read and not another diff.
        self.assertIn("\n line 1", default_diff)
        self.assertNotIn("\n line 1", zero_diff)
        self.assertLess(len(zero_diff), len(default_diff))
        for changed in ("+line 3 edited", "-line 3", "+inserted a", "+inserted b", "-line 31"):
            self.assertIn(changed, zero_diff)

        default_blocks, zero_blocks = self._change_blocks([default_diff, zero_diff])

        # The claim itself: same blocks, same worktree line anchors, same
        # expected/replacement text — so the same gutter, peeks and ruler.
        self.assertEqual(default_blocks, zero_blocks)
        self.assertEqual(len(default_blocks), 3)
        self.assertEqual(
            [(block["expected"], block["replacement"]) for block in zero_blocks],
            [
                (["line 3 edited"], ["line 3"]),
                (["inserted a", "inserted b"], []),
                ([], ["line 31"]),
            ],
        )

    def test_zero_context_holds_for_an_untracked_style_whole_file_rewrite(self):
        """A file replaced end to end has one hunk either way — still identical."""
        with TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "gridvibe@example.invalid")
            self._git(repo, "config", "user.name", "GridVibe Test")
            target = repo / "notes.txt"
            target.write_text("old one\nold two\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "initial")
            target.write_text("new one\nnew two\nnew three\n", encoding="utf-8")
            default_diff = self._git(repo, "diff", "HEAD", "--no-ext-diff", "--no-color", "--", "notes.txt")
            zero_diff = self._git(
                repo, "diff", "HEAD", "--unified=0", "--no-ext-diff", "--no-color", "--", "notes.txt"
            )

        default_blocks, zero_blocks = self._change_blocks([default_diff, zero_diff])
        self.assertEqual(default_blocks, zero_blocks)
        self.assertEqual(len(zero_blocks), 1)
        self.assertEqual(zero_blocks[0]["line"], 1)


if __name__ == "__main__":
    unittest.main()
