"""Behavioral coverage for the launcher's one global notification banner.

Both halves run in Node against the real module — `notice-banner.js` is
require()-able and takes its DOM and timers from an injected runtime — so the
replacement, persistence, and timer rules are executed rather than asserted as
source text. The last case slices the real `checkForUpdates` out of
`launcher.js` and runs it, which is what pins the reported bug: a failed update
used to report itself twice, once to each of the page's two message sinks.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
NOTICE_JS = STATIC_JS / "notice-banner.js"
LAUNCHER_JS = STATIC_JS / "launcher.js"

NODE = shutil.which("node")

# A hand-stubbed banner element and a controllable clock: the adapter only ever
# flips attributes and classes and asks the runtime for elements and timers, so
# this is the whole DOM surface it can reach.
HARNESS_STUBS = """
function stubElement() {
    const classes = new Set();
    const attributes = new Map();
    return {
        classes,
        attributes,
        textContent: '',
        innerHTML: null,
        classList: {
            toggle: (name, force) => {
                if (force) { classes.add(name); } else { classes.delete(name); }
            },
            contains: name => classes.has(name)
        },
        setAttribute: (name, value) => { attributes.set(name, String(value)); },
        removeAttribute: name => { attributes.delete(name); },
        getAttribute: name => (attributes.has(name) ? attributes.get(name) : null)
    };
}

function fakeClock() {
    const pending = new Map();
    let sequence = 0;
    return {
        pending,
        setTimeout: (fn, ms) => {
            sequence += 1;
            pending.set(sequence, { fn, ms });
            return sequence;
        },
        clearTimeout: handle => { pending.delete(handle); },
        fire: () => {
            const due = [...pending.values()];
            pending.clear();
            due.forEach(entry => entry.fn());
        }
    };
}

function harness(notice) {
    const elements = new Map();
    [notice.ELEMENT_ID, notice.TEXT_ID].forEach(id => elements.set(id, stubElement()));
    notice.policy.TYPES.forEach(
        type => elements.set(notice.iconElementId(type), stubElement())
    );
    const clock = fakeClock();
    const errors = [];
    const banner = notice.create({
        getElement: id => elements.get(id) || null,
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout,
        logError: text => errors.push(text)
    });
    return {
        banner,
        clock,
        errors,
        root: elements.get(notice.ELEMENT_ID),
        text: elements.get(notice.TEXT_ID),
        icon: type => elements.get(notice.iconElementId(type)),
        visible: () => elements.get(notice.ELEMENT_ID).getAttribute('hidden') === null
    };
}
"""


@unittest.skipUnless(NODE, "Node.js is required for notice banner tests")
class NoticeBannerNodeTestCase(unittest.TestCase):
    def _run_node(self, body: str, module_path: Path = NOTICE_JS):
        script = HARNESS_STUBS + "\nconst notice = require(process.argv[2]);\n" + body
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(module_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class NoticePolicyTestCase(NoticeBannerNodeTestCase):
    """The pure half — no DOM, no timers, no globals."""

    def test_unknown_severities_resolve_to_info_instead_of_throwing(self):
        result = self._run_node(
            """
            const { normalizeType } = notice.policy;
            process.stdout.write(JSON.stringify({
                empty: normalizeType(''),
                missing: normalizeType(undefined),
                nulled: normalizeType(null),
                garbage: normalizeType('catastrophe'),
                numeric: normalizeType(7),
                padded: normalizeType('  ERROR  '),
                known: notice.policy.TYPES.map(normalizeType)
            }));
            """
        )
        # Call sites pass '' for "no particular severity", and a couple pass a
        # value straight through from a server payload.
        self.assertEqual(result["empty"], "info")
        self.assertEqual(result["missing"], "info")
        self.assertEqual(result["nulled"], "info")
        self.assertEqual(result["garbage"], "info")
        self.assertEqual(result["numeric"], "info")
        self.assertEqual(result["padded"], "error")
        self.assertEqual(result["known"], ["error", "warning", "success", "info"])

    def test_failures_persist_and_confirmations_expire(self):
        result = self._run_node(
            """
            const { isPersistent, autoDismissMs } = notice.policy;
            process.stdout.write(JSON.stringify({
                persistent: ['error', 'warning', 'success', 'info'].map(isPersistent),
                delays: ['error', 'warning', 'success', 'info'].map(autoDismissMs)
            }));
            """
        )
        # D4: a failure must never disappear before it is read; a confirmation
        # should not need dismissing.
        self.assertEqual(result["persistent"], [True, True, False, False])
        self.assertEqual(result["delays"], [0, 0, 6000, 6000])

    def test_only_errors_are_echoed_to_the_console(self):
        result = self._run_node(
            """
            process.stdout.write(JSON.stringify(
                ['error', 'warning', 'success', 'info', ''].map(
                    notice.policy.shouldEchoToConsole
                )
            ));
            """
        )
        self.assertEqual(result, [True, False, False, False, False])

    def test_text_is_collapsed_and_capped_without_splitting_a_pair(self):
        result = self._run_node(
            """
            const { normalizeText, MAX_TEXT_LENGTH } = notice.policy;
            // A surrogate pair straddling the cap must not be halved.
            const straddling = 'a'.repeat(MAX_TEXT_LENGTH - 2) + '\\u{1F600}'.repeat(4);
            const capped = normalizeText(straddling);
            process.stdout.write(JSON.stringify({
                collapsed: normalizeText('  Update   failed.\\n  Retry later.  '),
                empty: normalizeText('   '),
                nulled: normalizeText(null),
                cappedLength: capped.length,
                lastCode: capped.charCodeAt(capped.length - 2),
                lonelyHighSurrogate: /[\\uD800-\\uDBFF](?![\\uDC00-\\uDFFF])/.test(capped)
            }));
            """
        )
        self.assertEqual(result["collapsed"], "Update failed. Retry later.")
        self.assertEqual(result["empty"], "")
        self.assertEqual(result["nulled"], "")
        self.assertLessEqual(result["cappedLength"], 600)
        self.assertFalse(result["lonelyHighSurrogate"])

    def test_next_state_replaces_and_never_accumulates(self):
        result = self._run_node(
            """
            const { nextState } = notice.policy;
            let slot = null;
            const revisions = [];
            ['first', 'second', 'third'].forEach(text => {
                slot = nextState(slot, { text, type: 'success' });
                revisions.push(slot.revision);
            });
            const afterError = nextState(slot, { text: 'boom', type: 'error' });
            const thenSuccess = nextState(afterError, { text: 'fine', type: 'success' });
            process.stdout.write(JSON.stringify({
                isArray: Array.isArray(slot),
                keptText: slot.text,
                revisions,
                emptyClears: nextState(slot, { text: '   ', type: 'error' }),
                errorWins: thenSuccess.type,
                errorWinsText: thenSuccess.text
            }));
            """
        )
        # D3: three notices leave exactly one slot, holding the newest.
        self.assertFalse(result["isArray"])
        self.assertEqual(result["keptText"], "third")
        self.assertEqual(result["revisions"], [1, 2, 3])
        self.assertIsNone(result["emptyClears"])
        # An error followed by a success leaves the success — nothing "wins" by
        # severity, or a stale failure would outlive the thing that fixed it.
        self.assertEqual(result["errorWins"], "success")
        self.assertEqual(result["errorWinsText"], "fine")


class NoticeAdapterTestCase(NoticeBannerNodeTestCase):
    """The DOM half, driven through the injected runtime."""

    def test_a_notice_with_no_banner_in_the_page_still_echoes(self):
        result = self._run_node(
            """
            const errors = [];
            const banner = notice.create({
                getElement: () => null,
                setTimeout: () => { throw new Error('must not schedule'); },
                clearTimeout: () => {},
                logError: text => errors.push(text)
            });
            const state = banner.show('Launch failed.', 'error');
            process.stdout.write(JSON.stringify({ errors, type: state.type }));
            """
        )
        # A notice fired before the markup is parsed is a no-op, not a throw.
        self.assertEqual(result["errors"], ["Launch failed."])
        self.assertEqual(result["type"], "error")

    def test_showing_paints_severity_and_swaps_exactly_one_icon(self):
        result = self._run_node(
            """
            const page = harness(notice);
            page.banner.show('Saved the workspace.', 'success');
            process.stdout.write(JSON.stringify({
                visible: page.visible(),
                text: page.text.textContent,
                innerHtmlUntouched: page.text.innerHTML === null,
                classes: [...page.root.classes].sort(),
                shownIcons: notice.policy.TYPES.filter(
                    type => page.icon(type).getAttribute('hidden') === null
                ),
                live: page.root.getAttribute('aria-live')
            }));
            """
        )
        self.assertTrue(result["visible"])
        self.assertEqual(result["text"], "Saved the workspace.")
        # textContent, never innerHTML — a server error may contain markup.
        self.assertTrue(result["innerHtmlUntouched"])
        self.assertEqual(result["classes"], ["is-success"])
        self.assertEqual(result["shownIcons"], ["success"])
        self.assertEqual(result["live"], "polite")

    def test_markup_in_a_server_error_stays_inert_text(self):
        result = self._run_node(
            """
            const page = harness(notice);
            page.banner.show('<img src=x onerror=alert(1)> failed', 'error');
            process.stdout.write(JSON.stringify({
                text: page.text.textContent,
                innerHtmlUntouched: page.text.innerHTML === null,
                live: page.root.getAttribute('aria-live')
            }));
            """
        )
        self.assertEqual(result["text"], "<img src=x onerror=alert(1)> failed")
        self.assertTrue(result["innerHtmlUntouched"])
        # A failure is announced assertively; a confirmation politely.
        self.assertEqual(result["live"], "assertive")

    def test_a_second_notice_replaces_the_first_and_its_timer(self):
        result = self._run_node(
            """
            const page = harness(notice);
            page.banner.show('First.', 'success');
            page.banner.show('Second.', 'info');
            process.stdout.write(JSON.stringify({
                pendingTimers: page.clock.pending.size,
                text: page.text.textContent,
                classes: [...page.root.classes].sort(),
                shownIcons: notice.policy.TYPES.filter(
                    type => page.icon(type).getAttribute('hidden') === null
                )
            }));
            """
        )
        # One module-scope timer, cleared by every show(): a per-notice timer is
        # how a replaced notice ends up hiding its successor.
        self.assertEqual(result["pendingTimers"], 1)
        self.assertEqual(result["text"], "Second.")
        self.assertEqual(result["classes"], ["is-info"])
        self.assertEqual(result["shownIcons"], ["info"])

    def test_a_persistent_notice_schedules_nothing(self):
        result = self._run_node(
            """
            const page = harness(notice);
            page.banner.show('Could not save.', 'error');
            const afterError = page.clock.pending.size;
            page.banner.show('Careful.', 'warning');
            const afterWarning = page.clock.pending.size;
            page.clock.fire();
            process.stdout.write(JSON.stringify({
                afterError,
                afterWarning,
                stillVisible: page.visible()
            }));
            """
        )
        self.assertEqual(result["afterError"], 0)
        self.assertEqual(result["afterWarning"], 0)
        self.assertTrue(result["stillVisible"])

    def test_a_transient_notice_hides_itself_when_its_timer_fires(self):
        result = self._run_node(
            """
            const page = harness(notice);
            page.banner.show('Saved.', 'success');
            const delay = [...page.clock.pending.values()][0].ms;
            page.clock.fire();
            process.stdout.write(JSON.stringify({
                delay,
                visible: page.visible(),
                slot: page.banner.current()
            }));
            """
        )
        self.assertEqual(result["delay"], 6000)
        self.assertFalse(result["visible"])
        self.assertIsNone(result["slot"])

    def test_a_superseded_timer_cannot_hide_the_notice_that_replaced_it(self):
        result = self._run_node(
            """
            const page = harness(notice);
            page.banner.show('Saved.', 'success');
            // The stale entry keeps its callback; a real clock would still
            // fire it if the adapter had forgotten to cancel it.
            const stale = [...page.clock.pending.values()][0];
            page.banner.show('Could not save.', 'error');
            stale.fn();
            process.stdout.write(JSON.stringify({
                visible: page.visible(),
                text: page.text.textContent,
                classes: [...page.root.classes].sort()
            }));
            """
        )
        self.assertTrue(result["visible"])
        self.assertEqual(result["text"], "Could not save.")
        self.assertEqual(result["classes"], ["is-error"])

    def test_dismiss_hides_once_and_a_late_tick_cannot_reopen_it(self):
        result = self._run_node(
            """
            const page = harness(notice);
            page.banner.show('Saved.', 'success');
            const stale = [...page.clock.pending.values()][0];
            page.banner.dismiss();
            const afterDismiss = {
                visible: page.visible(),
                pendingTimers: page.clock.pending.size,
                slot: page.banner.current()
            };
            stale.fn();
            process.stdout.write(JSON.stringify({
                afterDismiss,
                stillHidden: !page.visible()
            }));
            """
        )
        self.assertFalse(result["afterDismiss"]["visible"])
        self.assertEqual(result["afterDismiss"]["pendingTimers"], 0)
        self.assertIsNone(result["afterDismiss"]["slot"])
        self.assertTrue(result["stillHidden"])

    def test_an_empty_message_clears_the_banner(self):
        result = self._run_node(
            """
            const page = harness(notice);
            page.banner.show('Saved.', 'success');
            const state = page.banner.show('   ', 'error');
            process.stdout.write(JSON.stringify({
                state,
                visible: page.visible(),
                pendingTimers: page.clock.pending.size
            }));
            """
        )
        self.assertIsNone(result["state"])
        self.assertFalse(result["visible"])
        self.assertEqual(result["pendingTimers"], 0)


@unittest.skipUnless(NODE, "Node.js is required for notice banner tests")
class UpdateCheckReportsOnceTestCase(unittest.TestCase):
    """The reported bug: checkForUpdates() wrote every outcome to both of the
    launcher's old message sinks, so one failure arrived twice, in two corners,
    with two lifetimes. The real function is sliced out of launcher.js and run
    against a stubbed fetch; what is counted is how many notices it raises."""

    def _slice(self, source: str, start: str, end: str) -> str:
        begin = source.index(start)
        return source[begin:source.index(end, begin)]

    def _run_update_check(self, response_body: dict, ok: bool):
        launcher_js = LAUNCHER_JS.read_text(encoding="utf-8")
        check_for_updates = self._slice(
            launcher_js, "    async function checkForUpdates()", "    function requestRestartLifecycle("
        )
        harness = """
            const notices = [];
            let restartRequested = false;
            let installKind = 'git';
            const RELEASES_URL = '';
            const window = { open: () => {} };
            const button = {
                disabled: false,
                classList: { add: () => {}, remove: () => {} }
            };
            const document = { getElementById: () => button };
            function showGridVibeNotice(text, type) {
                notices.push({ text, type });
                return null;
            }
            function describeFailure(summary, error) {
                const detail = String((error && error.message) || '').trim();
                if (!detail) { return summary; }
                return /[.!?]$/.test(detail) ? `${summary} ${detail}` : `${summary} ${detail}.`;
            }
            function shortCommit(value) { return String(value || '').slice(0, 7); }
            function requestRestartLifecycle() { restartRequested = true; }
            async function openGenericConfirmModal() { return false; }
            const RESPONSE = JSON.parse(process.argv[2]);
            async function fetch() {
                return { ok: RESPONSE.ok, json: async () => RESPONSE.body };
            }
        """ + check_for_updates + """
            checkForUpdates().then(() => {
                process.stdout.write(JSON.stringify({ notices, restartRequested }));
            });
        """
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), json.dumps({"ok": ok, "body": response_body})],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_failed_update_raises_exactly_one_notice(self):
        result = self._run_update_check(
            {"error": "could not reach the git remote"}, ok=False
        )
        self.assertEqual(len(result["notices"]), 1)
        self.assertEqual(result["notices"][0]["type"], "error")
        # One prefix, from the caller — the server's sentence is appended, not
        # wrapped in a second "Update failed:".
        self.assertEqual(
            result["notices"][0]["text"],
            "The update check failed. could not reach the git remote.",
        )

    def test_a_failure_with_no_server_message_is_still_a_sentence(self):
        result = self._run_update_check({}, ok=False)
        self.assertEqual(len(result["notices"]), 1)
        self.assertEqual(result["notices"][0]["text"], "The update check failed.")

    def test_an_up_to_date_check_raises_exactly_one_notice(self):
        result = self._run_update_check(
            {"updated": False, "message": "GridVibe is already up to date."}, ok=True
        )
        self.assertEqual(len(result["notices"]), 1)
        self.assertEqual(result["notices"][0]["type"], "success")
        self.assertFalse(result["restartRequested"])

    def test_an_update_needing_a_restart_reports_once_and_asks_to_restart(self):
        result = self._run_update_check(
            {
                "updated": True,
                "restart_required": True,
                "message": "Updated main to abc1234.",
            },
            ok=True,
        )
        self.assertEqual(len(result["notices"]), 1)
        self.assertTrue(result["restartRequested"])


@unittest.skipUnless(NODE, "Node.js is required for notice banner tests")
class LaunchRetargetNoticeTestCase(unittest.TestCase):
    """Editing a saved row's directory drops its explorer state, and says so.

    The three functions behind that report are sliced out of launcher.js and
    run: which rows were retargeted, the sentence naming them, and the severity
    the one launch notice goes out at. They are separate from the launch itself
    precisely so they can be executed -- the launch is a network round trip and
    a window open.
    """

    def _slice(self, source: str, start: str, end: str) -> str:
        begin = source.index(start)
        return source[begin:source.index(end, begin)]

    def _run(self, body: str, rows=()):
        launcher_js = LAUNCHER_JS.read_text(encoding="utf-8")
        functions = self._slice(
            launcher_js,
            "    function retargetedExplorerRowTitles()",
            "    function collectTerminalDrafts()",
        )
        harness = (
            """
            const ROWS = JSON.parse(process.argv[2]);
            /* A launcher row: its title and subdirectory inputs, plus the
               directory its saved explorer state was captured under. */
            function makeRow(spec) {
                return {
                    dataset: { explorerTabsDir: spec.tabsDir },
                    querySelector(selector) {
                        if (selector === '.t-title') {
                            return spec.title === null
                                ? null
                                : { value: spec.title };
                        }
                        if (selector === '.t-dir') {
                            return spec.dir === null ? null : { value: spec.dir };
                        }
                        return null;
                    },
                    __mode: spec.mode
                };
            }
            const rows = ROWS.map(makeRow);
            const document = { querySelectorAll: () => rows };
            function getTerminalCommandMode(row) { return row.__mode; }
            """
            + functions
            + body
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), json.dumps(list(rows))],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    @staticmethod
    def _row(mode="explorer", title="", tabs_dir="src", directory="src"):
        return {"mode": mode, "title": title, "tabsDir": tabs_dir, "dir": directory}

    def _titles(self, rows):
        return self._run(
            "process.stdout.write(JSON.stringify(retargetedExplorerRowTitles()));",
            rows,
        )

    def test_only_a_row_whose_directory_moved_is_reported(self):
        titles = self._titles([
            self._row(title="Kept", tabs_dir="src", directory="src"),
            self._row(title="Moved", tabs_dir="src", directory="docs"),
        ])
        self.assertEqual(titles, ["Moved"])

    def test_a_row_with_no_saved_explorer_state_is_never_reported(self):
        # Nothing was captured under a previous root, so nothing was dropped.
        titles = self._titles([self._row(title="Fresh", tabs_dir="", directory="docs")])
        self.assertEqual(titles, [])

    def test_a_non_explorer_row_is_never_reported(self):
        titles = self._titles([
            self._row(mode="command", title="Shell", tabs_dir="src", directory="docs"),
        ])
        self.assertEqual(titles, [])

    def test_an_untitled_row_is_named_by_its_position_in_the_form(self):
        # The bug this pins: numbering the *survivors* called the fifth pane
        # "Terminal 1" and pointed the reader at a row they never touched.
        titles = self._titles([
            self._row(title="A", tabs_dir="src", directory="src"),
            self._row(title="B", tabs_dir="src", directory="src"),
            self._row(title="", tabs_dir="src", directory="docs"),
        ])
        self.assertEqual(titles, ["Terminal 3"])

    def test_several_untitled_rows_keep_their_own_form_positions(self):
        titles = self._titles([
            self._row(title="", tabs_dir="src", directory="src"),
            self._row(title="", tabs_dir="src", directory="docs"),
            self._row(title="", tabs_dir="src", directory="web"),
        ])
        self.assertEqual(titles, ["Terminal 2", "Terminal 3"])

    def test_a_row_missing_its_directory_input_is_read_without_throwing(self):
        titles = self._titles([
            self._row(title="Odd", tabs_dir="src", directory=None),
        ])
        self.assertEqual(titles, ["Odd"])

    def test_the_note_names_one_pane_and_counts_several(self):
        result = self._run(
            """
            process.stdout.write(JSON.stringify({
                none: explorerRetargetLaunchNote([]),
                one: explorerRetargetLaunchNote(['Docs']),
                many: explorerRetargetLaunchNote(['Docs', 'Web', 'Api'])
            }));
            """
        )
        self.assertEqual(result["none"], "")
        self.assertIn('"Docs"', result["one"])
        self.assertIn("3 panes", result["many"])
        for note in (result["one"], result["many"]):
            self.assertIn("was not restored", note)
            self.assertIn("the folder was changed after saving", note)

    def test_discarded_state_is_a_warning_and_not_an_auto_dismissing_info(self):
        # `info` and `success` dismiss themselves after six seconds; a report
        # that saved state was thrown away has to stay until it is read.
        result = self._run(
            """
            process.stdout.write(JSON.stringify({
                clean: launchNoticeSeverity(0, 0),
                retargeted: launchNoticeSeverity(0, 1),
                preflight: launchNoticeSeverity(2, 0),
                both: launchNoticeSeverity(1, 1)
            }));
            """
        )
        self.assertEqual(result["clean"], "success")
        self.assertEqual(result["retargeted"], "warning")
        self.assertEqual(result["preflight"], "warning")
        self.assertEqual(result["both"], "warning")


if __name__ == "__main__":
    unittest.main()
