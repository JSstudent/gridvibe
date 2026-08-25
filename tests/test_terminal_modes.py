"""Behavioral coverage for the mouse-reporting recovery a crashed TUI needs.

`terminal-modes.js` takes its socket, its pane writer, and its timers from an
injected runtime, so the module runs in Node against the same sequence the page
drives: leave the room, rejoin it, let the server replay its rolling buffer, and
only then write the teardown.

The contract pinned here is the one the bug was: the replay carries the dead
program's `\\x1b[?1003h`, so a teardown written before it is undone by it. The
teardown must land after the replayed bytes, exactly once, and it must still
land when the rejoin is never acknowledged at all.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TERMINAL_MODES_JS = STATIC_JS / "terminal-modes.js"

NODE = shutil.which("node")

# The whole runtime surface the module can reach: a socket that records emits
# and holds the join acknowledgement, the pane's `term.write`, and a clock the
# test fires by hand. `writes` is the pane's received stream — the replay is
# pushed into the same array because both arrive through `term.write`, which is
# what makes their order the thing under test.
HARNESS_STUBS = """
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
        armed: () => pending.size,
        fire: () => {
            const due = [...pending.values()];
            pending.clear();
            due.forEach(entry => entry.fn());
        }
    };
}

/* The page's half of Reset view: a grid whose slot 0 holds one pane, plus the
   group switch that hands that slot to a different pane object. Each pane
   records what its own `term.write` received, so "which pane got the teardown"
   is answered by reading the panes rather than by trusting an index. */
function makePane(name) {
    const pane = { name, _attached: true, _pendingOutput: '', stream: [] };
    pane.term = { write: data => pane.stream.push(data) };
    return pane;
}

function grid(modes) {
    const paneA = makePane('a');
    const paneB = makePane('b');
    const terminals = [paneA];
    const sessionIds = ['sess-a'];

    /* The page's captured flush, verbatim in behaviour: the pane's queued
       bytes go in before anything written after them. */
    const flush = pane => {
        if (!pane || !pane._attached || !pane.term || !pane._pendingOutput) {
            return;
        }
        const pending = pane._pendingOutput;
        pane._pendingOutput = '';
        pane.term.write(pending);
    };

    return {
        paneA,
        paneB,
        capture: () => modes.captureResetTarget({
            pane: terminals[0],
            sessionId: sessionIds[0],
            flush,
            write: (pane, data) => { if (pane?.term) { pane.term.write(data); } },
            currentPane: () => terminals[0],
            currentSessionId: () => sessionIds[0]
        }),
        switchGroup: () => { terminals[0] = paneB; sessionIds[0] = 'sess-b'; },
        reconnect: () => { sessionIds[0] = 'sess-a2'; },
        emptySlot: () => { terminals[0] = null; sessionIds[0] = null; },
        currentPane: () => terminals[0],
        stream: pane => pane.stream.map(
            data => (data === modes.MOUSE_REPORTING_RESET ? 'teardown' : data)
        )
    };
}

function harness(modes, options) {
    const opts = options || {};
    const writes = [];
    const emits = [];
    const clock = fakeClock();
    let ack = null;

    const runtime = {
        sessionId: 'sess-1',
        emit: (event, payload, callback) => {
            emits.push({ event, sessionId: payload && payload.session_id });
            if (event === 'join_session' && typeof callback === 'function') {
                ack = callback;
            }
        },
        write: data => writes.push(data),
        setTimeout: clock.setTimeout,
        clearTimeout: clock.clearTimeout
    };
    if (Object.prototype.hasOwnProperty.call(opts, 'timeoutMs')) {
        runtime.timeoutMs = opts.timeoutMs;
    }
    if (opts.noSession) { delete runtime.sessionId; }
    if (opts.noSocket) { delete runtime.emit; }
    if (opts.noTimers) { delete runtime.setTimeout; delete runtime.clearTimeout; }

    return {
        writes,
        emits,
        clock,
        // The server replays the buffer inside the join handler; the ack packet
        // is written after it, over the same ordered connection.
        replay: text => writes.push(text),
        acknowledged: () => ack !== null,
        ack: () => { if (ack) { ack(); } },
        start: () => modes.rejoinAndResetAfterReplay(runtime),
        // Symbolic view of what reached the pane, so assertions read as the
        // sequence rather than as escape-sequence literals.
        stream: () => writes.map(
            data => (data === modes.MOUSE_REPORTING_RESET ? 'teardown' : data)
        )
    };
}
"""


@unittest.skipUnless(NODE, "Node.js is required for terminal mode tests")
class TerminalModesNodeTestCase(unittest.TestCase):
    def _run_node(self, body: str):
        script = (
            HARNESS_STUBS
            + "\nconst modes = require(process.argv[2]);\n"
            + "(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(TERMINAL_MODES_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


class MouseReportingTeardownTestCase(TerminalModesNodeTestCase):
    """The write itself — client-side only, and complete."""

    def test_the_teardown_disarms_every_mouse_reporting_mode(self):
        result = self._run_node(
            """
            process.stdout.write(JSON.stringify({
                modes: modes.MOUSE_REPORTING_MODES,
                reset: modes.MOUSE_REPORTING_RESET
            }));
            """
        )
        # The trackers and the encodings alike: a stale encoding left on its own
        # still changes what the next program's reports look like.
        self.assertEqual(result["modes"], [1000, 1002, 1003, 1005, 1006, 1015])
        for mode in result["modes"]:
            with self.subTest(mode=mode):
                self.assertIn(f"\x1b[?{mode}l", result["reset"])
        # Disarming only. An `h` anywhere in here would arm a mode instead.
        self.assertNotIn("h", result["reset"])

    def test_resetting_writes_the_teardown_once_and_reports_it(self):
        result = self._run_node(
            """
            const writes = [];
            const wrote = modes.resetMouseReporting(data => writes.push(data));
            process.stdout.write(JSON.stringify({
                wrote,
                count: writes.length,
                isTeardown: writes[0] === modes.MOUSE_REPORTING_RESET
            }));
            """
        )
        self.assertTrue(result["wrote"])
        self.assertEqual(result["count"], 1)
        self.assertTrue(result["isTeardown"])

    def test_a_pane_with_no_terminal_is_reported_rather_than_thrown_at(self):
        result = self._run_node(
            """
            process.stdout.write(JSON.stringify({
                missing: modes.resetMouseReporting(null),
                notAFunction: modes.resetMouseReporting({})
            }));
            """
        )
        self.assertFalse(result["missing"])
        self.assertFalse(result["notAFunction"])


class RejoinReplayOrderingTestCase(TerminalModesNodeTestCase):
    """ISSUE-2026-038: the replay re-arms what term.reset() just cleared."""

    def test_the_teardown_lands_after_the_replayed_buffer(self):
        """The regression itself. The buffer still holds the dead TUI's
        `?1003h`, so a teardown written before the replay is undone by it."""
        result = self._run_node(
            """
            const page = harness(modes);
            const pending = page.start();
            // What handle_join_session does: replay the buffer, then ack.
            page.replay('\\x1b[?1003h\\x1b[?1006hsome TUI frame');
            page.ack();
            const outcome = await pending;
            process.stdout.write(JSON.stringify({
                settledBy: outcome.settledBy,
                stream: page.stream()
            }));
            """
        )
        self.assertEqual(result["settledBy"], "ack")
        self.assertEqual(
            result["stream"],
            ["\x1b[?1003h\x1b[?1006hsome TUI frame", "teardown"],
        )

    def test_the_pane_leaves_the_room_before_it_rejoins_it(self):
        """Without the leave the server counts the session as already joined
        and replays nothing, so the rejoin would restore no content at all."""
        result = self._run_node(
            """
            const page = harness(modes);
            const pending = page.start();
            page.ack();
            await pending;
            process.stdout.write(JSON.stringify({ emits: page.emits }));
            """
        )
        self.assertEqual(
            result["emits"],
            [
                {"event": "leave_session", "sessionId": "sess-1"},
                {"event": "join_session", "sessionId": "sess-1"},
            ],
        )

    def test_an_unacknowledged_rejoin_still_resets_the_pane(self):
        """A socket that never answers must not leave the pane typing mouse
        reports forever — Reset view has to come back either way."""
        result = self._run_node(
            """
            const page = harness(modes, { timeoutMs: 1500 });
            const pending = page.start();
            page.replay('replayed');
            const beforeFallback = page.stream();
            const armed = page.clock.armed();
            const delay = [...page.clock.pending.values()][0].ms;
            page.clock.fire();
            const outcome = await pending;
            process.stdout.write(JSON.stringify({
                beforeFallback,
                armed,
                delay,
                settledBy: outcome.settledBy,
                stream: page.stream()
            }));
            """
        )
        self.assertEqual(result["beforeFallback"], ["replayed"])
        self.assertEqual(result["armed"], 1)
        self.assertEqual(result["delay"], 1500)
        self.assertEqual(result["settledBy"], "timeout")
        self.assertEqual(result["stream"], ["replayed", "teardown"])

    def test_an_acknowledged_rejoin_cancels_the_fallback_and_writes_once(self):
        """A second teardown after a live program had re-armed the mode would
        disarm it again for no reason."""
        result = self._run_node(
            """
            const page = harness(modes);
            const pending = page.start();
            page.replay('replayed');
            page.ack();
            await pending;
            const armedAfterAck = page.clock.armed();
            page.clock.fire();
            page.ack();
            await new Promise(resolve => setImmediate(resolve));
            process.stdout.write(JSON.stringify({
                armedAfterAck,
                stream: page.stream()
            }));
            """
        )
        self.assertEqual(result["armedAfterAck"], 0)
        self.assertEqual(result["stream"], ["replayed", "teardown"])

    def test_the_default_fallback_is_bounded(self):
        result = self._run_node(
            """
            const page = harness(modes);
            page.start();
            process.stdout.write(JSON.stringify({
                published: modes.REPLAY_SETTLE_TIMEOUT_MS,
                armedWith: [...page.clock.pending.values()][0].ms
            }));
            """
        )
        self.assertEqual(result["published"], 1500)
        self.assertEqual(result["armedWith"], 1500)

    def test_a_pane_with_nothing_to_rejoin_is_reset_immediately(self):
        """No socket and no session id means no replay to wait behind, so the
        teardown is the whole operation."""
        result = self._run_node(
            """
            const noSession = harness(modes, { noSession: true });
            const a = await noSession.start();
            const noSocket = harness(modes, { noSocket: true });
            const b = await noSocket.start();
            process.stdout.write(JSON.stringify({
                noSession: { settledBy: a.settledBy, stream: noSession.stream() },
                noSocket: { settledBy: b.settledBy, stream: noSocket.stream() }
            }));
            """
        )
        self.assertEqual(
            result["noSession"], {"settledBy": "none", "stream": ["teardown"]}
        )
        self.assertEqual(
            result["noSocket"], {"settledBy": "none", "stream": ["teardown"]}
        )

    def test_without_timers_the_reset_waits_for_the_ack_rather_than_racing_it(self):
        """Resetting before the replay is the bug; an unbounded wait is not."""
        result = self._run_node(
            """
            const page = harness(modes, { noTimers: true });
            const pending = page.start();
            await new Promise(resolve => setImmediate(resolve));
            const beforeAck = page.stream();
            page.replay('replayed');
            page.ack();
            await pending;
            process.stdout.write(JSON.stringify({
                beforeAck,
                stream: page.stream()
            }));
            """
        )
        self.assertEqual(result["beforeAck"], [])
        self.assertEqual(result["stream"], ["replayed", "teardown"])


class CapturedResetTargetTestCase(TerminalModesNodeTestCase):
    """A grid slot is not an identity, and Reset view is asynchronous.

    The teardown lands when the rejoin is acknowledged; a group switch inside
    that wait puts another pane in `terminals[index]`. The write follows the
    pane that asked, the slot work does not follow the slot.
    """

    def test_the_teardown_follows_the_pane_that_asked_after_a_group_switch(self):
        result = self._run_node(
            """
            const page = grid(modes);
            const target = page.capture();
            page.switchGroup();
            const wrote = target.write(modes.MOUSE_REPORTING_RESET);
            process.stdout.write(JSON.stringify({
                wrote,
                asked: page.stream(page.paneA),
                incoming: page.stream(page.paneB)
            }));
            """
        )
        self.assertTrue(result["wrote"])
        self.assertEqual(result["asked"], ["teardown"])
        # The pane that merely inherited the slot receives nothing at all.
        self.assertEqual(result["incoming"], [])

    def test_the_captured_pane_queue_is_flushed_before_the_teardown(self):
        """The replay is what re-arms the mode, so it has to be applied to the
        captured pane first — including while that pane is off-screen."""
        result = self._run_node(
            """
            const page = grid(modes);
            const target = page.capture();
            page.paneA._pendingOutput = '\x1b[?1003hreplayed';
            page.switchGroup();
            target.write(modes.MOUSE_REPORTING_RESET);
            process.stdout.write(JSON.stringify({
                asked: page.stream(page.paneA),
                incoming: page.stream(page.paneB),
                drained: page.paneA._pendingOutput
            }));
            """
        )
        self.assertEqual(result["asked"], ["[?1003hreplayed", "teardown"])
        self.assertEqual(result["incoming"], [])
        self.assertEqual(result["drained"], "")

    def test_readiness_stops_before_fitting_the_pane_that_took_the_slot(self):
        """A group switch during the yielded readiness loop ends the old
        operation before its next slot-based fit or final readiness read."""
        result = self._run_node(
            """
            const page = grid(modes);
            const target = page.capture();
            const attempts = [];
            let readyReads = 0;
            const ready = await modes.waitForCurrentPaneReady({
                maxAttempts: 3,
                isCurrent: () => target.isCurrent(),
                attempt: () => {
                    attempts.push(page.currentPane().name);
                    return false;
                },
                wait: async () => { page.switchGroup(); },
                ready: () => { readyReads += 1; return true; }
            });
            process.stdout.write(JSON.stringify({ ready, attempts, readyReads }));
            """
        )
        self.assertFalse(result["ready"])
        self.assertEqual(result["attempts"], ["a"])
        self.assertEqual(result["readyReads"], 0)

    def test_readiness_can_finish_while_the_capture_stays_current(self):
        result = self._run_node(
            """
            const page = grid(modes);
            const target = page.capture();
            let attempts = 0;
            const ready = await modes.waitForCurrentPaneReady({
                maxAttempts: 3,
                isCurrent: () => target.isCurrent(),
                attempt: () => { attempts += 1; return attempts === 2; },
                wait: async () => {}
            });
            process.stdout.write(JSON.stringify({ ready, attempts }));
            """
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["attempts"], 2)

    def test_slot_work_is_skipped_once_the_pane_or_its_session_moves(self):
        """The redraw and the busy release address `index`; the incoming group
        must never inherit either from a reset it did not ask for."""
        result = self._run_node(
            """
            const fresh = grid(modes);
            const before = fresh.capture().isCurrent();

            const replaced = grid(modes);
            const replacedTarget = replaced.capture();
            replaced.switchGroup();

            const reconnected = grid(modes);
            const reconnectedTarget = reconnected.capture();
            reconnected.reconnect();

            const emptied = grid(modes);
            const emptiedTarget = emptied.capture();
            emptied.emptySlot();

            process.stdout.write(JSON.stringify({
                before,
                afterGroupSwitch: replacedTarget.isCurrent(),
                afterSessionChange: reconnectedTarget.isCurrent(),
                afterSlotEmptied: emptiedTarget.isCurrent()
            }));
            """
        )
        self.assertTrue(result["before"])
        self.assertFalse(result["afterGroupSwitch"])
        # A pane that reconnected under the same object is a different session.
        self.assertFalse(result["afterSessionChange"])
        self.assertFalse(result["afterSlotEmptied"])

    def test_an_empty_slot_captures_a_target_that_writes_nothing(self):
        """Capturing from a slot with no pane must report rather than throw."""
        result = self._run_node(
            """
            const target = modes.captureResetTarget({
                pane: null,
                sessionId: 'sess-a',
                write: () => { throw new Error('must not write'); }
            });
            process.stdout.write(JSON.stringify({
                wrote: target.write(modes.MOUSE_REPORTING_RESET),
                isCurrent: target.isCurrent()
            }));
            """
        )
        self.assertFalse(result["wrote"])
        self.assertFalse(result["isCurrent"])

    def test_the_replay_and_the_teardown_both_reach_the_original_pane_in_order(self):
        """The whole sequence end to end: the rejoin is acknowledged only after
        the group has already switched, and the ordering the module exists for
        still holds — on the pane that asked."""
        result = self._run_node(
            """
            const page = grid(modes);
            const target = page.capture();
            const clock = fakeClock();
            let ack = null;
            const pending = modes.rejoinAndResetAfterReplay({
                sessionId: 'sess-a',
                emit: (event, payload, callback) => {
                    if (event === 'join_session') { ack = callback; }
                },
                write: data => target.write(data),
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });
            // The server replays into the pane's own queue, then acknowledges —
            // and the user switched group in between.
            page.paneA._pendingOutput = '\x1b[?1003hsome TUI frame';
            page.switchGroup();
            ack();
            const outcome = await pending;
            process.stdout.write(JSON.stringify({
                settledBy: outcome.settledBy,
                asked: page.stream(page.paneA),
                incoming: page.stream(page.paneB),
                slotWorkAllowed: target.isCurrent()
            }));
            """
        )
        self.assertEqual(result["settledBy"], "ack")
        self.assertEqual(
            result["asked"], ["[?1003hsome TUI frame", "teardown"]
        )
        self.assertEqual(result["incoming"], [])
        self.assertFalse(result["slotWorkAllowed"])


if __name__ == "__main__":
    unittest.main()
