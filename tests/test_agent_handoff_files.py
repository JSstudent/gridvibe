"""The temporary file a large handed-over task travels in.

`web/agent_handoff_files.py` writes on the pane's own machine and never
through a shell. Pinned here:

- **One directory per handoff, removed with it**, and a local write that can
  fail leaves nothing behind -- the caller falls back to paged delivery.
- **The sweep removes only what a crash left, and only when old**: a newer
  entry may belong to another GridVibe install sharing the directory.
- **A remote file fails closed.** A host that refuses ``chmod``, or reports a
  mode other accounts can read, gets no file at all, exactly like the MCP
  config -- and the remote sweep judges age by the remote host's own clock.
- **No log line names a path.**
"""

import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import agent_handoff_files as files  # noqa: E402
from web import ssh_tunnel  # noqa: E402
from web.agent_handoffs import HandoffView  # noqa: E402

HANDOFF_ID = "0123456789abcdef0123456789abcdef"
OTHER_ID = "fedcba9876543210fedcba9876543210"


def _view(text="Line one\nLine two\n", handoff_id=HANDOFF_ID):
    return HandoffView(
        handoff_id=handoff_id,
        text=text,
        chars=len(text),
        source_session_id="pane-a",
        from_title="Terminal 1",
        from_agent="claude",
        created_at="2026-09-24T10:00:00+00:00",
    )


class DocumentTestCase(unittest.TestCase):
    def test_three_header_lines_a_blank_line_then_the_text_exactly(self):
        text = "Keep  this\n\tindent\n"
        document = files.handoff_document(_view(text))

        lines = document.split("\n")
        self.assertTrue(lines[0].startswith("Handed over by GridVibe from pane Terminal 1 (pane-a, agent claude)"))
        self.assertEqual(lines[1], "Created: 2026-09-24T10:00:00+00:00")
        self.assertTrue(lines[2].startswith("Note: Written by the agent in pane Terminal 1"))
        self.assertEqual(lines[3], "")
        self.assertTrue(document.endswith("\n\n" + text))


class LocalWriterTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = os.path.join(self.temp.name, "handoffs")

    def test_one_directory_per_handoff_holding_the_document(self):
        written = files.write_local_handoff(HANDOFF_ID, "the document", root=self.root)

        self.assertIsNotNone(written)
        path, cleanup = written
        self.assertEqual(Path(path), Path(self.root) / HANDOFF_ID / files.LOCAL_FILE_NAME)
        self.assertEqual(Path(path).read_bytes(), b"the document")

        cleanup()
        self.assertFalse((Path(self.root) / HANDOFF_ID).exists())
        # The shared root stays: other handoffs live there.
        self.assertTrue(Path(self.root).is_dir())

    def test_text_is_written_as_utf8_without_newline_translation(self):
        path, _cleanup = files.write_local_handoff(HANDOFF_ID, "a\nb — c\n", root=self.root)

        self.assertEqual(Path(path).read_bytes(), "a\nb — c\n".encode("utf-8"))

    def test_a_handoff_id_that_is_not_one_is_refused_and_writes_nothing(self):
        for bad in ("../escape", "", "ABCDEF" * 6, HANDOFF_ID + "0"):
            with self.subTest(bad=bad):
                self.assertIsNone(files.write_local_handoff(bad, "x", root=self.root))
        self.assertFalse(Path(self.root).exists())

    def test_an_existing_directory_is_never_written_into(self):
        """Exclusive creation: a second write for the same id fails cleanly."""
        files.write_local_handoff(HANDOFF_ID, "first", root=self.root)

        self.assertIsNone(files.write_local_handoff(HANDOFF_ID, "second", root=self.root))
        self.assertEqual(
            (Path(self.root) / HANDOFF_ID / files.LOCAL_FILE_NAME).read_text(encoding="utf-8"),
            "first",
        )

    def test_a_write_that_fails_leaves_nothing_behind(self):
        real_open = os.open

        def failing_open(path, flags, mode=0o777):
            if str(path).endswith(files.LOCAL_FILE_NAME):
                raise PermissionError(13, "denied")
            return real_open(path, flags, mode)

        with patch.object(files.os, "open", side_effect=failing_open):
            self.assertIsNone(files.write_local_handoff(HANDOFF_ID, "x", root=self.root))

        self.assertFalse((Path(self.root) / HANDOFF_ID).exists())

    @unittest.skipIf(os.name == "nt", "POSIX modes")
    def test_the_directory_and_file_are_owner_only(self):
        path, _cleanup = files.write_local_handoff(HANDOFF_ID, "x", root=self.root)

        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o700)

    @unittest.skipIf(os.name == "nt", "POSIX modes")
    def test_an_open_shared_root_is_narrowed_before_use(self):
        os.makedirs(self.root, mode=0o777)
        os.chmod(self.root, 0o777)

        self.assertIsNotNone(files.write_local_handoff(HANDOFF_ID, "x", root=self.root))
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o700)

    def test_a_root_that_is_a_file_is_refused(self):
        Path(self.root).write_text("not a directory", encoding="utf-8")

        self.assertIsNone(files.write_local_handoff(HANDOFF_ID, "x", root=self.root))

    def test_the_default_root_is_the_test_suites_own_directory(self):
        """`tests/__init__.py` redirects it: the suite never writes or sweeps
        a directory a real GridVibe uses."""
        self.assertEqual(
            files.local_handoff_root(),
            os.path.realpath(os.environ[files.HANDOFF_DIR_VARIABLE]),
        )

    def test_without_the_override_it_lives_under_the_real_temp_directory(self):
        with patch.dict(os.environ, {files.HANDOFF_DIR_VARIABLE: ""}):
            root = files.local_handoff_root()

        self.assertEqual(os.path.basename(root), files.LOCAL_DIRECTORY_NAME)
        self.assertEqual(os.path.dirname(root), os.path.realpath(tempfile.gettempdir()))

    def test_no_log_line_names_the_path(self):
        with self.assertLogs(files.logger, level="DEBUG") as logs:
            files.write_local_handoff(HANDOFF_ID, "x", root=self.root)
            files.write_local_handoff(HANDOFF_ID, "x", root=self.root)

        joined = "\n".join(logs.output)
        self.assertNotIn(self.root, joined)
        self.assertNotIn(files.LOCAL_FILE_NAME, joined)
        self.assertIn(HANDOFF_ID, joined)


class LocalSweepTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "handoffs"
        self.root.mkdir()
        self.now = time.time()

    def _entry(self, name, age_seconds, directory=True):
        path = self.root / name
        if directory:
            path.mkdir()
            (path / files.LOCAL_FILE_NAME).write_text("x", encoding="utf-8")
        else:
            path.write_text("x", encoding="utf-8")
        stamp = self.now - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_only_old_handoff_directories_are_removed(self):
        old = self._entry(HANDOFF_ID, files.SWEEP_AGE_SECONDS + 60)
        young = self._entry(OTHER_ID, files.SWEEP_AGE_SECONDS - 60)
        foreign = self._entry("not-a-handoff", files.SWEEP_AGE_SECONDS * 3)
        stray_file = self._entry("aa" * 16, files.SWEEP_AGE_SECONDS * 3, directory=False)

        removed = files.sweep_local_handoffs(root=str(self.root), now=self.now)

        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(young.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue(stray_file.exists())

    def test_a_missing_root_sweeps_nothing(self):
        self.assertEqual(
            files.sweep_local_handoffs(root=str(self.root / "absent"), now=self.now), 0
        )


class FakeSftp:
    """An SFTP server in a dict, with the failures a real host can have."""

    def __init__(self, *, home="/home/ubuntu", refuse_chmod=False, report_mode=None,
                 refuse_open=False, clock=5_000_000.0):
        self.home = home
        self.refuse_chmod = refuse_chmod
        self.report_mode = report_mode
        self.refuse_open = refuse_open
        self.clock = clock
        self.dirs = {}
        self.files = {}
        self.removed = []

    def normalize(self, path):
        return self.home

    def stat(self, path):
        if path in self.dirs:
            return SimpleNamespace(st_mode=stat.S_IFDIR | self.dirs[path]["mode"], st_mtime=self.clock)
        if path in self.files:
            entry = self.files[path]
            mode = self.report_mode if self.report_mode is not None else entry["mode"]
            return SimpleNamespace(st_mode=stat.S_IFREG | mode, st_mtime=entry["mtime"])
        raise FileNotFoundError(path)

    def mkdir(self, path, mode=0o777):
        self.dirs[path] = {"mode": mode}

    def chmod(self, path, mode):
        if self.refuse_chmod:
            raise PermissionError("Operation unsupported")
        target = self.dirs.get(path) or self.files.get(path)
        target["mode"] = mode

    def open(self, path, flags):
        if self.refuse_open:
            raise PermissionError("denied")
        fake = self

        class _Handle:
            def write(self, data):
                fake.files[path] = {"data": data, "mode": 0o644, "mtime": fake.clock}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        return _Handle()

    def remove(self, path):
        self.removed.append(path)
        self.files.pop(path, None)

    def listdir_attr(self, directory):
        prefix = directory.rstrip("/") + "/"
        return [
            SimpleNamespace(filename=path[len(prefix):], st_mtime=entry["mtime"])
            for path, entry in self.files.items()
            if path.startswith(prefix) and "/" not in path[len(prefix):]
        ]


class RemoteWriterTestCase(unittest.TestCase):
    DIRECTORY = "/home/ubuntu/.gridvibe/handoffs"
    PATH = f"{DIRECTORY}/{HANDOFF_ID}.md"

    def test_a_file_is_written_owner_only_inside_owner_only_directories(self):
        sftp = FakeSftp()

        path = files.write_remote_handoff(sftp, HANDOFF_ID, "the — brief")

        self.assertEqual(path, self.PATH)
        self.assertEqual(sftp.files[path]["data"], "the — brief".encode("utf-8"))
        self.assertEqual(sftp.files[path]["mode"], 0o600)
        self.assertEqual(sftp.dirs["/home/ubuntu/.gridvibe"]["mode"], 0o700)
        self.assertEqual(sftp.dirs[self.DIRECTORY]["mode"], 0o700)

    def test_a_host_that_refuses_chmod_gets_no_file(self):
        sftp = FakeSftp(refuse_chmod=True)

        self.assertEqual(files.write_remote_handoff(sftp, HANDOFF_ID, "x"), "")
        self.assertEqual(sftp.files, {})

    def test_a_mode_other_accounts_can_read_is_removed_again(self):
        sftp = FakeSftp()
        sftp.dirs["/home/ubuntu/.gridvibe"] = {"mode": 0o700}
        sftp.dirs[self.DIRECTORY] = {"mode": 0o700}
        sftp.report_mode = 0o644

        self.assertEqual(files.write_remote_handoff(sftp, HANDOFF_ID, "x"), "")
        self.assertIn(self.PATH, sftp.removed)
        self.assertNotIn(self.PATH, sftp.files)

    def test_a_write_the_host_refuses_leaves_nothing(self):
        sftp = FakeSftp(refuse_open=True)

        self.assertEqual(files.write_remote_handoff(sftp, HANDOFF_ID, "x"), "")
        self.assertEqual(sftp.files, {})

    def test_no_sftp_or_no_home_writes_nothing(self):
        self.assertEqual(files.write_remote_handoff(None, HANDOFF_ID, "x"), "")
        self.assertEqual(files.write_remote_handoff(FakeSftp(home=""), HANDOFF_ID, "x"), "")
        self.assertEqual(files.write_remote_handoff(FakeSftp(), "../../etc/passwd", "x"), "")

    def test_the_sweep_uses_the_remote_hosts_clock(self):
        """This machine's clock may disagree with the host's by hours."""
        sftp = FakeSftp(clock=5_000_000.0)
        stale = f"{self.DIRECTORY}/{OTHER_ID}.md"
        young = f"{self.DIRECTORY}/{'ab' * 16}.md"
        foreign = f"{self.DIRECTORY}/notes.md"
        sftp.files[stale] = {"data": b"", "mode": 0o600, "mtime": 5_000_000.0 - files.SWEEP_AGE_SECONDS - 1}
        sftp.files[young] = {"data": b"", "mode": 0o600, "mtime": 5_000_000.0 - 60}
        sftp.files[foreign] = {"data": b"", "mode": 0o600, "mtime": 1.0}

        with patch.object(files.time, "time", return_value=1.0):
            files.write_remote_handoff(sftp, HANDOFF_ID, "x")

        self.assertNotIn(stale, sftp.files)
        self.assertIn(young, sftp.files)
        self.assertIn(foreign, sftp.files)
        self.assertIn(self.PATH, sftp.files)

    def test_no_log_line_names_the_remote_file(self):
        sftp = FakeSftp(refuse_chmod=True)
        sftp.dirs["/home/ubuntu/.gridvibe"] = {"mode": 0o700}
        sftp.dirs[self.DIRECTORY] = {"mode": 0o700}

        with self.assertLogs(level="DEBUG") as logs:
            files.write_remote_handoff(sftp, HANDOFF_ID, "x")

        joined = "\n".join(logs.output)
        self.assertNotIn(f"{HANDOFF_ID}.md", joined)


class TunnelTeardownTestCase(unittest.TestCase):
    def test_teardown_removes_the_handoff_files_on_the_same_channel(self):
        sftp = FakeSftp()
        sftp.files["/home/ubuntu/.gridvibe/handoffs/a.md"] = {"data": b"", "mode": 0o600, "mtime": 0}
        sftp.close = lambda: sftp.removed.append("<channel closed>")

        class _Inline:
            def __init__(self, target, **_kwargs):
                self.target = target

            def start(self):
                self.target()

        record = {
            "sftp": sftp,
            "remote_path": "/home/ubuntu/.gridvibe/mcp-p.json",
            "remote_port": 0,
            "handoff_paths": ["/home/ubuntu/.gridvibe/handoffs/a.md"],
        }
        with patch.object(ssh_tunnel.threading, "Thread", _Inline):
            ssh_tunnel.teardown(None, record)

        # Removed, and before the channel closed under it.
        self.assertLess(
            sftp.removed.index("/home/ubuntu/.gridvibe/handoffs/a.md"),
            sftp.removed.index("<channel closed>"),
        )


if __name__ == "__main__":
    unittest.main()
