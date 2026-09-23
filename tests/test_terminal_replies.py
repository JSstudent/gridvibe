"""Behavioral coverage for the terminal's own answers reaching the wrong program.

A query is a sequence a program writes into its *output* to make the terminal
talk back, and the terminal answers by typing the reply into its *input*. The
answer is therefore only ever correct in the instant it is produced: whoever
owns the pty when it lands reads it as keystrokes.

GridVibe produces those answers late. Output for a pane that is not yet fitted
is held in `_pendingOutput`, and the fit is a debounce plus a bounded retry
ladder for a pane with no size yet -- a pane being replaced, restored, or
switched to. The backlog is then parsed in one go and every query inside it is
answered *now*, hundreds of milliseconds after the asker stopped listening:

    > ]10;rgb:e0e0/e0e0/e0e0\\]11;rgb:0d0d/0d0d/0d0d\\

Two owners share one list of what a query is. `web/static/js/terminal-replies.js`
strips it off a backlog the page is writing late, and `_TERMINAL_QUERY_RE` in
`web/api.py` strips it off the buffer the server replays into a pane whose
program has since changed. The fixture table below is deliberately one table
for both, so neither side can quietly grow a hole the other has covered.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import web.api as api

STATIC_JS = Path(__file__).resolve().parent.parent / "web" / "static" / "js"
TERMINAL_REPLIES_JS = STATIC_JS / "terminal-replies.js"

NODE = shutil.which("node")

ESC = "\x1b"
ST = ESC + "\\"
BEL = "\x07"

# Sequences whose only purpose is to make the terminal talk back. Every one of
# these must be removed from bytes that are being delivered late, because the
# answer can no longer reach the program that asked for it.
QUERY_FIXTURES = {
    "OSC 10 foreground (ST)": ESC + "]10;?" + ST,
    "OSC 11 background (BEL)": ESC + "]11;?" + BEL,
    "OSC 12 cursor colour": ESC + "]12;?" + ST,
    "OSC 4 indexed palette": ESC + "]4;1;?" + ST,
    "OSC 5 special colour": ESC + "]5;0;?" + ST,
    "OSC 52 clipboard read": ESC + "]52;c;?" + ST,
    "DA1": ESC + "[c",
    "DA1 with parameter": ESC + "[0c",
    "DA2": ESC + "[>c",
    "DA3": ESC + "[=c",
    "DSR device status": ESC + "[5n",
    "DSR cursor position": ESC + "[6n",
    "DECXCPR extended cursor": ESC + "[?6n",
    "DECDSR locator": ESC + "[?25n",
    "XTVERSION": ESC + "[>0q",
    "XTVERSION without parameter": ESC + "[>q",
    "DECRQM private (sync)": ESC + "[?2026$p",
    "DECRQM ansi": ESC + "[4$p",
    "XTWINOPS text area size": ESC + "[18t",
    "XTWINOPS pixel size": ESC + "[14t",
    "XTWINOPS cell size": ESC + "[16t",
    "XTWINOPS screen size": ESC + "[19t",
    "XTGETTCAP": ESC + "P+q544e" + ST,
    "DECRQSS": ESC + "P$qm" + ST,
}

# Sequences that render, or set state a live program depends on. Removing any
# of these would make the pane wrong rather than quiet -- the failure mode a
# widened query list is most likely to introduce.
ACTION_FIXTURES = {
    "title stack push": ESC + "[22;0t",
    "title stack pop": ESC + "[23;0t",
    "DECSCUSR cursor shape": ESC + "[1 q",
    "mouse reporting armed": ESC + "[?1003h",
    "mouse reporting disarmed": ESC + "[?1003l",
    "SGR colour": ESC + "[31m",
    "cursor position set": ESC + "[10;20H",
    "alternate buffer": ESC + "[?1049h",
    "OSC 0 window title": ESC + "]0;a title" + BEL,
    "OSC 11 background set": ESC + "]11;#000000" + ST,
    "OSC 9;4 progress": ESC + "]9;4;1;50" + BEL,
    "bracketed paste": ESC + "[?2004h",
}


class ServerReplayQueryFilterTestCase(unittest.TestCase):
    """`_TERMINAL_QUERY_RE` -- what the replayed buffer may still contain."""

    def test_every_query_is_removed_from_a_replay(self):
        for name, sequence in QUERY_FIXTURES.items():
            with self.subTest(query=name):
                self.assertEqual(api._TERMINAL_QUERY_RE.sub("", sequence), "")

    def test_no_rendering_or_mode_sequence_is_removed_from_a_replay(self):
        # A rejoin to a pane whose TUI is still running has to restore that
        # program's modes, so the filter is queries only -- never state.
        for name, sequence in ACTION_FIXTURES.items():
            with self.subTest(action=name):
                self.assertEqual(api._TERMINAL_QUERY_RE.sub("", sequence), sequence)

    def test_a_query_is_removed_from_the_middle_of_real_output(self):
        stream = (
            "welcome\r\n"
            + ESC + "[?1003h"
            + ESC + "]11;?" + ST
            + "prompt$ "
            + ESC + "[6n"
            + ESC + "[31m"
        )
        self.assertEqual(
            api._TERMINAL_QUERY_RE.sub("", stream),
            "welcome\r\n" + ESC + "[?1003h" + "prompt$ " + ESC + "[31m",
        )


@unittest.skipUnless(NODE, "Node.js is required for terminal reply tests")
class TerminalRepliesNodeTestCase(unittest.TestCase):
    """The page's half, executed in Node against the real module."""

    def _run_node(self, body: str):
        script = (
            "const replies = require(process.argv[2]);\n"
            + "const fixtures = JSON.parse(process.argv[3]);\n"
            + FAKE_CLOCK
            + "(async () => {\n"
            + body
            + "\n})().catch(error => { console.error(error); process.exit(1); });\n"
        )
        payload = json.dumps({"queries": QUERY_FIXTURES, "actions": ACTION_FIXTURES})
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(script, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(TERMINAL_REPLIES_JS), payload],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)


FAKE_CLOCK = """
function fakeClock() {
    const pending = new Map();
    let sequence = 0;
    return {
        setTimeout: (fn, ms) => {
            sequence += 1;
            pending.set(sequence, { fn, ms });
            return sequence;
        },
        clearTimeout: handle => { pending.delete(handle); },
        armed: () => pending.size,
        delays: () => [...pending.values()].map(entry => entry.ms),
        fire: () => {
            const due = [...pending.values()];
            pending.clear();
            due.forEach(entry => entry.fn());
        }
    };
}
"""


class QueryListParityTestCase(TerminalRepliesNodeTestCase):
    """One list, two owners. A hole on either side is a leak on that side."""

    def test_the_page_strips_every_query_the_server_strips(self):
        result = self._run_node(
            """
            const out = {};
            for (const [name, seq] of Object.entries(fixtures.queries)) {
                out[name] = replies.stripTerminalQueries(seq);
            }
            process.stdout.write(JSON.stringify(out));
            """
        )
        for name in QUERY_FIXTURES:
            with self.subTest(query=name):
                self.assertEqual(result[name], "")

    def test_the_page_keeps_every_sequence_the_server_keeps(self):
        result = self._run_node(
            """
            const out = {};
            for (const [name, seq] of Object.entries(fixtures.actions)) {
                out[name] = replies.stripTerminalQueries(seq);
            }
            process.stdout.write(JSON.stringify(out));
            """
        )
        for name, sequence in ACTION_FIXTURES.items():
            with self.subTest(action=name):
                self.assertEqual(result[name], sequence)


class StaleDeferralBudgetTestCase(TerminalRepliesNodeTestCase):
    """Age, not identity, is what makes an answer wrong."""

    def test_a_prompt_flush_is_written_whole_and_still_answers(self):
        # The common case, and the reason the budget exists at all: a pane that
        # fits promptly keeps answering colour queries, so an agent CLI can go
        # on detecting the theme.
        result = self._run_node(
            """
            const pane = {};
            const writes = [];
            const outcome = replies.writeDeferred({
                pane,
                data: 'hello' + fixtures.queries['OSC 11 background (BEL)'],
                heldMs: 40,
                write: (payload, done) => { writes.push(payload); if (done) done(); }
            });
            process.stdout.write(JSON.stringify({
                outcome,
                writes,
                suppressedDuring: false,
                depth: replies.suppressionDepth(pane)
            }));
            """
        )
        self.assertFalse(result["outcome"]["stale"])
        self.assertTrue(result["outcome"]["written"])
        # Written verbatim -- the query is still in it.
        self.assertEqual(len(result["writes"]), 1)
        self.assertIn("\u001b]11;?", result["writes"][0])
        self.assertEqual(result["depth"], 0)

    def test_a_backlog_held_past_the_budget_loses_its_queries(self):
        result = self._run_node(
            """
            const pane = {};
            const writes = [];
            const clock = fakeClock();
            const outcome = replies.writeDeferred({
                pane,
                data: 'banner\\r\\n'
                    + fixtures.queries['OSC 10 foreground (ST)']
                    + fixtures.queries['OSC 11 background (BEL)']
                    + fixtures.actions['mouse reporting armed']
                    + 'prompt$ ',
                heldMs: replies.STALE_DEFERRAL_MS + 1,
                write: (payload, done) => { writes.push(payload); if (done) done(); },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });
            process.stdout.write(JSON.stringify({ outcome, writes }));
            """
        )
        self.assertTrue(result["outcome"]["stale"])
        written = result["writes"][0]
        # The queries are gone...
        self.assertNotIn("]10;?", written)
        self.assertNotIn("]11;?", written)
        # ...and nothing else is. A stale backlog still has to render, and the
        # mode a live program armed still has to be restored.
        self.assertEqual(written, "banner\r\n\u001b[?1003hprompt$ ")

    def test_the_budget_is_exclusive_at_its_own_edge(self):
        result = self._run_node(
            """
            const at = replies.deferredWritePlan({
                data: 'x', heldMs: replies.STALE_DEFERRAL_MS
            });
            const past = replies.deferredWritePlan({
                data: 'x', heldMs: replies.STALE_DEFERRAL_MS + 1
            });
            const unstated = replies.deferredWritePlan({ data: 'x' });
            process.stdout.write(JSON.stringify({
                at: at.stale, past: past.stale, unstated: unstated.stale,
                budget: replies.STALE_DEFERRAL_MS
            }));
            """
        )
        self.assertFalse(result["at"])
        self.assertTrue(result["past"])
        # A flush that never recorded a hold is not stale by default.
        self.assertFalse(result["unstated"])
        self.assertEqual(result["budget"], 150)

    def test_a_backlog_that_was_only_a_query_writes_nothing(self):
        result = self._run_node(
            """
            const pane = {};
            const writes = [];
            const clock = fakeClock();
            const outcome = replies.writeDeferred({
                pane,
                data: fixtures.queries['DSR cursor position'],
                heldMs: 5000,
                write: (payload, done) => { writes.push(payload); if (done) done(); },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });
            process.stdout.write(JSON.stringify({
                outcome, writes, depth: replies.suppressionDepth(pane)
            }));
            """
        )
        self.assertFalse(result["outcome"]["written"])
        self.assertEqual(result["writes"], [])
        # Nothing was written, so nothing may be left suppressed either.
        self.assertEqual(result["depth"], 0)

    def test_a_query_split_across_the_next_write_is_removed_as_one_sequence(self):
        result = self._run_node(
            """
            const pane = {};
            const writes = [];
            const clock = fakeClock();
            const io = {
                pane,
                write: (payload, done) => { writes.push(payload); if (done) done(); },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            };
            const first = replies.writeDeferred({
                ...io,
                data: 'banner' + '\\u001b]11;',
                heldMs: replies.STALE_DEFERRAL_MS + 1
            });
            const second = replies.writeFollowing({
                ...io,
                data: '?' + '\\u001b\\\\' + 'prompt$ '
            });
            process.stdout.write(JSON.stringify({
                first,
                second,
                writes,
                residue: replies.terminalQueryResidue(pane),
                suppressed: replies.repliesSuppressed(pane)
            }));
            """
        )
        self.assertEqual(result["writes"], ["banner", "prompt$ "])
        self.assertEqual(result["first"]["residue"], "\x1b]11;")
        self.assertEqual(result["second"]["residue"], "")
        self.assertEqual(result["residue"], "")
        self.assertFalse(result["suppressed"])

    def test_a_rendering_sequence_split_at_the_same_boundary_is_preserved(self):
        result = self._run_node(
            """
            const pane = {};
            const writes = [];
            const clock = fakeClock();
            const io = {
                pane,
                write: (payload, done) => { writes.push(payload); if (done) done(); },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            };
            replies.writeDeferred({
                ...io,
                data: 'banner' + '\\u001b[?1003',
                heldMs: replies.STALE_DEFERRAL_MS + 1
            });
            replies.writeFollowing({ ...io, data: 'hprompt$ ' });
            process.stdout.write(JSON.stringify({
                writes,
                residue: replies.terminalQueryResidue(pane)
            }));
            """
        )
        self.assertEqual(result["writes"], ["banner", "\x1b[?1003hprompt$ "])
        self.assertEqual(result["residue"], "")


class QuietWindowTestCase(TerminalRepliesNodeTestCase):
    """The backstop: a query form the list has never heard of still cannot leak."""

    def test_replies_are_refused_while_a_late_backlog_is_parsing(self):
        result = self._run_node(
            """
            const pane = {};
            const clock = fakeClock();
            let duringParse = null;
            let finish = null;
            replies.quietWrite({
                pane,
                data: 'late bytes',
                /* xterm.js parses asynchronously: the reply is emitted while
                   the parser is working, and `done` fires only afterwards. */
                write: (payload, done) => {
                    duringParse = replies.repliesSuppressed(pane);
                    finish = done;
                },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });
            const beforeFinish = replies.repliesSuppressed(pane);
            finish();
            process.stdout.write(JSON.stringify({
                duringParse,
                beforeFinish,
                afterFinish: replies.repliesSuppressed(pane),
                timersLeft: clock.armed()
            }));
            """
        )
        self.assertTrue(result["duringParse"])
        self.assertTrue(result["beforeFinish"])
        self.assertFalse(result["afterFinish"])
        # The fallback is cancelled by the parse it was covering for.
        self.assertEqual(result["timersLeft"], 0)

    def test_the_window_closes_on_the_clock_when_the_parse_never_reports(self):
        # Refusing a pane's input is the safe direction for milliseconds and
        # the wrong one forever.
        result = self._run_node(
            """
            const pane = {};
            const clock = fakeClock();
            replies.quietWrite({
                pane,
                data: 'late bytes',
                write: () => {},
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });
            const stuck = replies.repliesSuppressed(pane);
            const delays = clock.delays();
            clock.fire();
            process.stdout.write(JSON.stringify({
                stuck, delays, afterTimeout: replies.repliesSuppressed(pane)
            }));
            """
        )
        self.assertTrue(result["stuck"])
        self.assertEqual(result["delays"], [1000])
        self.assertFalse(result["afterTimeout"])

    def test_a_late_parse_after_the_fallback_does_not_reopen_the_window(self):
        result = self._run_node(
            """
            const pane = {};
            const clock = fakeClock();
            let finish = null;
            replies.quietWrite({
                pane,
                data: 'late bytes',
                write: (payload, done) => { finish = done; },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });
            clock.fire();
            const afterTimeout = replies.suppressionDepth(pane);
            finish();
            process.stdout.write(JSON.stringify({
                afterTimeout, afterLateParse: replies.suppressionDepth(pane)
            }));
            """
        )
        # Exactly one release, whichever of the two fires first.
        self.assertEqual(result["afterTimeout"], 0)
        self.assertEqual(result["afterLateParse"], 0)

    def test_two_overlapping_late_writes_keep_the_window_shut_until_both_finish(self):
        result = self._run_node(
            """
            const pane = {};
            const clock = fakeClock();
            const finishers = [];
            const start = () => replies.quietWrite({
                pane,
                data: 'late bytes',
                write: (payload, done) => { finishers.push(done); },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });
            start();
            start();
            const both = replies.suppressionDepth(pane);
            finishers[0]();
            const afterFirst = replies.repliesSuppressed(pane);
            finishers[1]();
            process.stdout.write(JSON.stringify({
                both, afterFirst, afterSecond: replies.repliesSuppressed(pane)
            }));
            """
        )
        self.assertEqual(result["both"], 2)
        # The first to finish must not reopen the channel for the second.
        self.assertTrue(result["afterFirst"])
        self.assertFalse(result["afterSecond"])

    def test_a_throwing_write_still_gives_the_pane_its_input_back(self):
        result = self._run_node(
            """
            const pane = {};
            const clock = fakeClock();
            let threw = false;
            try {
                replies.quietWrite({
                    pane,
                    data: 'late bytes',
                    write: () => { throw new Error('parser exploded'); },
                    setTimeout: clock.setTimeout,
                    clearTimeout: clock.clearTimeout
                });
            } catch (error) { threw = true; }
            process.stdout.write(JSON.stringify({
                threw,
                suppressed: replies.repliesSuppressed(pane),
                timersLeft: clock.armed()
            }));
            """
        )
        self.assertTrue(result["threw"])
        self.assertFalse(result["suppressed"])
        self.assertEqual(result["timersLeft"], 0)

    def test_a_pane_with_no_clock_is_never_left_mute(self):
        result = self._run_node(
            """
            const pane = {};
            const written = replies.quietWrite({
                pane,
                data: 'late bytes',
                write: () => {}
            });
            process.stdout.write(JSON.stringify({
                written, suppressed: replies.repliesSuppressed(pane)
            }));
            """
        )
        self.assertTrue(result["written"])
        self.assertFalse(result["suppressed"])

    def test_an_unsuppressed_pane_and_a_missing_pane_read_the_same(self):
        result = self._run_node(
            """
            process.stdout.write(JSON.stringify({
                nothing: replies.repliesSuppressed(null),
                fresh: replies.repliesSuppressed({}),
                explorer: replies.repliesSuppressed({ _paneType: 'explorer' })
            }));
            """
        )
        self.assertFalse(result["nothing"])
        self.assertFalse(result["fresh"])
        self.assertFalse(result["explorer"])


class PaneInputGateTestCase(TerminalRepliesNodeTestCase):
    """The rule as the page applies it: one pane's window, not the grid's."""

    def test_only_the_pane_being_flushed_loses_its_replies(self):
        result = self._run_node(
            """
            /* The page's forwarding, verbatim in behaviour: whatever `onData`
               produces goes to that pane's session unless the pane is inside a
               late write. */
            const paneA = { name: 'a' };
            const paneB = { name: 'b' };
            const sent = [];
            const forward = (pane, data) => {
                if (replies.repliesSuppressed(pane)) return;
                sent.push({ pane: pane.name, data });
            };

            const clock = fakeClock();
            let finish = null;
            replies.quietWrite({
                pane: paneA,
                data: 'late bytes',
                write: (payload, done) => {
                    finish = done;
                    /* The reply xterm produces from the stale query, and a
                       keystroke the reader lands in the other pane. */
                    forward(paneA, '\\u001b]11;rgb:0d0d/0d0d/0d0d\\u001b\\\\');
                    forward(paneB, 'ls');
                },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });
            finish();
            forward(paneA, 'ls');
            process.stdout.write(JSON.stringify({ sent }));
            """
        )
        # The stale reply never left the page; the neighbour pane typed
        # normally throughout, and the flushed pane types again once its own
        # window has closed.
        self.assertEqual(
            result["sent"],
            [
                {"pane": "b", "data": "ls"},
                {"pane": "a", "data": "ls"},
            ],
        )

    def test_suppression_follows_the_pane_when_its_slot_changes_hands(self):
        result = self._run_node(
            """
            const paneA = {};
            const paneB = {};
            const clock = fakeClock();
            let finish = null;
            replies.quietWrite({
                pane: paneA,
                data: 'late bytes',
                write: (payload, done) => { finish = done; },
                setTimeout: clock.setTimeout,
                clearTimeout: clock.clearTimeout
            });

            const whileSuppressed = replies.inputForwardPlan({
                pane: paneA,
                sessionId: 'A',
                activePanes: [paneB],
                activeSessionIds: ['B']
            });
            finish();
            const afterParse = replies.inputForwardPlan({
                pane: paneA,
                sessionId: 'A',
                activePanes: [paneB],
                activeSessionIds: ['B']
            });
            const replacement = replies.inputForwardPlan({
                pane: paneB,
                sessionId: 'B',
                activePanes: [paneB],
                activeSessionIds: ['B']
            });
            process.stdout.write(JSON.stringify({ whileSuppressed, afterParse, replacement }));
            """
        )
        self.assertFalse(result["whileSuppressed"]["send"])
        self.assertEqual(result["whileSuppressed"]["broadcastIndex"], -1)
        self.assertTrue(result["afterParse"]["send"])
        self.assertEqual(result["afterParse"]["sessionId"], "A")
        self.assertEqual(result["afterParse"]["broadcastIndex"], -1)
        self.assertTrue(result["replacement"]["send"])
        self.assertEqual(result["replacement"]["broadcastIndex"], 0)


# What xterm types back for each query it answers. A reply is dropped only
# when it is matched to a query of its own kind that the server read too long
# ago, so every pair here must match -- and must not match any other kind.
QUERY_REPLY_PAIRS = {
    "OSC 10 foreground": (ESC + "]10;?" + ST, ESC + "]10;rgb:e0e0/e0e0/e0e0" + ST),
    "OSC 11 background (BEL)": (ESC + "]11;?" + BEL, ESC + "]11;rgb:0d0d/0d0d/0d0d" + BEL),
    "OSC 12 cursor colour": (ESC + "]12;?" + ST, ESC + "]12;rgb:ffff/ffff/ffff" + ST),
    "OSC 4 indexed palette": (ESC + "]4;1;?" + ST, ESC + "]4;1;rgb:cdcd/0000/0000" + ST),
    "DA1": (ESC + "[c", ESC + "[?1;2c"),
    "DA2": (ESC + "[>c", ESC + "[>0;276;0c"),
    "DSR device status": (ESC + "[5n", ESC + "[0n"),
    "DSR cursor position": (ESC + "[6n", ESC + "[12;40R"),
    "DECXCPR": (ESC + "[?6n", ESC + "[?12;40;1R"),
    "XTVERSION": (ESC + "[>0q", ESC + "P>|xterm.js(5.5.0)" + ST),
    "DECRQM private": (ESC + "[?2026$p", ESC + "[?2026;2$y"),
    "OSC 52 clipboard read": (ESC + "]52;c;?" + ST, ESC + "]52;c;aGVsbG8=" + ST),
    "XTWINOPS window state": (ESC + "[11t", ESC + "[1t"),
    "XTWINOPS window position": (ESC + "[13t", ESC + "[3;0;0t"),
    "XTWINOPS pixel size": (ESC + "[14t", ESC + "[4;600;800t"),
    "XTWINOPS screen pixel size": (ESC + "[15t", ESC + "[5;1080;1920t"),
    "XTWINOPS cell size": (ESC + "[16t", ESC + "[6;17;9t"),
    "XTWINOPS text area size": (ESC + "[18t", ESC + "[8;24;80t"),
    "XTWINOPS screen size": (ESC + "[19t", ESC + "[9;60;200t"),
    "XTWINOPS icon label": (ESC + "[20t", ESC + "]Lgridvibe" + ST),
    "XTWINOPS window title": (ESC + "[21t", ESC + "]lgridvibe" + ST),
    "XTGETTCAP": (ESC + "P+q544e" + ST, ESC + "P1+r544e=787465726d" + ST),
    "DECRQSS": (ESC + "P$qm" + ST, ESC + "P1$r0m" + ST),
}


class ServerReplyAgeGateTestCase(unittest.TestCase):
    """A reply is refused by the server once its query is older than the budget.

    The page can only time what it held; the server reads the query off the pty
    and receives the answer, on one clock, so it sees the socket and a busy
    restoring page too. Codex waits about 100 ms for a colour reply.
    """

    def setUp(self):
        from web import terminal_replies

        self.replies = terminal_replies
        self.budget = terminal_replies.REPLY_AGE_BUDGET_S

    def ledger(self):
        return self.replies.ReplyLedger()

    def test_a_prompt_reply_reaches_the_program(self):
        for name, (query, reply) in QUERY_REPLY_PAIRS.items():
            with self.subTest(name):
                ledger = self.ledger()
                ledger.note_output("before" + query + "after", 100.0)
                self.assertEqual(ledger.filter_input(reply, 100.0 + self.budget / 2), reply)

    def test_a_late_reply_is_removed_and_the_typing_around_it_kept(self):
        for name, (query, reply) in QUERY_REPLY_PAIRS.items():
            with self.subTest(name):
                ledger = self.ledger()
                ledger.note_output(query, 100.0)
                self.assertEqual(
                    ledger.filter_input("ab" + reply + "cd", 100.0 + self.budget + 0.05),
                    "abcd",
                )

    def test_codex_restore_pair_is_removed_whole(self):
        ledger = self.ledger()
        ledger.note_output(ESC + "]10;?" + ST + ESC + "]11;?" + ST, 100.0)
        late = ESC + "]10;rgb:e0e0/e0e0/e0e0" + ST + ESC + "]11;rgb:0d0d/0d0d/0d0d" + ST
        self.assertEqual(ledger.filter_input(late, 100.3), "")

    def test_a_reply_shaped_key_with_no_query_outstanding_passes(self):
        ledger = self.ledger()
        shift_f3 = ESC + "[1;2R"
        self.assertEqual(ledger.filter_input(shift_f3, 100.0), shift_f3)
        ledger.note_output(ESC + "]11;?" + ST, 100.0)
        self.assertEqual(ledger.filter_input(shift_f3, 105.0), shift_f3)

    def test_each_query_answers_once(self):
        ledger = self.ledger()
        ledger.note_output(ESC + "[6n", 100.0)
        report = ESC + "[1;2R"
        self.assertEqual(ledger.filter_input(report, 105.0), "")
        # The same bytes again are a keystroke now: nothing is outstanding.
        self.assertEqual(ledger.filter_input(report, 105.1), report)

    def test_an_unanswered_old_query_does_not_make_a_fresh_reply_late(self):
        ledger = self.ledger()
        ledger.note_output(ESC + "]11;?" + ST, 100.0)  # stripped by the page
        ledger.note_output(ESC + "]11;?" + ST, 105.0)
        reply = ESC + "]11;rgb:0d0d/0d0d/0d0d" + ST
        self.assertEqual(ledger.filter_input(reply, 105.0 + self.budget / 2), reply)

    def test_a_reply_does_not_match_a_query_of_another_kind(self):
        ledger = self.ledger()
        ledger.note_output(ESC + "]10;?" + ST, 100.0)
        reply = ESC + "]11;rgb:0d0d/0d0d/0d0d" + ST
        self.assertEqual(ledger.filter_input(reply, 101.0), reply)

    def test_each_reply_is_matched_only_to_its_own_query(self):
        for asked, (query, _reply) in QUERY_REPLY_PAIRS.items():
            for answered, (_query, reply) in QUERY_REPLY_PAIRS.items():
                if asked == answered:
                    continue
                with self.subTest(asked=asked, answered=answered):
                    ledger = self.ledger()
                    ledger.note_output(query, 100.0)
                    self.assertEqual(ledger.filter_input(reply, 105.0), reply)

    def test_a_query_split_across_reads_is_recorded(self):
        query = ESC + "]11;?" + ST
        for cut in range(1, len(query)):
            with self.subTest(cut=cut):
                ledger = self.ledger()
                ledger.note_output("out" + query[:cut], 100.0)
                ledger.note_output(query[cut:] + "more", 100.0)
                reply = ESC + "]11;rgb:0d0d/0d0d/0d0d" + ST
                self.assertEqual(ledger.filter_input(reply, 101.0), "")

    def test_a_forgotten_query_no_longer_holds_back_a_key(self):
        ledger = self.ledger()
        ledger.note_output(ESC + "[6n", 100.0)
        later = 100.0 + self.replies.PENDING_QUERY_HORIZON_S + 1
        report = ESC + "[1;2R"
        self.assertEqual(ledger.filter_input(report, later), report)

    def test_rendering_output_records_nothing(self):
        ledger = self.ledger()
        ledger.note_output("".join(ACTION_FIXTURES.values()), 100.0)
        for name, (_query, reply) in QUERY_REPLY_PAIRS.items():
            with self.subTest(name):
                self.assertEqual(ledger.filter_input(reply, 105.0), reply)

    def test_the_pane_pump_and_input_path_share_the_ledger(self):
        from unittest import mock

        import web.terminal_io as terminal_io

        connection = {"kind": "local"}
        with mock.patch.object(terminal_io.time, "monotonic", return_value=100.0):
            terminal_io._decoded_terminal_output(
                "gate0001", connection, ESC + "]11;?" + ST
            )
        late = "x" + ESC + "]11;rgb:0d0d/0d0d/0d0d" + ST + "y"
        with mock.patch.object(terminal_io.time, "monotonic", return_value=100.5):
            self.assertEqual(terminal_io._sanitize_terminal_input(connection, late), "xy")


if __name__ == "__main__":
    unittest.main()
