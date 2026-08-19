"""Behavioral coverage for the explorer's Phase-2 worker pipeline.

The worker's two CPU-only transforms and the page-side pool are DOM-free, so
these tests execute them in Node. They cover the contracts that would otherwise
be easy to regress while the UI still looked correct on small fixtures:

* the pool leaves one logical processor free, caps itself at four, stays lazy,
  and kills a running task when its pane supersedes it;
* Highlight.js runs against the real pinned build and crosses the worker
  boundary as typed arrays plus a class dictionary, then reconstructs the
  viewer's existing per-line run Map with exact source offsets;
* the large-Diff parser preserves FIFO delete/add pairing, context and hunk
  rows without touching the DOM.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-worker-core.js"
CLIENT_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-worker-client.js"
WORKER_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-worker.js"
DIFF_JS = REPO_ROOT / "web" / "static" / "js" / "explorer-diff.js"
HIGHLIGHT_JS = REPO_ROOT / "web" / "static" / "vendor" / "highlight.min.js"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is required for explorer worker tests")
class ExplorerWorkerTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        harness = (
            "const fs = require('fs');\n"
            "const vm = require('vm');\n"
            f"const core = require({json.dumps(str(CORE_JS))});\n"
            f"const client = require({json.dumps(str(CLIENT_JS))});\n"
            "const emit = value => console.log(JSON.stringify(value));\n"
            + body
        )
        with TemporaryDirectory() as temp_dir:
            script = Path(temp_dir) / "harness.js"
            script.write_text(harness, encoding="utf-8")
            result = subprocess.run(
                [NODE, str(script), str(HIGHLIGHT_JS)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_real_highlight_build_round_trips_compact_runs_and_offsets(self):
        result = self._run_node(
            "const sandbox = { console };\n"
            "sandbox.self = sandbox; sandbox.globalThis = sandbox;\n"
            "vm.createContext(sandbox);\n"
            "vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);\n"
            "const NL = String.fromCharCode(10);\n"
            "const source = ['/* first', ' * second */', 'const value = \"<&\";'].join(NL);\n"
            "const compact = core.highlightToCompact(source, 'javascript', sandbox.hljs);\n"
            "const lines = client.decodeHighlightResult(source, compact);\n"
            "emit({\n"
            "  constructors: [compact.starts, compact.lengths, compact.classIds, compact.lineRunStarts]\n"
            "    .map(value => value.constructor.name),\n"
            "  classes: compact.classes,\n"
            "  texts: Array.from(lines.values()).map(runs => runs.map(run => run.text).join('')),\n"
            "  starts: Array.from(lines.values()).map(runs => runs.map(run => run.start)),\n"
            "  styled: Array.from(lines.values()).map(runs => runs.map(run => run.className)),\n"
            "  keys: Object.keys(compact).sort()\n"
            "});\n"
        )

        self.assertEqual(
            result["constructors"],
            ["Uint32Array", "Uint32Array", "Uint16Array", "Uint32Array"],
        )
        self.assertEqual(result["texts"], ["/* first", " * second */", 'const value = "<&";'])
        self.assertEqual(result["starts"][0], [0])
        self.assertEqual(result["starts"][1], [9])
        self.assertTrue(any("comment" in name for name in result["styled"][0]))
        self.assertTrue(any("keyword" in name for name in result["styled"][2]))
        self.assertEqual(
            result["keys"],
            ["classIds", "classes", "lengths", "lineRunStarts", "starts"],
        )

    def test_the_decoded_map_materializes_one_line_at_a_time(self):
        """The freeze the worker removed must not reappear on the line after it.

        Building every run object and every substring for a whole document is
        one synchronous task proportional to the file, landing exactly where
        the Highlight.js pass used to. A line's runs are a bounded slice of the
        compact arrays, and the frame-sliced build asks for a few hundred lines
        per frame — so they are built when they are asked for, and only then.

        A bad shape still fails where the answer is accepted, not halfway
        through a paint: the structural check stays eager.
        """
        result = self._run_node(
            "const NL = String.fromCharCode(10);\n"
            "const source = Array.from({ length: 400 },"
            "  (_, i) => 'const v' + i + ' = 1;').join(NL);\n"
            "const starts = [], lengths = [], classIds = [], lineRunStarts = [0];\n"
            "let at = 0;\n"
            "source.split(NL).forEach(line => {\n"
            "  starts.push(at); lengths.push(line.length); classIds.push(0);\n"
            "  at += line.length + 1; lineRunStarts.push(starts.length);\n"
            "});\n"
            "const compact = {\n"
            "  classes: ['hljs-keyword'],\n"
            "  starts: Uint32Array.from(starts), lengths: Uint32Array.from(lengths),\n"
            "  classIds: Uint16Array.from(classIds),\n"
            "  lineRunStarts: Uint32Array.from(lineRunStarts)\n"
            "};\n"
            "const lines = client.decodeHighlightResult(source, compact);\n"
            "const untouched = lines.materialized;\n"
            "const first = lines.get(1).map(run => run.text);\n"
            "const afterOne = lines.materialized;\n"
            "const stable = lines.get(1) === lines.get(1);\n"
            "const afterRepeat = lines.materialized;\n"
            "let threw = '';\n"
            "try {\n"
            "  client.decodeHighlightResult(source, Object.assign({}, compact, {\n"
            "    lengths: Uint32Array.from(lengths.map(() => source.length + 5))\n"
            "  }));\n"
            "} catch (error) { threw = error.message; }\n"
            "emit({\n"
            "  size: lines.size, untouched, afterOne, afterRepeat, stable, first,\n"
            "  last: lines.get(400).map(run => run.text),\n"
            "  missing: lines.get(401) === undefined,\n"
            "  allLines: Array.from(lines.values()).length,\n"
            "  threw\n"
            "});\n"
        )

        self.assertEqual(result["size"], 400)
        # Decoding on its own materializes nothing.
        self.assertEqual(result["untouched"], 0)
        # One line asked for, one line built — and asking again reuses it
        # rather than rebuilding, so a repaint of the same rows is free.
        self.assertEqual(result["afterOne"], 1)
        self.assertEqual(result["afterRepeat"], 1)
        self.assertTrue(result["stable"])
        self.assertEqual(result["first"], ["const v0 = 1;"])
        self.assertEqual(result["last"], ["const v399 = 1;"])
        self.assertTrue(result["missing"])
        # Map's read surface is intact, so callers cannot tell the difference.
        self.assertEqual(result["allLines"], 400)
        # A run pointing past the buffer is refused at the boundary, not on the
        # frame that happens to paint that line.
        self.assertIn("invalid highlight run", result["threw"])

    def test_compact_markup_preserves_crlf_and_empty_lines(self):
        result = self._run_node(
            "const source = 'one' + String.fromCharCode(13, 10)"
            "  + String.fromCharCode(13, 10) + '<&>';\n"
            "const markup = '<span class=\"hljs-string\">one\\r\\n\\r\\n&lt;&amp;&gt;</span>';\n"
            "const compact = core.compactHighlightMarkup(markup, source.length);\n"
            "const lines = client.decodeHighlightResult(source, compact);\n"
            "emit(Array.from(lines.values()).map(runs => runs.map(run => ({\n"
            "  text: run.text, start: run.start, className: run.className\n"
            "}))));\n"
        )

        self.assertEqual(result[0], [{"text": "one", "start": 0, "className": "hljs-string"}])
        self.assertEqual(result[1], [])
        self.assertEqual(result[2], [{"text": "<&>", "start": 7, "className": "hljs-string"}])

    def test_large_diff_parser_preserves_pairing_and_hunks(self):
        rows = self._run_node(
            "const NL = String.fromCharCode(10);\n"
            "const diff = [\n"
            "  'diff --git a/a.txt b/a.txt',\n"
            "  '@@ -10,4 +10,4 @@ block',\n"
            "  '-old one', '-old two', '+new one',\n"
            "  ' context', '+inserted', ' context two'\n"
            "].join(NL);\n"
            "emit(core.parseSideBySideDiff(diff).rows);\n"
        )

        self.assertEqual(rows[0]["left"]["type"], "hunk")
        self.assertEqual(rows[1], {
            "left": {"type": "delete", "number": 10, "text": "old one"},
            "right": {"type": "add", "number": 10, "text": "new one"},
        })
        self.assertEqual(rows[2]["left"]["text"], "old two")
        self.assertIsNone(rows[2]["right"])
        self.assertEqual(rows[3]["left"]["type"], "context")
        self.assertIsNone(rows[4]["left"])
        self.assertEqual(rows[4]["right"]["text"], "inserted")

    def test_pool_is_lazy_bounded_and_terminates_a_superseded_job(self):
        result = self._run_node(
            "(async () => {\n"
            "  const made = [];\n"
            "  class FakeWorker {\n"
            "    constructor() { this.message = null; this.terminated = false; made.push(this); }\n"
            "    postMessage(message) { this.message = message; }\n"
            "    terminate() { this.terminated = true; }\n"
            "    finish(value) {\n"
            "      this.onmessage({ data: { id: this.message.id, ok: true, result: value } });\n"
            "    }\n"
            "  }\n"
            "  const pool = new client.WorkerPool({ size: 2, createWorker: () => new FakeWorker() });\n"
            "  const controller = new AbortController();\n"
            "  const first = pool.request('diff', { diff: 'first' }, { signal: controller.signal })\n"
            "    .then(() => 'resolved', error => error.name);\n"
            "  const second = pool.request('diff', { diff: 'second' });\n"
            "  const third = pool.request('diff', { diff: 'third' });\n"
            "  const beforeAbort = made.length;\n"
            "  controller.abort();\n"
            "  const afterAbort = made.length;\n"
            "  made[1].finish('second-result');\n"
            "  made[2].finish('third-result');\n"
            "  emit({\n"
            "    sizes: [1, 2, 4, 8].map(client.poolSizeFor),\n"
            "    beforeAbort, afterAbort, firstTerminated: made[0].terminated,\n"
            "    results: [await first, await second, await third]\n"
            "  });\n"
            "})();\n"
        )

        self.assertEqual(result["sizes"], [1, 1, 3, 4])
        self.assertEqual(result["beforeAbort"], 2)
        self.assertEqual(result["afterAbort"], 3)
        self.assertTrue(result["firstTerminated"])
        self.assertEqual(
            result["results"],
            ["AbortError", "second-result", "third-result"],
        )

    def test_worker_url_keeps_the_page_cachebuster(self):
        result = self._run_node(
            "emit(client.workerUrlFrom(\n"
            "  'http://127.0.0.1:5050/static/js/explorer-worker-client.js?v=9.4.1',\n"
            "  'http://127.0.0.1:5050/terminals'\n"
            "));\n"
        )

        self.assertEqual(
            result,
            "http://127.0.0.1:5050/static/js/explorer-worker.js?v=9.4.1",
        )

    def test_large_diff_paints_a_status_then_the_worker_model_with_undo(self):
        result = self._run_node(
            "(async () => {\n"
            "  let resolveModel; let parseCalls = 0; let undoWires = 0;\n"
            "  const code = {\n"
            "    raw: '', dataset: {},\n"
            "    classList: { toggle() {} },\n"
            "    querySelector: () => null, querySelectorAll: () => [],\n"
            "    addEventListener() {},\n"
            "    get innerHTML() { return this.raw; },\n"
            "    set innerHTML(value) { this.raw = value; }\n"
            "  };\n"
            "  const sandbox = {\n"
            "    console,\n"
            "    window: {\n"
            "      GridVibeExplorerWorkerCore: core,\n"
            "      GridVibeExplorerWorkers: {\n"
            "        available: () => true,\n"
            "        parseDiff() {\n"
            "          parseCalls += 1;\n"
            "          return new Promise(resolve => { resolveModel = resolve; });\n"
            "        }\n"
            "      }\n"
            "    },\n"
            "    document: { getElementById: id => id === 'explorer-diff-code-0' ? code : null },\n"
            "    terminals: [{ _explorerDiffContent: '@@ -1 +1 @@\\n-old\\n+new' }],\n"
            "    explorerTierPolicy: () => ({\n"
            "      diffTierForContent: () => 'large',\n"
            "      diffTierNotice: () => ({ title: 'Large diff', detail: 'Undo remains available.' })\n"
            "    }),\n"
            "    explorerLineWrapPreference: () => false,\n"
            "    explorerRequestSignal: () => undefined,\n"
            "    cancelExplorerRequestSlot() {},\n"
            "    explorerIsAbortError: error => error && error.name === 'AbortError',\n"
            "    normalizeExplorerLanguage: value => value || '',\n"
            "    explorerCodeLanguage: () => '',\n"
            "    highlightExplorerCode: value => String(value || ''),\n"
            "    escHtml: value => String(value == null ? '' : value)\n"
            "      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),\n"
            "    setTimeout, clearTimeout, URLSearchParams, fetch: () => {}\n"
            "  };\n"
            "  sandbox.globalThis = sandbox;\n"
            "  vm.createContext(sandbox);\n"
            "  vm.runInContext(fs.readFileSync("
            + json.dumps(str(DIFF_JS))
            + ", 'utf8'), sandbox);\n"
            "  sandbox.wireExplorerDiffUndoControls = () => { undoWires += 1; };\n"
            "  const first = sandbox.renderExplorerDiff(0);\n"
            "  const status = code.raw;\n"
            "  const second = sandbox.renderExplorerDiff(0);\n"
            "  resolveModel(core.parseSideBySideDiff(sandbox.terminals[0]._explorerDiffContent));\n"
            "  await Promise.all([first, second]);\n"
            "  emit({ status, final: code.raw, parseCalls, undoWires });\n"
            "})();\n"
        )

        self.assertIn("Large diff", result["status"])
        self.assertIn("Rendering large diff", result["status"])
        self.assertEqual(result["parseCalls"], 1)
        self.assertIn("explorer-diff-row", result["final"])
        self.assertIn("old", result["final"])
        self.assertIn("new", result["final"])
        self.assertEqual(result["undoWires"], 1)

    def test_worker_entry_imports_only_same_origin_vendored_assets(self):
        body = WORKER_JS.read_text(encoding="utf-8")
        self.assertIn("/static/vendor/highlight.min.js", body)
        self.assertIn("/static/js/explorer-worker-core.js", body)
        self.assertNotIn("https://", body)
        self.assertIn("highlightTransferList(result)", body)


if __name__ == "__main__":
    unittest.main()
