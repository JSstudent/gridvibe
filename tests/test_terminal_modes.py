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


if __name__ == "__main__":
    unittest.main()
