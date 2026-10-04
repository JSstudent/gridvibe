"""Independent directory state read (ISSUE-2026-061).

The filesystem change signal that does not ride Git: a cheap, bounded readdir
whose fingerprint advances on child creation, deletion and rename — including
inside a Git-ignored folder and under a root with no repository. Covers the
fingerprint helper, the bounded local/remote enumeration, and the route that
serves it, plus the `directory_revision` an ordinary /entries now carries so a
change between a surface load and the first poll is detected rather than
silently accepted as the baseline.

No real SSH host is exercised: a fake SFTP covers streaming ``listdir_iter``,
refusal of unsupported streaming, timeout restoration and leased release.
"""

import shutil
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from web import api
from web import explorer as web_explorer
from web.explorer import (
    EXPLORER_DIRECTORY_STATE_MAX_ENTRIES,
    _LocalExplorerBackend,
    _SftpExplorerBackend,
    directory_revision,
    directory_state_payload,
)


def _entry(name, kind="file", type_hint="file"):
    entry_kind = "directory" if kind == "directory" else kind
    return {
        "name": name,
        "path": name,
        "type": "directory" if kind == "directory" else type_hint,
        "entry_kind": entry_kind,
        "size": 0,
        "modified": None,
        "revision": name,
    }


class DirectoryRevisionTestCase(unittest.TestCase):
    def test_legal_filename_delimiters_cannot_collide_with_membership(self):
        # These two memberships collided under newline / unit-separator joining.
        combined = [_entry("a\x1ffile\x1ffile\nb")]
        separate = [_entry("a"), _entry("b")]
        self.assertNotEqual(directory_revision(combined), directory_revision(separate))

    def test_revision_is_deterministic_and_type_sensitive(self):
        entries = [_entry("a.txt"), _entry("b", "directory")]
        self.assertEqual(directory_revision(entries), directory_revision(list(reversed(entries))))
        self.assertEqual(
            directory_revision([_entry("a.txt", "file")]),
            directory_revision([_entry("a.txt", "file")]),
        )
        self.assertNotEqual(
            directory_revision([_entry("a.txt", "file")]),
            directory_revision([_entry("a.txt", "directory")]),
        )

    def test_revision_changes_on_create_delete_and_rename(self):
        baseline = directory_revision([_entry("a.txt"), _entry("b.txt")])
        self.assertNotEqual(baseline, directory_revision([_entry("a.txt"), _entry("b.txt"), _entry("c.txt")]))
        self.assertNotEqual(baseline, directory_revision([_entry("a.txt")]))
        self.assertNotEqual(baseline, directory_revision([_entry("a.txt"), _entry("renamed.txt")]))
        self.assertRegex(baseline, r"^[0-9a-f]{16}$")

    def test_revision_ignores_git_decorations(self):
        # The fingerprint is computed from the raw filesystem listing, so Git
        # status/renamed-path metadata must not shift it — otherwise every
        # decoration refresh would read as a change. (Synthetic *deleted* entries
        # never reach the fingerprint at all; the backend computes it first.)
        plain = [_entry("a.txt"), _entry("b.txt")]
        decorated = [
            {**_entry("a.txt"), "git": {"status": "modified"}},
            {**_entry("b.txt"), "git": {"status": "clean", "index_status": ".", "worktree_status": "."}},
        ]
        self.assertEqual(directory_revision(plain), directory_revision(decorated))

    def test_revision_is_unchanged_by_a_synthetic_entry_with_identical_identity(self):
        # A synthetic deleted entry whose name/type/kind already exist is the
        # same identity; only a genuinely new child changes the fingerprint.
        base = directory_revision([_entry("a.txt")])
        self.assertEqual(base, directory_revision([_entry("a.txt", "file", "file")]))


class BoundedListingTestCase(unittest.TestCase):
    def test_local_time_expiry_closes_iterator_and_refuses_token(self):
        iterator = Mock()
        iterator.__next__ = Mock(return_value=object())
        scan = Mock()
        scan.__enter__ = Mock(return_value=iterator)
        scan.__exit__ = Mock(return_value=False)
        backend = _LocalExplorerBackend()
        with patch.object(web_explorer.os, 'scandir', return_value=scan), \
             patch.object(web_explorer.time, 'monotonic', side_effect=[0, 0, 3]), \
             patch.object(web_explorer, '_explorer_entry_payload') as payload:
            entries, complete = backend.list_entries_bounded('/root', '/root', 100)
        self.assertFalse(complete)
        self.assertEqual(entries, [])
        payload.assert_not_called()
        scan.__exit__.assert_called_once()

    def _backend(self, root):
        session = SimpleNamespace(
            startup_mode="explorer",
            mode="wsl",
            explorer_root=str(root),
            directory=str(root),
        )
        return _LocalExplorerBackend(session)

    def test_local_listing_is_bounded_and_reports_incompleteness(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(5):
                (root / f"f{i}.txt").write_text("x", encoding="utf-8")
            backend = self._backend(root)
            entries, complete = backend.list_entries_bounded(str(root), str(root), 3)
            self.assertEqual(len(entries), 3)
            self.assertFalse(complete)
            entries, complete = backend.list_entries_bounded(str(root), str(root), 100)
            self.assertEqual(len(entries), 5)
            self.assertTrue(complete)

    def test_directory_state_payload_refuses_truncated_prefix(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(8):
                (root / f"f{i}.txt").write_text("x", encoding="utf-8")
            backend = self._backend(root)
            original = EXPLORER_DIRECTORY_STATE_MAX_ENTRIES
            try:
                web_explorer.EXPLORER_DIRECTORY_STATE_MAX_ENTRIES = 3
                payload = directory_state_payload(backend, "")
                self.assertEqual(payload["complete"], False)
                self.assertEqual(payload["revision"], "")
                web_explorer.EXPLORER_DIRECTORY_STATE_MAX_ENTRIES = 100
                payload = directory_state_payload(backend, "")
                self.assertEqual(payload["complete"], True)
                self.assertEqual(
                    payload["revision"],
                    directory_revision(backend.list_entries(str(root), str(root))),
                )
            finally:
                web_explorer.EXPLORER_DIRECTORY_STATE_MAX_ENTRIES = original


class FakeDirectorySftp:
    """A paramiko-flavoured SFTP double with both listing APIs.

    ``entries`` maps a normalized absolute path to ``{"type": ...}``; children
    are synthesized from the ``/``-separated tree just like the real protocol.
    """

    def __init__(self, entries):
        self.entries = entries
        self.listdir_iter_calls = 0

    def normalize(self, path):
        path = str(path or "/").replace("\\", "/").rstrip("/") or "/"
        return path

    def _children(self, path):
        prefix = path.rstrip("/") + "/"
        results = []
        for entry_path, item in self.entries.items():
            if not entry_path.startswith(prefix):
                continue
            name = entry_path[len(prefix):]
            if "/" in name or not name:
                continue
            results.append(
                SimpleNamespace(
                    filename=name,
                    st_mode=(0o040000 if item["type"] == "directory" else 0o100644),
                    st_size=0,
                    st_mtime=1000,
                )
            )
        return results

    def listdir_attr(self, path):
        return self._children(self.normalize(path))

    def listdir_iter(self, path, read_aheads=50):
        self.listdir_iter_calls += 1
        self._read_aheads = read_aheads
        for child in self._children(self.normalize(path)):
            yield child


class RemoteBoundedListingTestCase(unittest.TestCase):
    def test_leased_remote_resources_release_when_state_enumeration_fails(self):
        session = SimpleNamespace(session_id='remote')
        sftp = self._sftp()
        sftp.listdir_iter = Mock(side_effect=OSError('channel failed'))
        client = object()
        with patch.object(web_explorer, '_is_remote_explorer_session', return_value=True), \
             patch.object(web_explorer, '_acquire_ssh_sftp', return_value=(client, sftp)), \
             patch.object(web_explorer, '_release_ssh_sftp') as release:
            with self.assertRaises(OSError):
                with web_explorer._explorer_backend(session) as backend:
                    backend.list_entries_bounded('/srv/app', '/srv/app', 100)
        release.assert_called_once_with(session, client, sftp)

    def _sftp(self):
        return FakeDirectorySftp(
            {
                "/srv/app/a.txt": {"type": "file"},
                "/srv/app/b": {"type": "directory"},
                "/srv/app/c.txt": {"type": "file"},
                "/srv/app/b/deep.txt": {"type": "file"},
            }
        )

    def test_remote_streaming_listing_bounds_and_matches_fingerprint(self):
        sftp = self._sftp()
        backend = _SftpExplorerBackend(session=None, client=None, sftp=sftp)
        entries, complete = backend.list_entries_bounded("/srv/app", "/srv/app", 2)
        self.assertEqual(len(entries), 2)
        self.assertFalse(complete)
        self.assertEqual(sftp.listdir_iter_calls, 1)
        self.assertEqual(sftp._read_aheads, 1)
        entries, complete = backend.list_entries_bounded("/srv/app", "/srv/app", 100)
        self.assertTrue(complete)
        self.assertEqual(
            directory_revision(entries),
            directory_revision(backend.list_entries("/srv/app", "/srv/app")),
        )

    def test_remote_without_streaming_refuses_cheap_fingerprinting(self):
        sftp = self._sftp()
        sftp.listdir_iter = None
        sftp.listdir_attr = Mock(side_effect=AssertionError("unbounded read"))
        backend = _SftpExplorerBackend(session=None, client=None, sftp=sftp)
        entries, complete = backend.list_entries_bounded("/srv/app", "/srv/app", 100)
        self.assertFalse(complete)
        self.assertEqual(entries, [])
        sftp.listdir_attr.assert_not_called()

    def test_remote_time_budget_refuses_partial_token_and_closes_iterator(self):
        sftp = self._sftp()
        iterator = Mock()
        iterator.__next__ = Mock(return_value=sftp._children('/srv/app')[0])
        sftp.listdir_iter = Mock(return_value=iterator)
        channel = Mock()
        channel.gettimeout.return_value = 10.0
        sftp.get_channel = Mock(return_value=channel)
        backend = _SftpExplorerBackend(None, None, sftp)
        with patch.object(web_explorer.time, 'monotonic', side_effect=[0, 0, 0, 3]):
            entries, complete = backend.list_entries_bounded('/srv/app', '/srv/app', 100)
        self.assertFalse(complete)
        self.assertEqual(entries, [])
        iterator.close.assert_called_once()
        channel.settimeout.assert_any_call(2.0)
        channel.settimeout.assert_called_with(10.0)

    def test_remote_iterator_is_closed_on_read_failure(self):
        sftp = self._sftp()
        iterator = Mock()
        iterator.__next__ = Mock(side_effect=OSError('channel failed'))
        sftp.listdir_iter = Mock(return_value=iterator)
        backend = _SftpExplorerBackend(None, None, sftp)
        with self.assertRaises(OSError):
            backend.list_entries_bounded('/srv/app', '/srv/app', 100)
        iterator.close.assert_called_once()


class DirectoryStateRouteTestCase(unittest.TestCase):
    def test_state_poll_is_confined_shallow_and_does_not_read_git_or_content(self):
        root = self._root()
        (root / 'folder').mkdir()
        (root / 'folder' / 'nested').write_text('secret', encoding='utf-8')
        session_id = self._session(root)
        scandir = web_explorer.os.scandir
        with patch.object(web_explorer.os, 'scandir', wraps=scandir) as scan, \
             patch.object(_LocalExplorerBackend, 'run_git', side_effect=AssertionError('Git')), \
             patch.object(_LocalExplorerBackend, 'read_file_prefix', side_effect=AssertionError('content')), \
             patch.object(_LocalExplorerBackend, 'open_file_stream', side_effect=AssertionError('content')):
            response = self._state(session_id)
        self.assertEqual(response.status_code, 200)
        scan.assert_called_once_with(str(root))
        outside = self._state(session_id, path='../')
        self.assertEqual(outside.status_code, 400)

    def test_entries_remains_complete_beyond_cheap_poll_cap(self):
        root = self._root()
        for name in ('a', 'b', 'c'):
            (root / name).write_text('x', encoding='utf-8')
        session_id = self._session(root)
        with patch.object(api, 'EXPLORER_DIRECTORY_STATE_MAX_ENTRIES', 2):
            response = self.client.get(f'/api/explorer/{session_id}/entries?path=')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual({entry['name'] for entry in payload['entries']}, {'a', 'b', 'c'})
        self.assertIsNone(payload['directory_revision'])

    def test_deleted_watched_folder_is_distinct_from_missing_session(self):
        root = self._root()
        folder = root / 'gone'
        folder.mkdir()
        session_id = self._session(root)
        folder.rmdir()
        deleted = self._state(session_id, path='gone')
        self.assertEqual(deleted.status_code, 404)
        self.assertEqual(deleted.get_json()['code'], 'not_found')
        self.assertEqual(self._state('missing').get_json()['code'], 'session_not_found')

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    def _root(self):
        return Path(self.temp_dir.name)

    def _session(self, root):
        response = self.client.post(
            "/api/sessions",
            json={
                "connection_mode": "wsl",
                "sessions": [
                    {"directory": str(root), "title": "Files", "startup_mode": "explorer"}
                ],
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.get_json()["sessions"][0]["session_id"]

    def _state(self, session_id, path="", known=""):
        query = {}
        if path:
            query["path"] = path
        if known:
            query["known"] = known
        return self.client.get(
            f"/api/explorer/{session_id}/directory/state", query_string=query
        )

    def test_non_git_root_answers_a_valid_revision(self):
        root = self._root()
        (root / "a.txt").write_text("x", encoding="utf-8")
        session_id = self._session(root)
        response = self._state(session_id)
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertRegex(payload["revision"], r"^[0-9a-f]{16}$")
        self.assertTrue(payload["complete"])
        self.assertTrue(payload["changed"])

    def test_unchanged_directory_reports_no_change(self):
        root = self._root()
        (root / "a.txt").write_text("x", encoding="utf-8")
        session_id = self._session(root)
        revision = self._state(session_id).get_json()["revision"]
        payload = self._state(session_id, known=revision).get_json()
        self.assertFalse(payload["changed"])

    def test_external_creation_deletion_and_rename_advance_the_revision(self):
        root = self._root()
        (root / "a.txt").write_text("x", encoding="utf-8")
        session_id = self._session(root)
        baseline = self._state(session_id).get_json()["revision"]

        (root / "b.txt").write_text("x", encoding="utf-8")
        created = self._state(session_id, known=baseline).get_json()
        self.assertTrue(created["changed"])
        self.assertNotEqual(created["revision"], baseline)

        (root / "b.txt").unlink()
        deleted = self._state(session_id, known=created["revision"]).get_json()
        self.assertTrue(deleted["changed"])

        (root / "a.txt").rename(root / "renamed.txt")
        renamed = self._state(session_id, known=deleted["revision"]).get_json()
        self.assertTrue(renamed["changed"])

    @unittest.skipUnless(shutil.which('git'), 'Git is required for ignored-folder coverage')
    def test_ignored_folder_children_still_change_the_revision(self):
        root = self._root()
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True, capture_output=True, timeout=10)
        (root / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        ignored = root / "ignored"
        ignored.mkdir()
        session_id = self._session(root)
        baseline = self._state(session_id, path="ignored").get_json()["revision"]
        status_command = ['git', '-C', str(root), 'status', '--porcelain=v2', '--untracked-files=all']
        git_before = subprocess.run(status_command, check=True, capture_output=True, timeout=10).stdout

        (ignored / "new-file.txt").write_text("x", encoding="utf-8")
        subprocess.run(['git', '-C', str(root), 'check-ignore', 'ignored/new-file.txt'],
                       check=True, capture_output=True, timeout=10)
        git_after = subprocess.run(status_command, check=True, capture_output=True, timeout=10).stdout
        self.assertEqual(git_before, git_after)
        changed = self._state(session_id, path="ignored", known=baseline).get_json()
        self.assertTrue(changed["changed"])
        entries = self.client.get(f'/api/explorer/{session_id}/entries?path=ignored').get_json()
        self.assertEqual([entry['name'] for entry in entries['entries']], ['new-file.txt'])
        self.assertEqual(entries['directory_revision'], changed['revision'])

    @unittest.skipUnless(shutil.which('git'), 'Git is required for untracked-folder coverage')
    def test_ordinary_untracked_folder_has_an_independent_membership_token(self):
        root = self._root()
        subprocess.run(['git', 'init', '--quiet', str(root)], check=True, capture_output=True, timeout=10)
        folder = root / 'untracked'
        folder.mkdir()
        session_id = self._session(root)
        before = self._state(session_id, path='untracked').get_json()['revision']
        (folder / 'new.txt').write_text('new', encoding='utf-8')
        after = self._state(session_id, path='untracked', known=before).get_json()
        self.assertTrue(after['changed'])
        self.assertNotEqual(before, after['revision'])

    def test_entries_carries_the_same_directory_revision(self):
        root = self._root()
        (root / "a.txt").write_text("x", encoding="utf-8")
        sub = root / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("x", encoding="utf-8")
        session_id = self._session(root)

        state = self._state(session_id, path="sub").get_json()
        entries = self.client.get(
            f"/api/explorer/{session_id}/entries", query_string={"path": "sub"}
        ).get_json()
        self.assertEqual(entries["directory_revision"], state["revision"])

    def test_missing_session_is_404(self):
        response = self._state("missing")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
