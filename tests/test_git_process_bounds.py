"""ISSUE-2026-040: the explorer Git runner's output and process bounds.

These tests are written **before** the fix, and every one of them asserts the
*target* behaviour rather than today's defect: a suite that encoded the current
bug would have to be inverted later, which destroys its value as a stable
contract and misleads anyone bisecting through it.

Each pinned test therefore carries ``@unittest.expectedFailure`` until the fix
lands. **Removing the decorator is part of that work, not a later cleanup** —
an unexpected success fails the run, so a decorator left behind afterwards is
loud rather than silent. Tests *without* the decorator are controls: behaviour
that is already correct and that the rewrite must preserve. The approach
mirrors `tests/test_session_persistence_contract.py`.

What is pinned here (`web/explorer.py`, Guardrails 3 and 4):

1. Every Git command — read *and* write, local *and* remote — sets
   ``GIT_TERMINAL_PROMPT=0``. Today reads set only ``GIT_OPTIONAL_LOCKS=0``,
   through an ``if write: ... else: ...`` that makes the two mutually
   exclusive, so a read against a remote that wants credentials can park a
   worker thread on a prompt nobody can answer.
2. Output limits are a backend invariant, not an opt-in caller feature. Today
   ``max_output_bytes`` is omitted by status, graph, commit-file, diff, and
   every mutation path; only `explorer_search` passes it.
3. Both streams are bounded. Today the bounded branch caps stdout and
   accumulates stderr without any limit.
4. The remote runner drains bounded rather than calling whole-stream
   ``stdout.read()``/``stderr.read()``.
5. The timeout path owns a process **group** and reaps under a second bound,
   so a surviving Git helper holding our pipes cannot outlast the timeout.

`_run_git_command` is exercised through the module attribute
``web.explorer.subprocess`` rather than by patching one entry point, because
the fix is expected to replace ``subprocess.run()`` with an owned ``Popen``.
The stub answers both shapes so these tests pin the contract and not the call.
"""

import os
import shutil
import socket
import subprocess
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from web import explorer as web_explorer

#: The ceiling the fix must publish on `web.explorer` so every Git invocation
#: is bounded by default. Named here so the fix cannot satisfy the tests with
#: a private literal buried in one branch. It may be renamed, but only by
#: changing this constant in the same commit — never by adding a second
#: parallel surface (`SEARCH_GIT_MAX_OUTPUT_BYTES` stays the smaller,
#: per-operation search limit layered on top).
GIT_OUTPUT_CEILING_ATTR = "EXPLORER_GIT_MAX_OUTPUT_BYTES"

#: A child that produces far more than any sane ceiling. Large enough that
#: "the runner bounded it" and "the runner kept everything" cannot be confused,
#: small enough to stay cheap to allocate.
OVERSIZED_OUTPUT_BYTES = 24 * 1024 * 1024

#: How long the real stalled-remote case may take before the runner is judged
#: unbounded. It only has to separate "bounded" from "waited for git to give up
#: on its own", which is minutes away, so a few multiples of the 2 s timeout
#: under test is plenty — and it caps what this test costs the suite while the
#: defect is still present.
PROCESS_TREE_BOUND_SECONDS = 8.0


class _StubPipe:
    """A synthetic pipe yielding `total` bytes without materializing them.

    Records how much was actually read so a test can tell a runner that
    *stopped* at its ceiling from one that slurped the whole stream and sliced
    afterwards — the distinction Guardrail 3 is about.
    """

    def __init__(self, total: int, stop: threading.Event):
        self.total = int(total)
        self.remaining = int(total)
        self.bytes_read = 0
        self.closed = False
        self._stop = stop

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            return b""
        if size is None or size < 0:
            size = self.remaining
        # A terminated child stops producing; without this the reader threads
        # in the bounded branch would never see EOF after `terminate()`.
        if self._stop.is_set():
            return b""
        count = min(size, self.remaining)
        if count <= 0:
            return b""
        self.remaining -= count
        self.bytes_read += count
        return b"x" * count

    def close(self) -> None:
        self.closed = True


class _StubGitProcess:
    """One fake Git child, readable through pipes or `communicate()`."""

    def __init__(self, *, stdout_bytes: int = 0, stderr_bytes: int = 0, returncode: int = 0):
        self.pid = -1
        self.returncode = returncode
        self.killed = False
        self.terminated = False
        self._stop = threading.Event()
        self.stdout = _StubPipe(stdout_bytes, self._stop)
        self.stderr = _StubPipe(stderr_bytes, self._stop)

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self._stop.set()

    def kill(self):
        self.killed = True
        self._stop.set()

    def communicate(self, timeout=None):
        return self.stdout.read(), self.stderr.read()


class _RecordingSubprocess:
    """Stands in for the `subprocess` module inside `web.explorer`.

    Intercepts only the two process-spawning entry points and forwards
    everything else (``PIPE``, ``CompletedProcess``, ``TimeoutExpired``) to the
    real module, so the runner can still build its results normally.
    """

    def __init__(self, *, stdout_bytes: int = 0, stderr_bytes: int = 0, returncode: int = 0):
        self._stdout_bytes = stdout_bytes
        self._stderr_bytes = stderr_bytes
        self._returncode = returncode
        self.commands = []
        self.envs = []
        self.kwargs = []
        self.processes = []

    def __getattr__(self, name):
        return getattr(subprocess, name)

    def _record(self, command, kwargs):
        self.commands.append(list(command))
        self.envs.append(dict(kwargs.get("env") or {}))
        self.kwargs.append(kwargs)

    def run(self, command, **kwargs):
        self._record(command, kwargs)
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=self._returncode,
            stdout=b"x" * self._stdout_bytes,
            stderr=b"x" * self._stderr_bytes,
        )

    def Popen(self, command, **kwargs):  # noqa: N802 - mirrors the stdlib name
        self._record(command, kwargs)
        process = _StubGitProcess(
            stdout_bytes=self._stdout_bytes,
            stderr_bytes=self._stderr_bytes,
            returncode=self._returncode,
        )
        self.processes.append(process)
        return process

    @property
    def stdout_bytes_read(self) -> int:
        return sum(process.stdout.bytes_read for process in self.processes)

    @property
    def stderr_bytes_read(self) -> int:
        return sum(process.stderr.bytes_read for process in self.processes)


class _StubRemoteStream:
    """A paramiko-like channel file with a bounded, countable read."""

    def __init__(self, total: int, exit_status: int = 0):
        self.total = int(total)
        self.remaining = int(total)
        self.bytes_read = 0
        self.channel = SimpleNamespace(recv_exit_status=lambda: exit_status)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self.remaining
        count = min(size, self.remaining)
        if count <= 0:
            return b""
        self.remaining -= count
        self.bytes_read += count
        return b"x" * count


class _StubRemoteClient:
    """Answers `exec_command` with oversized stdout and stderr."""

    def __init__(self, *, stdout_bytes: int = 0, stderr_bytes: int = 0, exit_status: int = 0):
        self.commands = []
        self.stdout = _StubRemoteStream(stdout_bytes, exit_status)
        self.stderr = _StubRemoteStream(stderr_bytes, exit_status)

    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        return None, self.stdout, self.stderr


class ExplorerGitEnvironmentTestCase(unittest.TestCase):
    """Guardrail 4 — every Git invocation suppresses the credential prompt."""

    def _run(self, *, write: bool, **kwargs):
        stub = _RecordingSubprocess()
        with patch.object(web_explorer, "subprocess", stub):
            web_explorer._run_git_command(
                ["status", "--porcelain"],
                cwd=os.getcwd(),
                write=write,
                **kwargs,
            )
        self.assertEqual(len(stub.envs), 1)
        return stub.envs[0]

    @unittest.expectedFailure
    def test_local_read_commands_suppress_the_credential_prompt(self):
        """A read can reach a remote too: `git status` consults the upstream ref.

        Reads and writes take an `if write:` / `else:` today, so the prompt
        guard and the lock guard are mutually exclusive and a read never gets
        the one that stops it hanging.
        """
        env = self._run(write=False)
        self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")

    def test_local_read_commands_keep_the_optional_lock_guard(self):
        """The prompt fix must add to the read env, not replace what is there."""
        env = self._run(write=False)
        self.assertEqual(env.get("GIT_OPTIONAL_LOCKS"), "0")

    def test_local_write_commands_suppress_the_credential_prompt(self):
        """Already true — pinned so the ISSUE-2026-040 rewrite cannot drop it."""
        env = self._run(write=True)
        self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")

    @unittest.expectedFailure
    def test_local_bounded_commands_suppress_the_credential_prompt(self):
        """The bounded branch is a second spawn path with the same duty."""
        env = self._run(write=False, max_output_bytes=4096)
        self.assertEqual(env.get("GIT_TERMINAL_PROMPT"), "0")

    @unittest.expectedFailure
    def test_remote_read_commands_suppress_the_credential_prompt(self):
        """The remote shell prefix is one-or-the-other for the same reason."""
        command = web_explorer._remote_git_shell_command(
            ["status", "--porcelain"],
            "/srv/app",
            write=False,
        )
        self.assertIn("GIT_TERMINAL_PROMPT=0", command)

    def test_remote_read_commands_keep_the_optional_lock_guard(self):
        command = web_explorer._remote_git_shell_command(
            ["status", "--porcelain"],
            "/srv/app",
            write=False,
        )
        self.assertIn("GIT_OPTIONAL_LOCKS=0", command)

    def test_remote_write_commands_suppress_the_credential_prompt(self):
        command = web_explorer._remote_git_shell_command(
            ["push"],
            "/srv/app",
            write=True,
        )
        self.assertIn("GIT_TERMINAL_PROMPT=0", command)


class ExplorerGitOutputBoundsTestCase(unittest.TestCase):
    """Guardrail 3 — a Git command cannot return unbounded output."""

    @unittest.expectedFailure
    def test_the_module_publishes_a_default_output_ceiling(self):
        """The bound belongs to the runner, so no caller can forget it."""
        ceiling = getattr(web_explorer, GIT_OUTPUT_CEILING_ATTR)
        self.assertIsInstance(ceiling, int)
        self.assertGreater(ceiling, 0)
        self.assertLess(ceiling, OVERSIZED_OUTPUT_BYTES)

    @unittest.expectedFailure
    def test_unbounded_callers_still_get_bounded_stdout(self):
        """Status, graph, commit-file, diff, and every mutation take this path.

        They call the runner without `max_output_bytes`, so today a repository
        that produces a huge diff or status returns all of it into memory.
        """
        stub = _RecordingSubprocess(stdout_bytes=OVERSIZED_OUTPUT_BYTES)
        with patch.object(web_explorer, "subprocess", stub):
            result = web_explorer._run_git_command(["status"], cwd=os.getcwd())

        self.assertLess(len(result.stdout), OVERSIZED_OUTPUT_BYTES)
        self.assertTrue(getattr(result, "stdout_truncated", False))

    @unittest.expectedFailure
    def test_unbounded_callers_still_get_bounded_stderr(self):
        """A failing command's stderr is output too, and is never capped today."""
        stub = _RecordingSubprocess(stderr_bytes=OVERSIZED_OUTPUT_BYTES, returncode=1)
        with patch.object(web_explorer, "subprocess", stub):
            result = web_explorer._run_git_command(["status"], cwd=os.getcwd())

        self.assertLess(len(result.stderr), OVERSIZED_OUTPUT_BYTES)

    @unittest.expectedFailure
    def test_bounded_callers_also_bound_stderr(self):
        """`max_output_bytes` caps stdout only; `read_stderr` has no limit."""
        stub = _RecordingSubprocess(stderr_bytes=OVERSIZED_OUTPUT_BYTES, returncode=1)
        with patch.object(web_explorer, "subprocess", stub):
            result = web_explorer._run_git_command(
                ["grep", "-n", "needle"],
                cwd=os.getcwd(),
                max_output_bytes=64 * 1024,
            )

        self.assertLess(len(result.stderr), OVERSIZED_OUTPUT_BYTES)

    @unittest.expectedFailure
    def test_the_runner_stops_reading_instead_of_slicing_afterwards(self):
        """Guardrail 3: bound the read, do not buffer the whole stream first.

        `_bounded_git_diff` is the live example — it slices to
        `EXPLORER_GIT_DIFF_MAX_BYTES` only after the complete output has
        already been captured, so the peak memory is the repository's, not
        ours.
        """
        stub = _RecordingSubprocess(stdout_bytes=OVERSIZED_OUTPUT_BYTES)
        with patch.object(web_explorer, "subprocess", stub):
            web_explorer._run_git_command(["diff"], cwd=os.getcwd())

        self.assertTrue(
            stub.processes,
            "the runner handed the whole stream to subprocess.run() instead of "
            "draining an owned process itself",
        )
        self.assertLess(stub.stdout_bytes_read, OVERSIZED_OUTPUT_BYTES // 2)

    def test_bounded_stdout_reports_truncation(self):
        """Already true — the public field the fix must preserve."""
        stub = _RecordingSubprocess(stdout_bytes=OVERSIZED_OUTPUT_BYTES)
        with patch.object(web_explorer, "subprocess", stub):
            result = web_explorer._run_git_command(
                ["grep", "-n", "needle"],
                cwd=os.getcwd(),
                max_output_bytes=64 * 1024,
            )

        self.assertTrue(result.stdout_truncated)
        self.assertLessEqual(len(result.stdout), 64 * 1024)


class RemoteGitOutputBoundsTestCase(unittest.TestCase):
    """The SSH runner owes the same bounds as the local one (Guardrail 6)."""

    @unittest.expectedFailure
    def test_remote_stdout_is_bounded_without_a_caller_supplied_cap(self):
        """`stdout.read()` with no argument drains the whole channel today."""
        client = _StubRemoteClient(stdout_bytes=OVERSIZED_OUTPUT_BYTES)
        result = web_explorer._run_remote_git_command(
            client,
            ["status", "--porcelain"],
            cwd="/srv/app",
        )

        self.assertLess(len(result.stdout), OVERSIZED_OUTPUT_BYTES)

    @unittest.expectedFailure
    def test_remote_stderr_is_bounded(self):
        """The `head -c` pipeline bounds stdout only; stderr bypasses it."""
        client = _StubRemoteClient(stderr_bytes=OVERSIZED_OUTPUT_BYTES, exit_status=1)
        result = web_explorer._run_remote_git_command(
            client,
            ["status", "--porcelain"],
            cwd="/srv/app",
            max_output_bytes=64 * 1024,
        )

        self.assertLess(len(result.stderr), OVERSIZED_OUTPUT_BYTES)

    @unittest.expectedFailure
    def test_remote_reads_are_bounded_at_the_channel_not_after(self):
        """A bounded drain reads a bounded amount, whatever the peer sends."""
        client = _StubRemoteClient(stdout_bytes=OVERSIZED_OUTPUT_BYTES)
        web_explorer._run_remote_git_command(
            client,
            ["log", "--oneline"],
            cwd="/srv/app",
        )

        self.assertLess(client.stdout.bytes_read, OVERSIZED_OUTPUT_BYTES // 2)

    def test_remote_truncation_is_reported(self):
        """Already true — pinned so the bounded-drain rewrite preserves it."""
        client = _StubRemoteClient(stdout_bytes=64 * 1024)
        result = web_explorer._run_remote_git_command(
            client,
            ["log", "--oneline"],
            cwd="/srv/app",
            max_output_bytes=64 * 1024,
        )

        self.assertTrue(result.stdout_truncated)


class ExplorerGitProcessTreeTestCase(unittest.TestCase):
    """Guardrail 4 — the bound covers git's helpers, not only git itself."""

    def _run_git(self, repo: Path, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(repo),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _stalled_remote_port(self) -> int:
        """A listener that accepts the connection and then says nothing."""
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        accepted = []

        def accept_and_stall():
            while True:
                try:
                    accepted.append(listener.accept()[0])
                except OSError:
                    return

        threading.Thread(target=accept_and_stall, daemon=True).start()
        # Cleanup order matters: closing the sockets is what lets a child this
        # runner failed to reap notice EOF and exit, so a failing run does not
        # leave a git process behind for the rest of the suite.
        self.addCleanup(lambda: [conn.close() for conn in accepted])
        self.addCleanup(listener.close)
        return listener.getsockname()[1]

    @unittest.expectedFailure
    def test_a_stalled_remote_returns_the_worker_thread_within_the_bound(self):
        """The explorer runner has no process-group ownership at all today.

        The unbounded branch is `subprocess.run(timeout=...)`, which on Windows
        reaps with an unbounded `communicate()`; the bounded branch kills only
        the direct child and then calls `wait()` and `join()` with no bound.
        Either way a surviving transport helper holding our pipes outlasts the
        timeout — the same defect `web/selfupdate.py::_run_repo_git` already
        fixed with a process group, a tree kill, and a second bounded reap.

        Run on a worker thread with a bounded join so an unbounded runner fails
        this test in seconds rather than parking the suite for minutes.
        """
        if shutil.which("git") is None:
            self.skipTest("git executable is not available")

        # Registered before the listener so cleanup runs the other way round:
        # the sockets close first, releasing any child this runner failed to
        # reap, and only then is the checkout removed. `ignore_cleanup_errors`
        # keeps a still-held handle from turning an expected failure into a
        # suite error on Windows.
        tmp = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        port = self._stalled_remote_port()

        repo = Path(tmp.name) / "repo"
        repo.mkdir()
        self._run_git(repo, "init")
        self._run_git(repo, "config", "user.email", "gridvibe@example.invalid")
        self._run_git(repo, "config", "user.name", "GridVibe Test")
        # `https://`, not `git://`: the point of this test is the *helper*.
        # Git speaks git:// in-process, so that URL kills the direct child and
        # proves nothing; https:// forks `git-remote-https`, which inherits our
        # pipes and is exactly what outlived the bound in production.
        self._run_git(
            repo, "remote", "add", "origin", f"https://127.0.0.1:{port}/repo.git"
        )

        outcome = {}

        def call_runner():
            try:
                web_explorer._run_git_command(
                    ["fetch", "--all", "--prune"],
                    cwd=str(repo),
                    write=True,
                    timeout=2,
                )
            except BaseException as exc:  # noqa: BLE001 - recorded, re-checked below
                outcome["error"] = exc
            else:
                outcome["error"] = None
            outcome["finished"] = time.monotonic()

        started = time.monotonic()
        worker = threading.Thread(target=call_runner, daemon=True)
        worker.start()
        worker.join(PROCESS_TREE_BOUND_SECONDS)

        self.assertFalse(
            worker.is_alive(),
            f"the Git runner did not return within {PROCESS_TREE_BOUND_SECONDS:g}s",
        )
        self.assertLess(outcome["finished"] - started, PROCESS_TREE_BOUND_SECONDS)
        self.assertIsInstance(outcome["error"], subprocess.TimeoutExpired)


if __name__ == "__main__":
    unittest.main()
