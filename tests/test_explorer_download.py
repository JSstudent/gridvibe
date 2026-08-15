"""What a browser-mode explorer download is allowed to claim happened.

A programmatic ``<a download>`` click cannot observe the response, so every
outcome looked identical from the page: a stale tree row's ``404``, a ``403``,
and the server's 100 MB refusal all produced a green success toast and no file.
Multi-entry selection multiplied it — nine stale rows, nine green toasts,
nothing on disk.

These tests run the real ``explorer-viewer.js`` in Node against a stubbed page
and pin the narrower contract: the status is fetched before anything is
claimed; a failure reaches the error toast carrying the server's own reason; a
body too large to hold still streams through the anchor, but only after the
status is known; and a batch reports **once**, naming what landed.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
ICONS_JS = STATIC_JS / "terminal-icons.js"
# The batch path asks the real selection model whether N files need a
# confirmation, so its threshold is the one the tests are measured against.
SELECTION_JS = STATIC_JS / "explorer-selection.js"
VIEWER_JS = STATIC_JS / "explorer-viewer.js"
NODE = shutil.which("node")

HARNESS = """
const fs = require('fs');
const vm = require('vm');

const toasts = [];
const anchors = [];
const fetched = [];
const revoked = [];
// Deferred work is captured rather than run, so a 60 s object-URL release is
// observable without the harness waiting 60 s (or Node's loop staying alive
// for it). Each scenario flushes what it scheduled.
const timers = [];
const scheduleTimer = (fn, delay) => timers.push({ fn, delay });
const flushTimers = () => timers.splice(0).map(timer => { timer.fn(); return timer.delay; });
let aborted = 0;
let bodiesRead = 0;
let objectUrlSeq = 0;

// Canned responses, one consumed per request in order.
let responseQueue = [];

function fakeAnchor() {
    const link = {
        href: '',
        download: '',
        click() { anchors.push({ href: link.href, download: link.download }); },
        remove() {},
        setAttribute() {},
        addEventListener() {},
        classList: { add() {}, remove() {}, contains: () => false },
        dataset: {},
        style: {}
    };
    return link;
}

const sandbox = {
    console,
    JSON,
    Promise,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Math,
    Set,
    Map,
    Date,
    AbortController: class {
        constructor() { this.signal = { aborted: false }; }
        abort() { this.signal.aborted = true; aborted += 1; }
    },
    URL: {
        createObjectURL: () => `blob:gridvibe/${objectUrlSeq += 1}`,
        revokeObjectURL: (value) => { revoked.push(value); }
    },
    document: {
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
        createElement: () => fakeAnchor(),
        body: { dataset: {}, addEventListener() {}, appendChild() {} }
    },
    window: {
        addEventListener() {},
        setTimeout: scheduleTimer,
        clearTimeout() {},
        matchMedia: () => ({ matches: false }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        requestAnimationFrame: () => 0,
        pywebview: null
    },
    navigator: {},
    setTimeout: scheduleTimer,
    clearTimeout() {},
    requestAnimationFrame: () => 0,
    terminals: [{ _explorerMode: 'file', _explorerFilePath: 'src/open.txt', _explorerFileName: 'open.txt' }],
    sessionIds: ['s0'],
    escHtml: value => String(value == null ? '' : value),
    applyExplorerChangeMarks: () => {},
    updateExplorerFilesystemRootRevision: () => {},
    showTerminalToast: (text, kind) => { toasts.push({ text, kind }); },
    openGenericConfirmModal: async () => true,
    isPywebviewAvailable: () => Boolean(sandbox.window.pywebview),
    fetch: async (url) => {
        fetched.push(url);
        const canned = responseQueue.shift() || { ok: true, status: 200, length: '12' };
        return {
            ok: canned.ok,
            status: canned.status,
            headers: { get: name => (name === 'Content-Length' ? canned.length : null) },
            json: async () => {
                if (canned.errorBody === undefined) {
                    throw new Error('not json');
                }
                return canned.errorBody;
            },
            blob: async () => { bodiesRead += 1; return { size: Number(canned.length) || 0 }; }
        };
    }
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
[process.argv[2], process.argv[3], process.argv[4]].forEach(path => {
    vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox);
});

function reset(queue) {
    responseQueue = queue;
    toasts.length = 0;
    anchors.length = 0;
    fetched.length = 0;
    revoked.length = 0;
    timers.length = 0;
    aborted = 0;
    bodiesRead = 0;
    sandbox.window.pywebview = null;
}

const ok = (length) => ({ ok: true, status: 200, length: String(length) });
const gone = () => ({ ok: false, status: 404, errorBody: { error: 'File not found' } });
const rows = (count) => Array.from({ length: count }, (_, i) => ({ path: `src/f${i}.txt` }));
const snapshot = () => {
    // Nothing may be revoked before the click that consumes it, so the
    // deferred half is run only once the synchronous half has been recorded.
    const deferred = flushTimers();
    return {
        toasts: toasts.slice(),
        anchors: anchors.slice(),
        fetched: fetched.slice(),
        deferred,
        revoked: revoked.slice(),
        aborted,
        bodiesRead
    };
};

(async () => {
    const results = {};

    // 1. A file that is really there: fetched, buffered, handed to an anchor
    //    pointing at the blob rather than at the API, reported once.
    reset([ok(12)]);
    await sandbox.downloadExplorerFile(0, { path: 'src/a.txt' });
    results.served = snapshot();

    // 2. A stale row. The old anchor could not see this at all.
    reset([gone()]);
    results.missingReturn = await sandbox.downloadExplorerFile(0, { path: 'src/gone.txt' });
    results.missing = snapshot();

    // 3. A non-JSON error body still has to say something true.
    reset([{ ok: false, status: 403 }]);
    await sandbox.downloadExplorerFile(0, { path: 'src/denied.txt' });
    results.denied = snapshot();

    // 4. Too large to buffer: the status is known first, then the response is
    //    dropped unread and the browser streams the file itself.
    reset([ok(80 * 1024 * 1024)]);
    await sandbox.downloadExplorerFile(0, { path: 'src/huge.bin' });
    results.streamed = snapshot();

    // 5. The toolbar button downloads the open file with no options at all.
    reset([ok(12)]);
    await sandbox.downloadExplorerFile(0);
    results.toolbar = snapshot();

    // 6. Nine stale rows: one report, not nine successes.
    reset(rows(9).map(() => gone()));
    await sandbox.downloadExplorerFiles(0, rows(9));
    results.batchAllFailed = snapshot();

    // 7. A partly served batch names what landed.
    reset([ok(12), ok(12), gone(), ok(12), ok(12), ok(12), gone(), ok(12), ok(12)]);
    await sandbox.downloadExplorerFiles(0, rows(9));
    results.batchPartial = snapshot();

    // 8. A wholly served batch: still one toast.
    reset(rows(3).map(() => ok(12)));
    await sandbox.downloadExplorerFiles(0, rows(3));
    results.batchServed = snapshot();

    // 9. A batch of one has to read exactly like the single-entry action.
    reset([ok(12)]);
    await sandbox.downloadExplorerFiles(0, rows(1));
    results.batchOfOne = snapshot();

    // 10. The native window keeps its bridge, and a cancelled Save dialog is
    //     the user's answer rather than a failure to report.
    reset([]);
    sandbox.window.pywebview = { api: { save_download: async () => ({ cancelled: true }) } };
    await sandbox.downloadExplorerFile(0, { path: 'src/a.txt' });
    results.nativeCancelled = snapshot();

    reset([]);
    sandbox.window.pywebview = { api: { save_download: async () => ({ ok: false, error: 'Disk full' }) } };
    await sandbox.downloadExplorerFile(0, { path: 'src/a.txt' });
    results.nativeFailed = snapshot();

    process.stdout.write(JSON.stringify(results));
})();
"""


@unittest.skipUnless(NODE, "Node.js is required for explorer download tests")
class ExplorerDownloadTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(HARNESS, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(ICONS_JS), str(SELECTION_JS), str(VIEWER_JS)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            raise AssertionError(f"node harness failed:\n{completed.stderr}")
        cls.results = json.loads(completed.stdout)

    def test_a_served_file_is_fetched_before_anything_is_claimed(self):
        served = self.results["served"]
        self.assertEqual(served["fetched"], ["/api/explorer/s0/download?path=src%2Fa.txt"])
        self.assertEqual(served["bodiesRead"], 1)
        # The anchor points at the buffered body, not back at the API — the
        # transfer already happened, so the click cannot fail on its own.
        self.assertEqual(len(served["anchors"]), 1)
        self.assertTrue(served["anchors"][0]["href"].startswith("blob:"))
        self.assertEqual(served["anchors"][0]["download"], "a.txt")
        self.assertEqual(served["toasts"], [{"text": "Downloaded a.txt", "kind": "success"}])

    def test_a_stale_row_reports_the_failure_the_anchor_could_not_see(self):
        missing = self.results["missing"]
        self.assertEqual(len(missing["toasts"]), 1)
        self.assertEqual(missing["toasts"][0]["kind"], "error")
        # The server's own reason, not a generic one.
        self.assertIn("File not found", missing["toasts"][0]["text"])
        # Nothing was handed to the browser to "download".
        self.assertEqual(missing["anchors"], [])
        self.assertEqual(missing["bodiesRead"], 0)
        self.assertFalse(self.results["missingReturn"]["ok"])

    def test_an_error_without_a_json_body_still_names_the_status(self):
        denied = self.results["denied"]
        self.assertEqual(denied["toasts"][0]["kind"], "error")
        self.assertIn("403", denied["toasts"][0]["text"])
        self.assertEqual(denied["anchors"], [])

    def test_a_body_too_large_to_buffer_streams_but_only_after_the_status(self):
        streamed = self.results["streamed"]
        # The status was checked first…
        self.assertEqual(len(streamed["fetched"]), 1)
        # …then the response was dropped unread rather than held in memory.
        self.assertEqual(streamed["bodiesRead"], 0)
        self.assertEqual(streamed["aborted"], 1)
        # The anchor re-requests the API URL so the browser streams it to disk.
        self.assertEqual(len(streamed["anchors"]), 1)
        self.assertIn("/api/explorer/s0/download?path=", streamed["anchors"][0]["href"])
        self.assertEqual(streamed["toasts"][0]["kind"], "success")

    def test_the_toolbar_button_downloads_the_open_file(self):
        toolbar = self.results["toolbar"]
        self.assertEqual(toolbar["fetched"], ["/api/explorer/s0/download?path=src%2Fopen.txt"])
        self.assertEqual(toolbar["anchors"][0]["download"], "open.txt")

    def test_object_urls_are_released_after_the_click_that_consumes_them(self):
        # Revoking in the same task can race the browser's read of the URL, so
        # the release is deferred — but it must still happen, and it must
        # release the URL the click actually used.
        served = self.results["served"]
        self.assertEqual(served["deferred"], [60000])
        self.assertEqual(served["revoked"], [served["anchors"][0]["href"]])

    def test_a_streamed_download_leaks_no_object_url(self):
        # Nothing was buffered, so there is nothing to create or release.
        self.assertEqual(self.results["streamed"]["deferred"], [])
        self.assertEqual(self.results["streamed"]["revoked"], [])

    def test_nine_stale_rows_produce_one_error_not_nine_successes(self):
        batch = self.results["batchAllFailed"]
        self.assertEqual(len(batch["fetched"]), 9)
        self.assertEqual(len(batch["toasts"]), 1)
        self.assertEqual(batch["toasts"][0]["kind"], "error")
        self.assertIn("0 of 9", batch["toasts"][0]["text"])
        self.assertIn("File not found", batch["toasts"][0]["text"])
        self.assertEqual(batch["anchors"], [])

    def test_a_partly_served_batch_names_what_landed(self):
        batch = self.results["batchPartial"]
        self.assertEqual(len(batch["toasts"]), 1)
        self.assertEqual(batch["toasts"][0]["kind"], "error")
        self.assertIn("7 of 9", batch["toasts"][0]["text"])
        # The seven that were really served still reached the browser.
        self.assertEqual(len(batch["anchors"]), 7)

    def test_a_wholly_served_batch_reports_once(self):
        batch = self.results["batchServed"]
        self.assertEqual(len(batch["anchors"]), 3)
        self.assertEqual(batch["toasts"], [{"text": "Downloaded 3 files", "kind": "success"}])

    def test_a_batch_of_one_reads_like_the_single_entry_action(self):
        self.assertEqual(
            self.results["batchOfOne"]["toasts"],
            [{"text": "Downloaded f0.txt", "kind": "success"}],
        )

    def test_the_native_window_still_routes_through_its_bridge(self):
        # WebView2 drops anchor downloads; the bridge path must not have been
        # replaced by the fetch one.
        cancelled = self.results["nativeCancelled"]
        self.assertEqual(cancelled["fetched"], [])
        self.assertEqual(cancelled["anchors"], [])
        # A cancelled Save dialog is an answer, not a failure.
        self.assertEqual(cancelled["toasts"], [])

        failed = self.results["nativeFailed"]
        self.assertEqual(failed["toasts"][0]["kind"], "error")
        self.assertIn("Disk full", failed["toasts"][0]["text"])


if __name__ == "__main__":
    unittest.main()
