"""What a Codex conversation is called, when the pane only announces its id.

A named Codex thread can publish nothing over OSC but its own UUID, so the
dashboard row said `New session · repo` about a conversation Codex's own
resume picker lists by name. `web/agent_conversations.py` is the lookup that
closes that gap, and every case here drives the shipped rule rather than
asserting on its source: the real parser over real app-server output, the real
schedule over a real clock, and the real bounded runner against a stand-in
server that behaves the way the real one does -- answering once and then
staying up.

What is pinned, and why each one is a way this could quietly go wrong:

- **Only a *whole* title is an id.** `Review OCR delegation (019d…)` is a name
  with an id in it and is already published as the name it is; resolving the
  id out of its middle would replace prose somebody chose with a lookup of the
  same conversation.
- **The command is the other source, and the one that answers for a resumed
  pane.** GridVibe applies the `thread-title` override to the built-in `codex`
  and to nothing else, so a pane running `codex resume <uuid>` announces its
  *project* -- the same string for every thread in that repo -- and the only
  thing that names the conversation is the line that started it.
- **A command-borne name is held to the pane, not to an announcement**, since
  there is no announcement to hold it to. That makes the two ways it can go
  stale its owner's job: the agent being retired, and the reader switching
  conversations inside the TUI, which nothing the pane prints would show.
- **An id is never published as a name**, however it arrives -- including back
  out of the provider's own name field.
- **The lookup stops at the answer, not at EOF.** The app server is a
  long-running process by design, so a runner that waited for it to exit would
  hang on every success.
- **Every failure is bounded and costs the pane its name and nothing else.**
  A server that never answers is killed rather than waited on, one that floods
  is cut off, and a binary that is not there is simply unavailable.
- **A thread with no name yet is asked again a few times and then left
  alone.** Codex names a thread from its opening turns, so the first answer is
  routinely "not yet" -- and an unbounded retry would be the poll this whole
  design exists to avoid.
- **A newer conversation supersedes that retry immediately.** A fresh thread's
  sleeping worker must not make the first `/resume` lose its one UUID
  announcement and wait for unrelated terminal output to try again.
- **The answer belongs to the announcement it answers for.** A retired
  connection cannot name its replacement, a pane that has since said something
  else is answered by that, and the title floor takes the resolved name with
  it.
"""

import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web import agent_conversations as conversations  # noqa: E402
from web import terminal_io as terminal  # noqa: E402
from web.agent_activity import (  # noqa: E402
    AGENT_EVENT_TITLE,
    apply_agent_events,
    apply_conversation_name,
    describe_agent_activity,
    mask_agent_titles,
)

THREAD_ID = "019d2e46-065b-7b22-aa9e-51bb915be2ff"
OTHER_THREAD_ID = "01a041fa-3a1a-71e3-8827-b75ec6aefe6f"
THREAD_NAME = "Review OCR delegation"
THREAD_PREVIEW = "Split your pane side by side using GridVibe MCP"

ESC = "\x1b"
BEL = "\x07"


def title_sequence(text: str) -> str:
    """One OSC 0 title announcement, exactly as a pane writes it."""
    return f"{ESC}]0;{text}{BEL}"


def answer_line(thread_id: str = THREAD_ID, name=THREAD_NAME, preview="") -> str:
    """One `thread/read` answer, in the shape the app server really sends."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": conversations.APP_SERVER_THREAD_READ_ID,
            "result": {
                "thread": {
                    "id": thread_id,
                    "name": name,
                    "preview": preview,
                    "cwd": "/srv/app",
                }
            },
        }
    )


#: A stand-in app server: it speaks the same newline-delimited JSON-RPC, and
#: like the real one it does not exit after answering. That is the point --
#: a runner that waited for the process would hang on every success.
STAND_IN_SERVER = '''
import json, sys, time

mode = sys.argv[1]
heartbeat = sys.argv[2] if len(sys.argv) > 2 else ""


def beat_forever():
    counter = 0
    while True:
        counter += 1
        if heartbeat:
            with open(heartbeat, "w", encoding="utf-8") as handle:
                handle.write(str(counter))
        time.sleep(0.05)


def emit(message):
    sys.stdout.write(json.dumps(message) + "\\n")
    sys.stdout.flush()


if mode == "silent":
    beat_forever()

for raw in sys.stdin:
    raw = raw.strip()
    if not raw:
        continue
    message = json.loads(raw)
    method = message.get("method")
    if method == "initialize":
        emit({"id": message["id"], "result": {"userAgent": "stand-in"}})
        emit({"method": "remoteControl/status/changed", "params": {"status": "disabled"}})
        continue
    if method != "thread/read":
        continue
    thread_id = message["params"]["threadId"]
    if mode == "flood":
        while True:
            sys.stdout.write("x" * 4096 + "\\n")
            sys.stdout.flush()
    if mode == "unknown":
        emit({"id": message["id"], "error": {"code": -32600, "message": "thread not loaded"}})
    elif mode == "unnamed":
        emit({"id": message["id"], "result": {"thread": {"id": thread_id, "name": None}}})
    else:
        emit({"id": message["id"], "result": {"thread": {"id": thread_id, "name": "Review OCR delegation"}}})
    beat_forever()
'''


class ConversationIdentityTestCase(unittest.TestCase):
    """What counts as an id, and what counts as a name."""

    def test_only_a_whole_title_is_a_thread_id(self):
        self.assertEqual(conversations.conversation_thread_id(THREAD_ID), THREAD_ID)
        self.assertEqual(
            conversations.conversation_thread_id(THREAD_ID.replace("-", "")), THREAD_ID
        )
        self.assertEqual(conversations.conversation_thread_id(f"  {THREAD_ID.upper()}  "), THREAD_ID)
        # A name with the id in it is a name, and is published as one.
        self.assertEqual(conversations.conversation_thread_id(f"{THREAD_NAME} ({THREAD_ID})"), "")
        self.assertEqual(conversations.conversation_thread_id("Review OCR delegation"), "")
        self.assertEqual(conversations.conversation_thread_id(""), "")

    def test_a_short_hex_run_is_prose_and_not_an_identity_to_resolve(self):
        """`agent-identity.js` refuses to *paint* 24 hex digits; resolving is a request."""
        self.assertEqual(conversations.conversation_thread_id("a" * 24), "")
        self.assertEqual(conversations.conversation_thread_id("deadbeefcafe"), "")

    def test_a_name_that_is_only_the_id_again_is_refused(self):
        self.assertEqual(conversations.normalize_conversation_name(THREAD_ID, THREAD_ID), "")
        self.assertEqual(conversations.normalize_conversation_name(OTHER_THREAD_ID), "")
        self.assertEqual(conversations.normalize_conversation_name(None), "")
        self.assertEqual(conversations.normalize_conversation_name("   "), "")

    def test_a_name_is_flattened_and_bounded_like_any_other_row_label(self):
        self.assertEqual(
            conversations.normalize_conversation_name("Review\tOCR   delegation\n"),
            "Review OCR delegation",
        )
        long_name = conversations.normalize_conversation_name("n" * 500)
        self.assertLessEqual(len(long_name), 161)
        self.assertTrue(long_name.endswith("…"))


class ConversationCommandTestCase(unittest.TestCase):
    """Reading the conversation out of the command that started the agent."""

    def test_a_typed_resume_names_its_conversation(self):
        """The reported case: `codex resume <uuid>` typed at the prompt."""
        self.assertEqual(
            conversations.command_thread_id(f"codex resume {THREAD_ID}"), THREAD_ID
        )

    def test_the_id_is_found_past_the_options_in_front_of_it(self):
        self.assertEqual(
            conversations.command_thread_id(
                f"codex -c tui.terminal_title=['thread-title'] resume {THREAD_ID}"
            ),
            THREAD_ID,
        )
        self.assertEqual(
            conversations.command_thread_id(
                f'sudo codex.cmd resume {THREAD_ID.upper()} "fix the parser"'
            ),
            THREAD_ID,
        )

    def test_a_command_that_does_not_name_an_exact_conversation_names_none(self):
        """Each of these is a real Codex form, and none of them says *which*."""
        for command in (
            "codex resume --last",
            "codex resume",
            "codex",
            'codex "fix the parser"',
            "codex resume my-session-name",
            f"codex fork {THREAD_ID}",
            f"claude resume {THREAD_ID}",
            f"codex-wrapper resume {THREAD_ID}",
            "",
            'codex resume "unbalanced',
        ):
            with self.subTest(command=command):
                self.assertEqual(conversations.command_thread_id(command), "")

    def test_the_launch_gridvibe_composes_itself_names_no_conversation(self):
        """A built-in `codex` pane is answered by its announcement, not here."""
        self.assertEqual(
            conversations.command_thread_id(
                "codex -c \"tui.terminal_title=['thread-title']\" --sandbox workspace-write"
            ),
            "",
        )


class ThreadReadExchangeTestCase(unittest.TestCase):
    """The three lines out, and the one answer read back."""

    def test_the_request_is_a_handshake_an_ack_and_one_metadata_read(self):
        lines = [
            json.loads(line)
            for line in conversations.thread_read_request_text(THREAD_ID).splitlines()
        ]

        self.assertEqual(
            [line.get("method") for line in lines],
            ["initialize", "initialized", "thread/read"],
        )
        self.assertEqual(lines[2]["params"], {"threadId": THREAD_ID})
        # Metadata only: hydrating a thread's history to learn its name would
        # be a different operation with a different cost.
        self.assertNotIn("includeTurns", lines[2]["params"])
        self.assertEqual(lines[2]["id"], conversations.APP_SERVER_THREAD_READ_ID)
        self.assertIsNone(lines[1].get("id"))

    def test_a_named_thread_answers_with_its_name(self):
        self.assertEqual(
            conversations.parse_thread_read_output(
                answer_line(preview=THREAD_PREVIEW), THREAD_ID
            ),
            (conversations.LOOKUP_NAMED, THREAD_NAME),
        )

    def test_an_unnamed_thread_answers_with_its_preview(self):
        """Resume history still has a useful label when no explicit name was set."""
        self.assertEqual(
            conversations.parse_thread_read_output(
                answer_line(name=None, preview=THREAD_PREVIEW), THREAD_ID
            ),
            (conversations.LOOKUP_NAMED, THREAD_PREVIEW),
        )

    def test_a_thread_with_no_name_is_unnamed_and_not_unavailable(self):
        """Two different answers: one means "ask again later", one means "cannot"."""
        self.assertEqual(
            conversations.parse_thread_read_output(answer_line(name=None), THREAD_ID),
            (conversations.LOOKUP_UNNAMED, ""),
        )

    def test_a_provider_that_names_a_thread_after_its_own_id_publishes_nothing(self):
        self.assertEqual(
            conversations.parse_thread_read_output(answer_line(name=THREAD_ID), THREAD_ID),
            (conversations.LOOKUP_UNNAMED, ""),
        )

    def test_a_preview_that_is_only_the_thread_id_is_not_published(self):
        self.assertEqual(
            conversations.parse_thread_read_output(
                answer_line(name=None, preview=THREAD_ID), THREAD_ID
            ),
            (conversations.LOOKUP_UNNAMED, ""),
        )

    def test_an_error_answer_is_unknown(self):
        error = json.dumps(
            {
                "id": conversations.APP_SERVER_THREAD_READ_ID,
                "error": {"code": -32600, "message": "thread not loaded"},
            }
        )
        self.assertEqual(
            conversations.parse_thread_read_output(error, THREAD_ID),
            (conversations.LOOKUP_UNKNOWN, ""),
        )

    def test_another_threads_metadata_answers_nothing_about_this_pane(self):
        self.assertEqual(
            conversations.parse_thread_read_output(answer_line(OTHER_THREAD_ID), THREAD_ID),
            (conversations.LOOKUP_UNKNOWN, ""),
        )

    def test_notifications_and_shell_noise_around_the_answer_are_skipped(self):
        stream = "\n".join(
            [
                "bash: line 1: warning from somebody's profile",
                json.dumps({"id": 1, "result": {"userAgent": "codex"}}),
                json.dumps({"method": "remoteControl/status/changed", "params": {}}),
                "",
                answer_line(),
            ]
        )
        self.assertEqual(
            conversations.parse_thread_read_output(stream, THREAD_ID),
            (conversations.LOOKUP_NAMED, THREAD_NAME),
        )

    def test_a_stream_that_never_answers_is_unavailable(self):
        self.assertEqual(
            conversations.parse_thread_read_output("not json at all\n{}\n", THREAD_ID),
            (conversations.LOOKUP_UNAVAILABLE, ""),
        )
        self.assertEqual(
            conversations.parse_thread_read_output("", THREAD_ID),
            (conversations.LOOKUP_UNAVAILABLE, ""),
        )

    def test_the_reader_settles_at_the_answer_and_reads_no_further(self):
        reader = conversations.ThreadReadReader(THREAD_ID)

        self.assertFalse(reader.feed_line(json.dumps({"method": "thread/started"})))
        self.assertTrue(reader.feed_line(answer_line()))
        # Whatever the server goes on saying belongs to nobody's question.
        self.assertTrue(reader.feed_line(answer_line(name="Renamed later")))
        self.assertEqual(reader.answer(), (conversations.LOOKUP_NAMED, THREAD_NAME))


class ConversationScheduleTestCase(unittest.TestCase):
    """What is remembered, and when it is worth asking again."""

    def test_the_schedule_is_a_few_tries_further_apart_and_then_a_stop(self):
        delays = (5.0, 15.0)

        self.assertEqual(conversations.retry_delay(0, delays), 5.0)
        self.assertEqual(conversations.retry_delay(1, delays), 15.0)
        self.assertIsNone(conversations.retry_delay(2, delays))

    def test_the_shipped_schedule_widens_and_ends(self):
        """Codex names a thread from its opening turns, so the shape that
        matters is "soon, then later, then stop" rather than the seconds."""
        delays = conversations.CONVERSATION_RETRY_DELAYS
        waits = [conversations.retry_delay(index, delays) for index in range(len(delays) + 1)]

        self.assertIsNone(waits[-1])
        self.assertEqual(waits[:-1], sorted(waits[:-1]))
        self.assertTrue(all(wait > 0 for wait in waits[:-1]))

    def test_a_thread_that_had_no_name_is_not_remembered_as_unnamed(self):
        """The staleness this avoids: a thread unnamed at nine is named by ten.

        Only found names are cached, so the pane that asks tomorrow runs its
        own schedule instead of inheriting this morning's silence.
        """
        cache = conversations.ConversationNameCache()
        key = ("codex", "local", THREAD_ID)

        self.assertEqual(cache.resolved_name(key), "")
        cache.remember(key, "")

        self.assertEqual(cache.resolved_name(key), "")

    def test_a_name_is_remembered_so_the_next_pane_asks_nobody(self):
        cache = conversations.ConversationNameCache()
        key = ("codex", "local", THREAD_ID)

        cache.remember(key, THREAD_NAME)

        self.assertEqual(cache.resolved_name(key), THREAD_NAME)
        self.assertEqual(cache.resolved_name(("codex", "ssh:dev@box:22", THREAD_ID)), "")

    def test_the_cache_is_bounded_and_drops_what_was_read_longest_ago(self):
        cache = conversations.ConversationNameCache(max_entries=2)
        first, second, third = [("codex", "local", str(index)) for index in range(3)]
        cache.remember(first, "name 0")
        cache.remember(second, "name 1")

        cache.resolved_name(first)  # reading it is what keeps it
        cache.remember(third, "name 2")

        self.assertEqual(cache.resolved_name(first), "name 0")
        self.assertEqual(cache.resolved_name(third), "name 2")
        self.assertEqual(cache.resolved_name(second), "")

    def test_one_answer_can_be_forgotten_without_clearing_the_rest(self):
        cache = conversations.ConversationNameCache()
        key = ("codex", "local", THREAD_ID)
        other = ("codex", "local", OTHER_THREAD_ID)
        cache.remember(key, THREAD_NAME)
        cache.remember(other, "Other")

        cache.forget(key)

        self.assertEqual(cache.resolved_name(key), "")
        self.assertEqual(cache.resolved_name(other), "Other")

        cache.clear()
        self.assertEqual(cache.resolved_name(other), "")


class ConversationTargetTestCase(unittest.TestCase):
    """Where the lookup runs, which is wherever the pane's shell runs."""

    def test_a_local_pane_is_asked_on_this_machine(self):
        target = conversations.local_probe_target(
            "codex", shell_kind="powershell", resolved_binary="C:\\npm\\codex.CMD"
        )

        self.assertEqual(target["kind"], "local")
        self.assertEqual(target["environment"], "local")
        self.assertEqual(target["argv"], ["C:\\npm\\codex.CMD", "app-server"])

    def test_a_wsl_pane_is_asked_inside_its_own_distribution(self):
        target = conversations.local_probe_target(
            "codex", shell_kind="wsl", distribution="Ubuntu-22.04", wsl_executable="wsl.exe"
        )

        self.assertEqual(target["argv"][:4], ["wsl.exe", "--distribution", "Ubuntu-22.04", "--exec"])
        self.assertIn("app-server", target["argv"][-1])
        # Two distros are two machines as far as a thread store is concerned.
        self.assertEqual(target["environment"], "wsl:ubuntu-22.04")
        self.assertNotEqual(
            target["environment"],
            conversations.local_probe_target(
                "codex", shell_kind="wsl", distribution="Debian", wsl_executable="wsl.exe"
            )["environment"],
        )

    def test_a_pane_with_nowhere_to_ask_describes_no_lookup(self):
        self.assertEqual(
            conversations.local_probe_target("codex", shell_kind="wsl", wsl_executable=""), {}
        )
        self.assertEqual(
            conversations.local_probe_target("", shell_kind="cmd", resolved_binary=""), {}
        )
        self.assertEqual(conversations.remote_probe_target(None, "codex"), {})
        self.assertEqual(conversations.remote_probe_target(object(), ""), {})

    def test_an_ssh_pane_is_asked_over_its_own_transport(self):
        transport = object()
        target = conversations.remote_probe_target(
            transport, "codex", username="dev", host="box", port=2222
        )

        self.assertEqual(target["kind"], "ssh")
        self.assertIs(target["transport"], transport)
        self.assertIn("app-server", target["command"])
        self.assertTrue(target["command"].startswith("sh -lc "))
        # The account as well as the host: two accounts own two thread stores.
        self.assertEqual(target["environment"], "ssh:dev@box:2222")

    def test_the_cache_key_is_the_provider_the_environment_and_the_thread(self):
        local = conversations.local_probe_target("codex", resolved_binary="codex")
        remote = conversations.remote_probe_target(object(), "codex", username="dev", host="box")

        self.assertNotEqual(
            conversations.cache_key("codex", local, THREAD_ID),
            conversations.cache_key("codex", remote, THREAD_ID),
        )
        self.assertEqual(
            conversations.cache_key("codex", local, THREAD_ID), ("codex", "local", THREAD_ID)
        )


class LocalProbeRunnerTestCase(unittest.TestCase):
    """The bounded runner, against a server that behaves like the real one."""

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.script = Path(self.temp.name) / "stand_in_app_server.py"
        self.script.write_text(STAND_IN_SERVER, encoding="utf-8")
        self.heartbeat = Path(self.temp.name) / "alive"

    def _argv(self, mode: str, heartbeat: bool = False):
        argv = [sys.executable, "-u", str(self.script), mode]
        if heartbeat:
            argv.append(str(self.heartbeat))
        return argv

    def _probe(self, mode: str, heartbeat: bool = False, timeout: float = 15.0, **kwargs):
        return conversations.run_local_probe(
            self._argv(mode, heartbeat),
            conversations.thread_read_request_text(THREAD_ID),
            THREAD_ID,
            timeout=timeout,
            **kwargs,
        )

    def test_a_real_exchange_answers_with_the_thread_name(self):
        self.assertEqual(self._probe("named"), (conversations.LOOKUP_NAMED, THREAD_NAME))

    def test_a_server_that_answers_and_stays_up_does_not_hold_the_lookup(self):
        """The real app server never exits; stopping at the answer is the rule."""
        started = time.monotonic()
        outcome, name = self._probe("named", timeout=15.0)

        self.assertEqual((outcome, name), (conversations.LOOKUP_NAMED, THREAD_NAME))
        self.assertLess(time.monotonic() - started, 10.0)

    def test_an_unnamed_and_an_unknown_thread_are_told_apart(self):
        self.assertEqual(self._probe("unnamed"), (conversations.LOOKUP_UNNAMED, ""))
        self.assertEqual(self._probe("unknown"), (conversations.LOOKUP_UNKNOWN, ""))

    def test_a_server_that_never_answers_is_killed_rather_than_waited_on(self):
        started = time.monotonic()
        outcome, _ = self._probe("silent", heartbeat=True, timeout=1.0)
        elapsed = time.monotonic() - started

        self.assertEqual(outcome, conversations.LOOKUP_UNAVAILABLE)
        # The stand-in would beat forever; the only way this returned is the
        # bound, and the only way the beating stopped is the tree kill.
        self.assertLess(elapsed, 8.0)
        self.assertTrue(self.heartbeat.exists())
        first = self.heartbeat.read_text(encoding="utf-8")
        time.sleep(0.6)
        self.assertEqual(self.heartbeat.read_text(encoding="utf-8"), first)

    def test_a_server_that_floods_is_cut_off_at_the_output_ceiling(self):
        started = time.monotonic()
        outcome, _ = self._probe("flood", timeout=15.0, max_output_bytes=16 * 1024)

        self.assertEqual(outcome, conversations.LOOKUP_UNAVAILABLE)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_a_binary_that_is_not_there_costs_the_name_and_nothing_else(self):
        outcome, name = conversations.run_local_probe(
            [str(Path(self.temp.name) / "no-such-codex")],
            conversations.thread_read_request_text(THREAD_ID),
            THREAD_ID,
            timeout=2.0,
        )

        self.assertEqual((outcome, name), (conversations.LOOKUP_UNAVAILABLE, ""))

    def test_a_target_naming_no_command_runs_nothing(self):
        self.assertEqual(
            conversations.probe_conversation_name({"kind": "local", "argv": []}, THREAD_ID),
            (conversations.LOOKUP_UNAVAILABLE, ""),
        )
        self.assertEqual(
            conversations.probe_conversation_name({}, THREAD_ID),
            (conversations.LOOKUP_UNAVAILABLE, ""),
        )


class _FakeChannel:
    """A paramiko exec channel, reduced to what the runner actually uses."""

    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.command = ""
        self.sent = b""
        self.closed = False
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def exec_command(self, command):
        self.command = command

    def sendall(self, data):
        self.sent += data

    def recv(self, size):
        if not self.chunks:
            raise socket.timeout()
        return self.chunks.pop(0)[:size]

    def close(self):
        self.closed = True


class _FakeTransport:
    def __init__(self, channel, active=True):
        self.channel = channel
        self.active = active
        self.sessions = 0

    def is_active(self):
        return self.active

    def open_session(self, timeout=None):
        self.sessions += 1
        return self.channel


class RemoteProbeRunnerTestCase(unittest.TestCase):
    """The same bounds, with a channel standing in for the process tree."""

    def _run(self, chunks, active=True, **kwargs):
        channel = _FakeChannel(chunks)
        transport = _FakeTransport(channel, active=active)
        outcome = conversations.run_remote_probe(
            transport,
            "sh -lc 'exec codex app-server'",
            conversations.thread_read_request_text(THREAD_ID),
            THREAD_ID,
            timeout=2.0,
            **kwargs,
        )
        return outcome, channel, transport

    def test_a_remote_exchange_answers_over_one_extra_channel(self):
        answer = (answer_line() + "\n").encode("utf-8")
        outcome, channel, transport = self._run([b'{"id":1,"result":{}}\n', answer])

        self.assertEqual(outcome, (conversations.LOOKUP_NAMED, THREAD_NAME))
        # One extra channel, never the interactive one somebody is working in.
        self.assertEqual(transport.sessions, 1)
        self.assertEqual(channel.command, "sh -lc 'exec codex app-server'")
        self.assertIn(b"thread/read", channel.sent)
        self.assertTrue(channel.closed)

    def test_an_answer_split_across_two_reads_still_lands(self):
        raw = (answer_line() + "\n").encode("utf-8")
        outcome, _, _ = self._run([raw[:40], raw[40:]])

        self.assertEqual(outcome, (conversations.LOOKUP_NAMED, THREAD_NAME))

    def test_a_channel_that_says_nothing_costs_the_name_and_closes(self):
        outcome, channel, _ = self._run([])

        self.assertEqual(outcome, (conversations.LOOKUP_UNAVAILABLE, ""))
        self.assertTrue(channel.closed)

    def test_a_transport_that_is_no_longer_active_opens_no_channel(self):
        outcome, _, transport = self._run([b"anything"], active=False)

        self.assertEqual(outcome, (conversations.LOOKUP_UNAVAILABLE, ""))
        self.assertEqual(transport.sessions, 0)

    def test_a_flooding_host_is_cut_off_at_the_output_ceiling(self):
        outcome, channel, _ = self._run([b"x" * 4096] * 8, max_output_bytes=8 * 1024)

        self.assertEqual(outcome, (conversations.LOOKUP_UNAVAILABLE, ""))
        self.assertTrue(channel.closed)


class ConversationReadingTestCase(unittest.TestCase):
    """How a resolved name joins the reading the dashboard publishes."""

    def setUp(self):
        self.now = 1000.0
        self.record = apply_agent_events(None, [(AGENT_EVENT_TITLE, THREAD_ID)], self.now)
        self.conversation = {"provider": "codex", "title": THREAD_ID, "name": THREAD_NAME}

    def test_a_resolved_name_is_published_beside_the_announcement_it_answers(self):
        reading = describe_agent_activity(
            apply_conversation_name(self.record, self.conversation), self.now
        )

        self.assertEqual(reading["conversation_title"], THREAD_NAME)
        # Three separate facts: the raw announcement is still the raw one.
        self.assertEqual(reading["title"], THREAD_ID)

    def test_a_pane_that_has_since_said_something_else_answers_for_itself(self):
        renamed = apply_agent_events(
            self.record, [(AGENT_EVENT_TITLE, "Fix the parser")], self.now + 5
        )

        reading = describe_agent_activity(
            apply_conversation_name(renamed, self.conversation), self.now + 5
        )

        self.assertEqual(reading["conversation_title"], "")
        self.assertEqual(reading["title"], "Fix the parser")

    def test_the_title_floor_takes_the_resolved_name_with_it(self):
        masked = mask_agent_titles(self.record, self.now + 1)

        reading = describe_agent_activity(
            apply_conversation_name(masked, self.conversation), self.now + 1
        )

        self.assertEqual(reading["conversation_title"], "")
        self.assertEqual(reading["title"], "")

    def test_a_pane_with_no_resolved_name_reads_exactly_as_it_did(self):
        unchanged = apply_conversation_name(self.record, None)

        self.assertIs(unchanged, self.record)
        self.assertEqual(describe_agent_activity(self.record, self.now)["conversation_title"], "")

    def test_a_command_borne_name_is_held_to_the_pane_and_not_to_a_title(self):
        """A resumed pane announces its project, so there is no announcement
        to hold the name to -- and a title change says nothing about which
        conversation the pane is in."""
        from_command = {"provider": "codex", "name": THREAD_NAME}
        project = apply_agent_events(None, [(AGENT_EVENT_TITLE, "gridvibe_colab")], self.now)

        reading = describe_agent_activity(
            apply_conversation_name(project, from_command), self.now
        )

        self.assertEqual(reading["conversation_title"], THREAD_NAME)
        self.assertEqual(reading["title"], "gridvibe_colab")

        renamed = apply_agent_events(
            project, [(AGENT_EVENT_TITLE, "other_project")], self.now + 5
        )
        self.assertEqual(
            describe_agent_activity(
                apply_conversation_name(renamed, from_command), self.now + 5
            )["conversation_title"],
            THREAD_NAME,
        )

    def test_a_name_that_never_resolved_is_not_attached(self):
        empty = apply_conversation_name(self.record, {"title": THREAD_ID, "name": ""})

        self.assertIs(empty, self.record)


class ConversationObservationTestCase(unittest.TestCase):
    """The stream side: who is asked about, once, and who owns the answer."""

    def setUp(self):
        self.registry = {}
        context = patch.object(terminal, "ssh_connections", self.registry)
        context.start()
        self.addCleanup(context.stop)
        context = patch.object(terminal, "session_manager")
        self.session_manager = context.start()
        self.addCleanup(context.stop)
        self.session = SimpleNamespace(
            session_id="pane",
            mode="local",
            startup_mode="agent",
            agent_selection="codex",
            custom_agent="",
            use_wsl=False,
            use_powershell=False,
            distribution="",
            directory="C:\\repos\\gridvibe",
        )
        self.session_manager.get_session.return_value = self.session
        self.connection = {"kind": "local", "shell_kind": "cmd"}
        self.registry["pane"] = self.connection
        terminal.conversation_names.clear()
        self.addCleanup(terminal.conversation_names.clear)
        self.started = []
        context = patch.object(
            terminal,
            "_start_conversation_resolver",
            side_effect=lambda session_id, connection, title, thread_id: (
                self.started.append((session_id, title, thread_id)) or True
            ),
        )
        context.start()
        self.addCleanup(context.stop)

    def observe(self, chunk, connection=None):
        terminal._observe_agent_activity("pane", connection or self.connection, chunk)

    def test_a_new_uuid_announcement_arms_exactly_one_lookup(self):
        self.observe(title_sequence(THREAD_ID))
        # Everything the pane paints afterwards re-asserts the same title.
        for _ in range(5):
            self.observe(title_sequence(THREAD_ID))
            self.observe("working…\r\n")

        self.assertEqual(self.started, [("pane", THREAD_ID, THREAD_ID)])

    def test_a_second_conversation_arms_a_second_lookup(self):
        self.observe(title_sequence(THREAD_ID))
        self.observe(title_sequence(OTHER_THREAD_ID))

        self.assertEqual(
            [entry[2] for entry in self.started], [THREAD_ID, OTHER_THREAD_ID]
        )

    def test_a_pane_announcing_prose_arms_nothing(self):
        self.observe(title_sequence("Review OCR delegation"))
        self.observe(title_sequence(f"{THREAD_NAME} ({THREAD_ID})"))
        self.observe("just output\r\n")

        self.assertEqual(self.started, [])

    def test_a_pane_that_is_not_a_codex_agent_arms_nothing(self):
        self.session.agent_selection = "claude"
        self.observe(title_sequence(THREAD_ID))

        self.session.agent_selection = "codex"
        self.session.startup_mode = "terminal"
        self.observe(title_sequence(OTHER_THREAD_ID))

        self.assertEqual(self.started, [])

    def test_a_pane_whose_lookup_could_not_start_re_arms_later(self):
        with patch.object(terminal, "_start_conversation_resolver", return_value=False):
            self.observe(title_sequence(THREAD_ID))
            self.observe(title_sequence(THREAD_ID))

        # The announcement has not changed, so only the deferral can re-arm it.
        self.connection["agent_conversation_deferred_until"] = time.time() - 1
        self.observe(title_sequence(THREAD_ID))

        self.assertEqual(self.started, [("pane", THREAD_ID, THREAD_ID)])


class ConversationCommandObservationTestCase(unittest.TestCase):
    """The second trigger: the command, and the lines that take it away."""

    def setUp(self):
        self.registry = {}
        context = patch.object(terminal, "ssh_connections", self.registry)
        context.start()
        self.addCleanup(context.stop)
        self.connection = {"kind": "local", "shell_kind": "powershell"}
        self.registry["pane"] = self.connection
        self.session = SimpleNamespace(
            session_id="pane", startup_mode="agent", agent_selection="codex"
        )
        terminal.conversation_names.clear()
        self.addCleanup(terminal.conversation_names.clear)
        self.started = []
        context = patch.object(
            terminal,
            "_start_conversation_resolver",
            side_effect=lambda session_id, connection, title, thread_id, **kwargs: (
                self.started.append((title, thread_id, kwargs)) or True
            ),
        )
        context.start()
        self.addCleanup(context.stop)

    def test_a_resume_command_arms_a_lookup_bound_to_no_announcement(self):
        terminal._note_agent_conversation_command(
            "pane", self.connection, f"codex resume {THREAD_ID}"
        )

        self.assertEqual(len(self.started), 1)
        title, thread_id, _ = self.started[0]
        self.assertIsNone(title)
        self.assertEqual(thread_id, THREAD_ID)
        # Kept for a later rename to re-read; it never leaves the connection.
        self.assertEqual(
            self.connection["agent_conversation_command"], f"codex resume {THREAD_ID}"
        )

    def test_a_command_that_names_no_conversation_arms_nothing(self):
        for command in ("codex", "codex resume --last", ""):
            terminal._note_agent_conversation_command("pane", self.connection, command)

        self.assertEqual(self.started, [])
        self.assertNotIn("agent_conversation_command", self.connection)

    def test_switching_conversations_in_the_tui_drops_the_name_and_the_identity(self):
        """Nothing the pane prints would show this, so the line is all there is."""
        for verb in sorted(terminal.CONVERSATION_SWITCH_COMMANDS):
            with self.subTest(verb=verb):
                terminal._note_agent_conversation_command(
                    "pane", self.connection, f"codex resume {THREAD_ID}"
                )
                terminal._publish_agent_conversation(
                    "pane", self.connection, None, THREAD_NAME
                )

                terminal._note_agent_conversation_switch(
                    "pane", self.connection, self.session, verb
                )

                self.assertNotIn("agent_conversation", self.connection)
                self.assertNotIn("agent_conversation_command", self.connection)

    def test_switching_conversations_cancels_the_fresh_threads_retry(self):
        cancellation = threading.Event()
        terminal._cancel_conversation_resolver("pane")
        with terminal._conversation_resolver_lock:
            terminal._conversation_resolvers["pane"] = cancellation
        self.addCleanup(terminal._cancel_conversation_resolver, "pane")

        terminal._note_agent_conversation_switch(
            "pane", self.connection, self.session, "/resume"
        )

        self.assertTrue(cancellation.is_set())
        self.assertNotIn("pane", terminal._conversation_resolvers)

    def test_ordinary_conversation_input_takes_nothing_away(self):
        terminal._note_agent_conversation_command(
            "pane", self.connection, f"codex resume {THREAD_ID}"
        )
        terminal._publish_agent_conversation("pane", self.connection, None, THREAD_NAME)

        for line in ("/status", "/diff", "make the new chat window wider", "/newsletter"):
            terminal._note_agent_conversation_switch(
                "pane", self.connection, self.session, line
            )

        self.assertEqual(self.connection["agent_conversation"]["name"], THREAD_NAME)

    def test_another_agents_pane_is_left_alone(self):
        self.session.agent_selection = "claude"
        terminal._publish_agent_conversation("pane", self.connection, None, THREAD_NAME)

        terminal._note_agent_conversation_switch(
            "pane", self.connection, self.session, "/new"
        )

        self.assertEqual(self.connection["agent_conversation"]["name"], THREAD_NAME)

    def test_a_rename_keeps_the_conversation_and_asks_again_for_its_name(self):
        terminal._note_agent_conversation_command(
            "pane", self.connection, f"codex resume {THREAD_ID}"
        )
        terminal._publish_agent_conversation("pane", self.connection, None, "Old name")
        self.started.clear()
        target = {"kind": "local", "environment": "local", "argv": ["codex", "app-server"]}
        key = conversations.cache_key("codex", target, THREAD_ID)
        terminal.conversation_names.remember(key, "Old name")

        with patch.object(terminal, "_conversation_probe_target", return_value=target):
            terminal._note_agent_conversation_switch(
                "pane", self.connection, self.session, "/rename Something better"
            )

        # The pane is in the same conversation, so the identity stays.
        self.assertEqual(
            self.connection["agent_conversation_command"], f"codex resume {THREAD_ID}"
        )
        self.assertNotIn("agent_conversation", self.connection)
        self.assertEqual(terminal.conversation_names.resolved_name(key), "")
        self.assertEqual(len(self.started), 1)
        _, thread_id, kwargs = self.started[0]
        self.assertEqual(thread_id, THREAD_ID)
        # And it will not answer with the name it just dropped, which is what
        # a rename the reader is still typing would otherwise hand back.
        self.assertEqual(kwargs.get("reject_name"), "Old name")
        self.assertGreater(kwargs.get("start_delay"), 0)


class ConversationPublicationTestCase(unittest.TestCase):
    """Who may name a pane, and for how long."""

    def setUp(self):
        self.registry = {}
        context = patch.object(terminal, "ssh_connections", self.registry)
        context.start()
        self.addCleanup(context.stop)
        self.connection = {"kind": "local", "shell_kind": "cmd"}
        self.registry["pane"] = self.connection
        self.connection["agent_activity"] = apply_agent_events(
            None, [(AGENT_EVENT_TITLE, THREAD_ID)], time.time()
        )

    def snapshot_name(self):
        return describe_agent_activity(
            terminal.agent_activity_snapshot()["pane"], time.time()
        )["conversation_title"]

    def test_a_resolved_name_reaches_the_reading_the_dashboard_publishes(self):
        self.assertTrue(
            terminal._publish_agent_conversation("pane", self.connection, THREAD_ID, THREAD_NAME)
        )

        self.assertEqual(self.snapshot_name(), THREAD_NAME)

    def test_a_retired_connection_cannot_name_its_replacement(self):
        replacement = {"kind": "local", "shell_kind": "cmd"}
        replacement["agent_activity"] = self.connection["agent_activity"]
        self.registry["pane"] = replacement

        self.assertFalse(
            terminal._publish_agent_conversation("pane", self.connection, THREAD_ID, THREAD_NAME)
        )
        self.assertEqual(self.snapshot_name(), "")

    def test_an_announcement_the_pane_has_replaced_is_still_current_for_nobody(self):
        self.assertTrue(
            terminal._conversation_announcement_is_current("pane", self.connection, THREAD_ID)
        )

        self.connection["agent_activity"] = apply_agent_events(
            self.connection["agent_activity"], [(AGENT_EVENT_TITLE, "Fix the parser")], time.time()
        )

        self.assertFalse(
            terminal._conversation_announcement_is_current("pane", self.connection, THREAD_ID)
        )

    def test_a_command_borne_record_carries_no_announcement_and_no_id(self):
        terminal._publish_agent_conversation("pane", self.connection, None, THREAD_NAME)

        record = self.connection["agent_conversation"]
        self.assertNotIn("title", record)
        self.assertEqual(record["name"], THREAD_NAME)
        # The id is never carried anywhere a payload could reach.
        self.assertNotIn(THREAD_ID, str(record))
        self.assertEqual(self.snapshot_name(), THREAD_NAME)

    def test_the_resolved_name_is_dropped_when_the_agent_is_retired(self):
        terminal._publish_agent_conversation("pane", self.connection, THREAD_ID, THREAD_NAME)
        self.assertEqual(self.snapshot_name(), THREAD_NAME)

        terminal._forget_agent_conversation(self.connection)

        self.assertEqual(self.snapshot_name(), "")
        self.assertNotIn("agent_conversation", self.connection)
        self.assertNotIn("agent_conversation_command", self.connection)


class ConversationResolutionTestCase(unittest.TestCase):
    """The lookup loop itself: a miss, a wait, and the name that arrives."""

    def setUp(self):
        terminal._cancel_conversation_resolver("pane")
        self.addCleanup(terminal._cancel_conversation_resolver, "pane")
        self.registry = {}
        context = patch.object(terminal, "ssh_connections", self.registry)
        context.start()
        self.addCleanup(context.stop)
        context = patch.object(terminal, "session_manager")
        self.session_manager = context.start()
        self.addCleanup(context.stop)
        self.session = SimpleNamespace(
            session_id="pane", startup_mode="agent", agent_selection="codex"
        )
        self.session_manager.get_session.return_value = self.session
        self.connection = {"kind": "local", "shell_kind": "cmd"}
        self.connection["agent_activity"] = apply_agent_events(
            None, [(AGENT_EVENT_TITLE, THREAD_ID)], time.time()
        )
        self.registry["pane"] = self.connection
        self.target = {"kind": "local", "environment": "local", "argv": ["codex", "app-server"]}
        context = patch.object(terminal, "_conversation_probe_target", return_value=self.target)
        context.start()
        self.addCleanup(context.stop)
        self.cache = conversations.ConversationNameCache()
        context = patch.object(terminal, "conversation_names", self.cache)
        context.start()
        self.addCleanup(context.stop)
        # The published schedule with its clock wound down: the shape under
        # test is "a few tries, further apart, then stop", not the seconds.
        context = patch.object(
            terminal, "retry_delay", side_effect=lambda attempt: (0.01, 0.02)[attempt]
            if attempt < 2 else None
        )
        context.start()
        self.addCleanup(context.stop)
        self.answers = []
        self.asked = []

    def _probe(self, target, thread_id, **kwargs):
        self.asked.append(thread_id)
        return self.answers.pop(0) if self.answers else (conversations.LOOKUP_UNNAMED, "")

    def resolve(self, title=THREAD_ID):
        with patch.object(terminal, "probe_conversation_name", side_effect=self._probe):
            terminal._resolve_agent_conversation("pane", self.connection, title, THREAD_ID)

    def published(self):
        return (self.connection.get("agent_conversation") or {}).get("name", "")

    def test_a_name_that_arrives_after_two_misses_is_published(self):
        self.answers = [
            (conversations.LOOKUP_UNNAMED, ""),
            (conversations.LOOKUP_UNKNOWN, ""),
            (conversations.LOOKUP_NAMED, THREAD_NAME),
        ]

        self.resolve()

        self.assertEqual(len(self.asked), 3)
        self.assertEqual(self.published(), THREAD_NAME)
        # And the next pane on the same thread asks nobody.
        self.assertEqual(
            self.cache.resolved_name(conversations.cache_key("codex", self.target, THREAD_ID)),
            THREAD_NAME,
        )

    def test_a_resumed_conversation_supersedes_the_fresh_threads_sleeping_retry(self):
        """The first `/resume` must not lose to the unnamed fresh thread's worker."""
        first_asked = threading.Event()
        second_published = threading.Event()
        original_publish = terminal._publish_agent_conversation

        def probe(_target, thread_id, **_kwargs):
            self.asked.append(thread_id)
            if thread_id == THREAD_ID:
                first_asked.set()
                return conversations.LOOKUP_UNNAMED, ""
            return conversations.LOOKUP_NAMED, THREAD_NAME

        def publish(session_id, connection, title, name, **kwargs):
            published = original_publish(
                session_id, connection, title, name, **kwargs
            )
            if published and name == THREAD_NAME:
                second_published.set()
            return published

        with (
            patch.object(terminal, "probe_conversation_name", side_effect=probe),
            patch.object(terminal, "retry_delay", return_value=2.0),
            patch.object(
                terminal, "_publish_agent_conversation", side_effect=publish
            ),
        ):
            self.assertTrue(
                terminal._start_conversation_resolver(
                    "pane", self.connection, THREAD_ID, THREAD_ID
                )
            )
            self.assertTrue(first_asked.wait(1.0))

            self.connection["agent_activity"] = apply_agent_events(
                self.connection["agent_activity"],
                [(AGENT_EVENT_TITLE, OTHER_THREAD_ID)],
                time.time(),
            )
            self.assertTrue(
                terminal._start_conversation_resolver(
                    "pane", self.connection, OTHER_THREAD_ID, OTHER_THREAD_ID
                )
            )
            self.assertTrue(second_published.wait(1.0))

        deadline = time.time() + 1.0
        while "pane" in terminal._conversation_resolvers and time.time() < deadline:
            time.sleep(0.01)

        self.assertEqual(self.asked, [THREAD_ID, OTHER_THREAD_ID])
        self.assertEqual(self.published(), THREAD_NAME)
        self.assertNotIn("pane", terminal._conversation_resolvers)

    def test_a_thread_that_is_never_named_is_left_with_its_fallback(self):
        self.resolve()

        # One attempt per rung of the schedule, plus the first one, and then
        # the pane is left alone rather than polled.
        self.assertEqual(len(self.asked), 3)
        self.assertEqual(self.published(), "")
        self.assertNotIn("pane", terminal._conversation_resolvers)
        # Nothing about that silence is kept, so a later pane on the same
        # thread runs its own schedule rather than inheriting this one.
        self.assertEqual(
            self.cache.resolved_name(conversations.cache_key("codex", self.target, THREAD_ID)),
            "",
        )

    def test_an_answer_another_pane_already_found_costs_no_lookup(self):
        self.cache.remember(
            conversations.cache_key("codex", self.target, THREAD_ID), THREAD_NAME
        )

        self.resolve()

        self.assertEqual(self.asked, [])
        self.assertEqual(self.published(), THREAD_NAME)

    def test_a_pane_retired_while_the_lookup_was_out_is_never_named(self):
        self.answers = [(conversations.LOOKUP_NAMED, THREAD_NAME)]
        self.registry["pane"] = {"kind": "local"}

        self.resolve()

        self.assertEqual(self.published(), "")

    def test_a_cancelled_command_lookup_cannot_publish_its_late_answer(self):
        """A `/resume` switch may cancel while app-server is still answering."""
        cancellation = threading.Event()

        def answer_then_cancel(_target, _thread_id, **_kwargs):
            cancellation.set()
            return conversations.LOOKUP_NAMED, THREAD_NAME

        with patch.object(
            terminal, "probe_conversation_name", side_effect=answer_then_cancel
        ):
            terminal._resolve_agent_conversation(
                "pane",
                self.connection,
                None,
                THREAD_ID,
                cancel_event=cancellation,
            )

        self.assertEqual(self.published(), "")
        self.assertEqual(
            self.cache.resolved_name(
                conversations.cache_key("codex", self.target, THREAD_ID)
            ),
            "",
        )

    def test_a_pane_that_answered_the_question_itself_is_asked_nothing(self):
        self.connection["agent_activity"] = apply_agent_events(
            self.connection["agent_activity"],
            [(AGENT_EVENT_TITLE, "Fix the parser")],
            time.time(),
        )

        self.resolve()

        self.assertEqual(self.asked, [])
        self.assertEqual(self.published(), "")

    def test_a_rename_refuses_the_name_it_replaced_until_it_changes(self):
        self.answers = [
            (conversations.LOOKUP_NAMED, "Old name"),
            (conversations.LOOKUP_NAMED, "Old name"),
            (conversations.LOOKUP_NAMED, "Something better"),
        ]

        with patch.object(terminal, "probe_conversation_name", side_effect=self._probe):
            terminal._resolve_agent_conversation(
                "pane", self.connection, None, THREAD_ID, reject_name="Old name"
            )

        self.assertEqual(len(self.asked), 3)
        self.assertEqual(self.published(), "Something better")

    def test_a_command_borne_lookup_is_not_stopped_by_a_title_change(self):
        """A resumed pane's title is its project and moves for its own reasons."""
        self.answers = [(conversations.LOOKUP_NAMED, THREAD_NAME)]
        self.connection["agent_activity"] = apply_agent_events(
            self.connection["agent_activity"],
            [(AGENT_EVENT_TITLE, "some other project")],
            time.time(),
        )

        with patch.object(terminal, "probe_conversation_name", side_effect=self._probe):
            terminal._resolve_agent_conversation("pane", self.connection, None, THREAD_ID)

        self.assertEqual(self.published(), THREAD_NAME)

    def test_a_pane_with_nowhere_to_ask_asks_nothing(self):
        with patch.object(terminal, "_conversation_probe_target", return_value={}):
            self.resolve()

        self.assertEqual(self.asked, [])
        self.assertEqual(self.published(), "")


if __name__ == "__main__":
    unittest.main()
