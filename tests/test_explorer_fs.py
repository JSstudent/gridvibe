import io
import os
import posixpath
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import api
from web import explorer as web_explorer
from web import explorer_fs


def _local_session(root: Path, session_id: str = "explorer-fs-test"):
    return SimpleNamespace(
        session_id=session_id,
        mode="wsl",
        startup_mode="explorer",
        directory=str(root),
        explorer_root_directory=str(root),
    )


class _MemorySftpWriteHandle(io.BytesIO):
    def __init__(self, sftp, path):
        super().__init__()
        self.sftp = sftp
        self.path = path

    def close(self):
        if not self.closed:
            self.sftp.entries[self.path]["content"] = self.getvalue()
        super().close()


class _MemorySftpReadHandle(io.BytesIO):
    def __init__(self, content, read_sizes):
        super().__init__(content)
        self.read_sizes = read_sizes

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


class _MemorySftp:
    def __init__(self, entries):
        self.entries = entries
        self.open_modes = []
        self.read_sizes = []
        self.operations = []

    def normalize(self, path):
        raw = str(path or "/").replace("\\", "/")
        if not raw.startswith("/"):
            raw = f"/srv/app/{raw}"
        return posixpath.normpath(raw)

    def _attrs(self, path, include_name=False):
        normalized = self.normalize(path)
        if normalized not in self.entries:
            raise FileNotFoundError(normalized)
        item = self.entries[normalized]
        kind = item["kind"]
        default_mode = {
            "directory": stat.S_IFDIR | 0o755,
            "link": stat.S_IFLNK | 0o777,
        }.get(kind, stat.S_IFREG | 0o644)
        mode = item.get("mode", default_mode)
        values = {
            "st_mode": mode,
            "st_size": len(item.get("content", b"")),
            "st_mtime": item.get("mtime", 1000),
        }
        if include_name:
            values["filename"] = posixpath.basename(normalized)
        return SimpleNamespace(**values)

    def stat(self, path):
        return self._attrs(path)

    def lstat(self, path):
        return self._attrs(path)

    def listdir_attr(self, path):
        normalized = self.normalize(path)
        prefix = f"{normalized.rstrip('/')}/"
        results = []
        for candidate in sorted(self.entries):
            if not candidate.startswith(prefix):
                continue
            remainder = candidate[len(prefix) :]
            if not remainder or "/" in remainder:
                continue
            results.append(self._attrs(candidate, include_name=True))
        return results

    def open(self, path, mode="rb"):
        normalized = self.normalize(path)
        self.open_modes.append((normalized, mode))
        if mode == "rb":
            if normalized not in self.entries:
                raise FileNotFoundError(normalized)
            return _MemorySftpReadHandle(
                self.entries[normalized].get("content", b""), self.read_sizes
            )
        if mode == "x+b":
            if normalized in self.entries:
                raise FileExistsError(normalized)
            self.entries[normalized] = {
                "kind": "file",
                "content": b"",
                "mode": stat.S_IFREG | 0o600,
                "mtime": 1000,
            }
            return _MemorySftpWriteHandle(self, normalized)
        raise AssertionError(f"Unexpected open mode: {mode}")

    def mkdir(self, path):
        normalized = self.normalize(path)
        if normalized in self.entries:
            raise FileExistsError(normalized)
        self.entries[normalized] = {
            "kind": "directory",
            "mode": stat.S_IFDIR | 0o755,
            "mtime": 1000,
        }
        self.operations.append(("mkdir", normalized))

    def remove(self, path):
        normalized = self.normalize(path)
        if self.entries[normalized]["kind"] == "directory":
            raise IsADirectoryError(normalized)
        del self.entries[normalized]
        self.operations.append(("remove", normalized))

    def rmdir(self, path):
        normalized = self.normalize(path)
        prefix = f"{normalized.rstrip('/')}/"
        if any(candidate.startswith(prefix) for candidate in self.entries):
            raise OSError("directory not empty")
        del self.entries[normalized]
        self.operations.append(("rmdir", normalized))

    def rename(self, source, destination):
        source_path = self.normalize(source)
        destination_path = self.normalize(destination)
        if destination_path in self.entries:
            raise FileExistsError(destination_path)
        moved = {}
        for candidate, item in list(self.entries.items()):
            if candidate == source_path or candidate.startswith(f"{source_path}/"):
                suffix = candidate[len(source_path) :]
                moved[f"{destination_path}{suffix}"] = item
                del self.entries[candidate]
        if not moved:
            raise FileNotFoundError(source_path)
        self.entries.update(moved)
        self.operations.append(("rename", source_path, destination_path))

    def chmod(self, path, mode):
        normalized = self.normalize(path)
        kind_flag = stat.S_IFDIR if self.entries[normalized]["kind"] == "directory" else stat.S_IFREG
        self.entries[normalized]["mode"] = kind_flag | mode

    def utime(self, path, times):
        self.entries[self.normalize(path)]["mtime"] = times[1]


class ExplorerFilesystemPolicyTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "root"
        self.root.mkdir()
        self.backend = web_explorer._LocalExplorerBackend(_local_session(self.root))

    def test_copy_name_allocation_is_extension_aware(self):
        cases = {
            ("report.md", 0): "report.md",
            ("report.md", 1): "report-Copy.md",
            ("report.md", 2): "report-Copy-2.md",
            ("archive", 1): "archive-Copy",
            (".env", 1): ".env-Copy",
            ("archive.tar.gz", 1): "archive.tar-Copy.gz",
        }
        for (name, attempt), expected in cases.items():
            with self.subTest(name=name, attempt=attempt):
                self.assertEqual(
                    explorer_fs._copy_name_for_attempt(name, attempt), expected
                )

    def test_relative_path_normalization_rejects_unsafe_shapes(self):
        for value in ("", ".", "..", "a/../..", "a//b", "/tmp/a", r"C:\tmp\a", "a\x00b"):
            with self.subTest(value=value):
                with self.assertRaises(explorer_fs.ExplorerFsError):
                    explorer_fs._normalize_rel(value)
        self.assertEqual(explorer_fs._normalize_rel(r"src\app.py"), "src/app.py")

    def test_fs_revision_is_stable_and_uses_every_field(self):
        base = {
            "kind": "file",
            "size": 5,
            "mode": stat.S_IFREG | 0o640,
            "mtime_ns": 100,
            "dev": 2,
            "ino": 3,
        }
        revision = explorer_fs._fs_revision(base)
        self.assertEqual(revision, explorer_fs._fs_revision(dict(base)))
        self.assertRegex(revision, r"^[0-9a-f]{16}$")
        for field in base:
            changed = dict(base)
            changed[field] = f"{changed[field]}-changed"
            with self.subTest(field=field):
                self.assertNotEqual(revision, explorer_fs._fs_revision(changed))

    def test_claim_conflicts_are_ancestor_aware_and_namespaced(self):
        conflict = explorer_fs._explorer_claim_keys_conflict
        self.assertTrue(conflict("local|/repo", "local|/repo"))
        self.assertTrue(conflict("local|/repo", "local|/repo/src/app.py"))
        self.assertTrue(conflict("local|/repo/src/app.py", "local|/repo"))
        self.assertFalse(conflict("local|/repo/a", "local|/repo/ab"))
        self.assertFalse(conflict("ssh:a|/repo", "ssh:b|/repo"))

    def test_prescan_enforces_entry_depth_and_byte_limits_before_mutation(self):
        source = self.root / "source"
        source.mkdir()
        (source / "nested").mkdir()
        (source / "nested" / "data.bin").write_bytes(b"1234")
        source_stat = self.backend.fs_lstat(str(source))

        patches = (
            ("EXPLORER_FS_MAX_ENTRIES", 1),
            ("EXPLORER_FS_MAX_DEPTH", 0),
            ("EXPLORER_COPY_MAX_BYTES", 1),
        )
        for constant, value in patches:
            with self.subTest(constant=constant), patch.object(
                explorer_fs, constant, value
            ):
                with self.assertRaises(explorer_fs.ExplorerFsCopyLimitError) as caught:
                    explorer_fs._scan_tree(
                        self.backend,
                        str(source),
                        source_stat,
                        "source",
                        allow_special_entries=False,
                    )
                self.assertFalse(caught.exception.details["mutated"])
        self.assertFalse((self.root / "source-Copy").exists())

    def test_prescan_rejects_a_link_and_names_its_relative_path(self):
        source = self.root / "source"
        source.mkdir()
        target = self.root / "target.txt"
        target.write_text("target", encoding="utf-8")
        link = source / "linked.txt"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"Symlink creation is unavailable: {exc}")

        with self.assertRaises(explorer_fs.ExplorerFsUnsupportedEntryError) as caught:
            explorer_fs._scan_tree(
                self.backend,
                str(source),
                self.backend.fs_lstat(str(source)),
                "source",
                allow_special_entries=False,
            )
        self.assertIn("source/linked.txt", str(caught.exception))

    def test_copy_aborts_and_cleans_up_when_file_outgrows_scanned_size(self):
        source = self.root / "growing.log"
        source.write_bytes(b"before")
        source_stat = self.backend.fs_lstat(str(source))

        def growing_chunks(_path, _chunk_size):
            yield b"before"
            yield b"-after-scan"

        with patch.object(
            self.backend, "fs_read_chunks", side_effect=growing_chunks
        ):
            with self.assertRaises(explorer_fs.ExplorerFsEntryChangedError) as caught:
                explorer_fs.paste_explorer_entry_payload(
                    self.backend,
                    root_revision=explorer_fs._fs_root_revision(str(self.root)),
                    source_path=source.name,
                    source_revision=explorer_fs._fs_revision(source_stat),
                    destination_directory="",
                    session_id="growing-copy",
                )

        self.assertFalse(caught.exception.details["mutated"])
        self.assertEqual(caught.exception.details["path"], source.name)
        self.assertFalse((self.root / "growing-Copy.log").exists())

    def test_sftp_final_destination_uses_exclusive_create_only(self):
        sftp = MagicMock()
        handle = io.BytesIO()
        sftp.open.return_value = handle
        backend = web_explorer._SftpExplorerBackend(
            SimpleNamespace(host="host", port=22, username="user"),
            MagicMock(),
            sftp,
        )

        self.assertIs(backend.fs_create_exclusive("/repo/copy.txt"), handle)
        sftp.open.assert_called_once_with("/repo/copy.txt", "x+b")

        sftp.open.reset_mock()
        sftp.open.side_effect = ValueError("exclusive create unsupported")
        with self.assertRaises(explorer_fs.ExplorerFsIoError):
            explorer_fs._reserve_copy_destination(
                backend, "/repo", "copy.txt", "file"
            )
        sftp.open.assert_called_once_with("/repo/copy.txt", "x+b")

    def test_sftp_copy_streams_and_recursive_delete_uses_one_backend(self):
        content = b"x" * (explorer_fs.EXPLORER_COPY_CHUNK_BYTES * 2 + 17)
        sftp = _MemorySftp(
            {
                "/srv/app": {"kind": "directory"},
                "/srv/app/source": {"kind": "directory"},
                "/srv/app/source/data.bin": {
                    "kind": "file",
                    "content": content,
                    "mode": stat.S_IFREG | 0o640,
                },
            }
        )
        session = SimpleNamespace(
            session_id="remote-fs",
            mode="ssh",
            startup_mode="explorer",
            explorer_root_directory="/srv/app",
            directory="/srv/app",
            host="example.com",
            port=22,
            username="user",
        )
        backend = web_explorer._SftpExplorerBackend(session, MagicMock(), sftp)
        source_stat = backend.fs_lstat("/srv/app/source")
        copied = explorer_fs.paste_explorer_entry_payload(
            backend,
            root_revision=explorer_fs._fs_root_revision("/srv/app"),
            source_path="source",
            source_revision=explorer_fs._fs_revision(source_stat),
            destination_directory="",
            session_id=session.session_id,
        )

        copied_path = "/srv/app/source-Copy/data.bin"
        self.assertEqual(copied["destination_path"], "source-Copy")
        self.assertEqual(sftp.entries[copied_path]["content"], content)
        self.assertEqual(stat.S_IMODE(sftp.entries[copied_path]["mode"]), 0o640)
        self.assertTrue(sftp.read_sizes)
        self.assertLessEqual(
            max(sftp.read_sizes), explorer_fs.EXPLORER_COPY_CHUNK_BYTES
        )
        create_modes = [mode for _path, mode in sftp.open_modes if mode != "rb"]
        self.assertEqual(create_modes, ["x+b"])

        copied_stat = backend.fs_lstat("/srv/app/source-Copy")
        deleted = explorer_fs.delete_explorer_entry_payload(
            backend,
            root_revision=explorer_fs._fs_root_revision("/srv/app"),
            path="source-Copy",
            base_revision=explorer_fs._fs_revision(copied_stat),
            recursive=True,
            session_id=session.session_id,
        )
        self.assertEqual(deleted["deleted_path"], "source-Copy")
        self.assertNotIn("/srv/app/source-Copy", sftp.entries)
        operation_names = [operation[0] for operation in sftp.operations]
        self.assertIn("rename", operation_names)
        self.assertLess(
            operation_names.index("remove"), operation_names.index("rmdir")
        )

    def test_sftp_delete_unlinks_link_without_touching_target(self):
        sftp = _MemorySftp(
            {
                "/srv/app": {"kind": "directory"},
                "/srv/app/target.txt": {"kind": "file", "content": b"keep"},
                "/srv/app/link.txt": {"kind": "link"},
            }
        )
        session = SimpleNamespace(
            session_id="remote-link",
            mode="ssh",
            startup_mode="explorer",
            explorer_root_directory="/srv/app",
            directory="/srv/app",
            host="example.com",
            port=22,
            username="user",
        )
        backend = web_explorer._SftpExplorerBackend(session, MagicMock(), sftp)
        link_stat = backend.fs_lstat("/srv/app/link.txt")

        result = explorer_fs.delete_explorer_entry_payload(
            backend,
            root_revision=explorer_fs._fs_root_revision("/srv/app"),
            path="link.txt",
            base_revision=explorer_fs._fs_revision(link_stat),
            recursive=False,
            session_id=session.session_id,
        )

        self.assertEqual(result["entry_kind"], "link")
        self.assertNotIn("/srv/app/link.txt", sftp.entries)
        self.assertEqual(sftp.entries["/srv/app/target.txt"]["content"], b"keep")


class ExplorerFilesystemRoutesTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "repo"
        self.root.mkdir()
        self.session_id = f"explorer-fs-{id(self)}"
        self.session = _local_session(self.root, self.session_id)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        with api.session_manager.lock:
            api.session_manager.sessions[self.session_id] = self.session

    def tearDown(self):
        with api.session_manager.lock:
            api.session_manager.sessions.pop(self.session_id, None)
        web_explorer._explorer_save_claims.clear()

    def _entries(self, path: str = ""):
        response = self.client.get(
            f"/api/explorer/{self.session_id}/entries",
            query_string={"path": path},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def _entry(self, path: str):
        parent = path.rpartition("/")[0]
        name = path.rpartition("/")[2]
        return next(
            entry for entry in self._entries(parent)["entries"] if entry["name"] == name
        )

    def _paste(self, source: str, destination: str = "", **overrides):
        root_payload = self._entries()
        source_entry = self._entry(source)
        body = {
            "root_revision": root_payload["root_revision"],
            "source_path": source,
            "source_revision": source_entry["revision"],
            "destination_directory": destination,
            **overrides,
        }
        return self.client.post(
            f"/api/explorer/{self.session_id}/paste", json=body
        )

    def _delete(self, path: str, recursive: bool = False, **overrides):
        root_payload = self._entries()
        entry = self._entry(path)
        body = {
            "root_revision": root_payload["root_revision"],
            "path": path,
            "base_revision": entry["revision"],
            "recursive": recursive,
            **overrides,
        }
        return self.client.post(
            f"/api/explorer/{self.session_id}/delete", json=body
        )

    def test_entries_expose_kind_revision_and_root_revision(self):
        (self.root / "app.py").write_text("print(1)\n", encoding="utf-8")

        payload = self._entries()
        entry = payload["entries"][0]

        self.assertRegex(payload["root_revision"], r"^[0-9a-f]{16}$")
        self.assertEqual(entry["entry_kind"], "file")
        self.assertRegex(entry["revision"], r"^[0-9a-f]{16}$")

    def test_file_copy_streams_and_never_overwrites_collision(self):
        source = self.root / "report.md"
        source.write_bytes(b"source")
        first_collision = self.root / "report-Copy.md"
        first_collision.write_bytes(b"existing")

        response = self._paste("report.md")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertEqual(payload["destination_path"], "report-Copy-2.md")
        self.assertEqual((self.root / "report-Copy-2.md").read_bytes(), b"source")
        self.assertEqual(first_collision.read_bytes(), b"existing")
        self.assertEqual(source.read_bytes(), b"source")

    def test_directory_copy_recurses_and_rejects_its_own_descendant(self):
        source = self.root / "archive"
        (source / "nested").mkdir(parents=True)
        (source / "nested" / "note.txt").write_text("hello", encoding="utf-8")

        invalid = self._paste("archive", "archive")
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.get_json()["code"], "invalid_destination")

        copied = self._paste("archive")
        self.assertEqual(copied.status_code, 200, copied.get_data(as_text=True))
        self.assertEqual(
            (self.root / "archive-Copy" / "nested" / "note.txt").read_text(
                encoding="utf-8"
            ),
            "hello",
        )

    def test_stale_root_is_rejected_before_path_resolution(self):
        (self.root / "app.py").write_text("x", encoding="utf-8")
        entry = self._entry("app.py")
        with patch(
            "web.explorer_fs.resolve_mutation_target",
            side_effect=AssertionError("path resolution must not run"),
        ):
            response = self.client.post(
                f"/api/explorer/{self.session_id}/paste",
                json={
                    "root_revision": "stale-root",
                    "source_path": "app.py",
                    "source_revision": entry["revision"],
                    "destination_directory": "",
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "root_changed")

    def test_stale_entry_revision_rejects_copy_without_mutation(self):
        source = self.root / "app.py"
        source.write_text("v1", encoding="utf-8")
        root_revision = self._entries()["root_revision"]
        stale_revision = self._entry("app.py")["revision"]
        source.write_text("changed", encoding="utf-8")
        os.utime(source, None)

        response = self.client.post(
            f"/api/explorer/{self.session_id}/paste",
            json={
                "root_revision": root_revision,
                "source_path": "app.py",
                "source_revision": stale_revision,
                "destination_directory": "",
            },
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "entry_changed")
        self.assertFalse((self.root / "app-Copy.py").exists())

    def test_delete_file_empty_directory_and_confirmed_tree(self):
        (self.root / "file.txt").write_text("delete", encoding="utf-8")
        (self.root / "empty").mkdir()
        tree = self.root / "tree"
        tree.mkdir()
        (tree / "child.txt").write_text("child", encoding="utf-8")

        self.assertEqual(self._delete("file.txt").status_code, 200)
        self.assertFalse((self.root / "file.txt").exists())
        self.assertEqual(self._delete("empty").status_code, 200)
        self.assertFalse((self.root / "empty").exists())

        refused = self._delete("tree", recursive=False)
        self.assertEqual(refused.status_code, 400)
        self.assertTrue(tree.exists())
        accepted = self._delete("tree", recursive=True)
        self.assertEqual(accepted.status_code, 200, accepted.get_data(as_text=True))
        self.assertFalse(tree.exists())

    def test_recursive_delete_protects_direct_and_nested_git_directories(self):
        protected = self.root / ".git"
        protected.mkdir()
        self.assertEqual(self._delete(".git", recursive=True).status_code, 403)
        self.assertTrue(protected.exists())

        project = self.root / "project"
        (project / ".git").mkdir(parents=True)
        (project / ".git" / "HEAD").write_text("ref", encoding="utf-8")
        response = self._delete("project", recursive=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "protected_path")
        self.assertTrue(project.exists())

    def test_delete_symlink_unlinks_leaf_without_touching_target(self):
        target = self.root / "target.txt"
        target.write_text("keep", encoding="utf-8")
        link = self.root / "link.txt"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"Symlink creation is unavailable: {exc}")

        response = self._delete("link.txt")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertFalse(link.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "keep")

    def test_symlinked_parent_escape_is_rejected(self):
        outside = Path(self.temp_dir.name) / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("keep", encoding="utf-8")
        link = self.root / "outside-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Directory symlink creation is unavailable: {exc}")

        root_revision = self._entries()["root_revision"]
        response = self.client.post(
            f"/api/explorer/{self.session_id}/delete",
            json={
                "root_revision": root_revision,
                "path": "outside-link/secret.txt",
                "base_revision": "irrelevant",
                "recursive": False,
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue((outside / "secret.txt").exists())

    def test_failed_copy_cleans_only_its_reserved_destination(self):
        source = self.root / "app.py"
        source.write_text("source", encoding="utf-8")
        sibling = self.root / "keep.txt"
        sibling.write_text("keep", encoding="utf-8")

        with patch.object(
            web_explorer._LocalExplorerBackend,
            "fs_read_chunks",
            side_effect=OSError("simulated read failure"),
        ):
            response = self._paste("app.py")

        self.assertEqual(response.status_code, 500)
        self.assertFalse(response.get_json()["mutated"])
        self.assertFalse((self.root / "app-Copy.py").exists())
        self.assertEqual(sibling.read_text(encoding="utf-8"), "keep")

    def test_overlapping_claims_block_delete_and_save_across_sessions(self):
        directory = self.root / "src"
        directory.mkdir()
        file_path = directory / "app.py"
        file_path.write_text("x\n", encoding="utf-8")
        backend = web_explorer._LocalExplorerBackend(self.session)

        with web_explorer._explorer_path_claims(
            (backend.fs_claim_key(str(directory)),)
        ):
            delete_response = self._delete("src/app.py")
            self.assertEqual(delete_response.status_code, 409)
            self.assertEqual(
                delete_response.get_json()["code"], "operation_in_progress"
            )

            editor_revision = self.client.get(
                f"/api/explorer/{self.session_id}/file",
                query_string={"path": "src/app.py"},
            ).get_json()["revision"]
            save_response = self.client.put(
                f"/api/explorer/{self.session_id}/file",
                json={
                    "path": "src/app.py",
                    "content": "y\n",
                    "base_revision": editor_revision,
                },
            )
            self.assertEqual(save_response.status_code, 409)
            self.assertEqual(save_response.get_json()["code"], "save_in_progress")

    def test_mutation_routes_reject_unknown_fields_and_absolute_paths(self):
        (self.root / "app.py").write_text("x", encoding="utf-8")
        entry = self._entry("app.py")
        root_revision = self._entries()["root_revision"]
        unknown = self.client.post(
            f"/api/explorer/{self.session_id}/paste",
            json={
                "root_revision": root_revision,
                "source_path": "app.py",
                "source_revision": entry["revision"],
                "destination_directory": "",
                "source_session": "another-session",
            },
        )
        self.assertEqual(unknown.status_code, 400)
        absolute = self.client.post(
            f"/api/explorer/{self.session_id}/delete",
            json={
                "root_revision": root_revision,
                "path": str(self.root / "app.py"),
                "base_revision": entry["revision"],
                "recursive": False,
            },
        )
        self.assertEqual(absolute.status_code, 400)
        self.assertTrue((self.root / "app.py").exists())

    def test_mutation_routes_reject_missing_and_non_explorer_sessions(self):
        missing = self.client.post("/api/explorer/missing/paste", json={})
        self.assertEqual(missing.status_code, 404)

        terminal_id = f"terminal-{id(self)}"
        terminal_session = SimpleNamespace(
            session_id=terminal_id,
            mode="wsl",
            startup_mode="terminal",
            directory=str(self.root),
        )
        with api.session_manager.lock:
            api.session_manager.sessions[terminal_id] = terminal_session
        try:
            response = self.client.post(
                f"/api/explorer/{terminal_id}/delete", json={}
            )
        finally:
            with api.session_manager.lock:
                api.session_manager.sessions.pop(terminal_id, None)
        self.assertEqual(response.status_code, 400)


class ExplorerFilesystemFrontendContractTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent.parent
        cls.viewer = (root / "web/static/js/explorer-viewer.js").read_text(
            encoding="utf-8"
        )
        cls.controller = (root / "web/static/js/explorer-fs.js").read_text(
            encoding="utf-8"
        )
        cls.terminals = (root / "web/static/js/terminals.js").read_text(
            encoding="utf-8"
        )
        cls.template = (root / "templates/terminals.html").read_text(
            encoding="utf-8"
        )
        cls.css = (root / "web/static/css/terminals.css").read_text(
            encoding="utf-8"
        )

    def test_tree_and_preview_rows_share_context_metadata(self):
        for attribute in (
            "data-explorer-context-path",
            "data-explorer-context-kind",
            "data-explorer-context-revision",
            "data-explorer-context-surface",
        ):
            self.assertGreaterEqual(self.viewer.count(attribute), 2)
        self.assertIn('data-explorer-context-surface="tree"', self.viewer)
        self.assertIn('data-explorer-context-surface="preview"', self.viewer)
        self.assertIn("wireExplorerContextMenu(list, index);", self.viewer)

    def test_context_menu_supports_disabled_danger_and_keyboard_focus(self):
        self.assertIn("button.disabled = Boolean(disabled);", self.viewer)
        self.assertIn("button.setAttribute('aria-disabled'", self.viewer)
        self.assertIn("button.classList.add('danger');", self.viewer)
        self.assertIn("button:not(:disabled)", self.viewer)
        self.assertIn("!button.classList.contains('danger')", self.viewer)
        self.assertIn("button.textContent = label;", self.viewer)
        self.assertIn("event.clientX", self.viewer)
        self.assertIn("row.getBoundingClientRect()", self.viewer)

    def test_controller_is_session_scoped_confirmed_and_lifecycle_bound(self):
        self.assertIn("const explorerFilesystemClipboards = new Map();", self.controller)
        self.assertIn("'Paste — nothing copied'", self.controller)
        self.assertIn("disabled: !clipboard", self.controller)
        self.assertIn("danger: true", self.controller)
        self.assertIn("owner", self.controller)
        self.assertIn("confirmDiscardExplorerEdit", self.controller)
        self.assertIn("pane._explorerFsBusy", self.controller)
        self.assertIn("function cancelExplorerFilesystemUiForSession", self.controller)
        self.assertIn("function hasActiveExplorerFilesystemOperation", self.controller)
        self.assertIn("closeGenericConfirmModalForOwner", self.terminals)
        for caller in ("closeTerminalPane", "closeSessionGroup", "switchGroup"):
            section = self.terminals[self.terminals.index(f"function {caller}") :]
            self.assertIn("cancelExplorerFilesystemUiForSession", section[:3000])

    def test_new_asset_loads_in_domain_order_and_uses_token_styles(self):
        self.assertLess(
            self.template.index("js/explorer-search.js"),
            self.template.index("js/explorer-fs.js"),
        )
        self.assertLess(
            self.template.index("js/explorer-fs.js"),
            self.template.index("js/terminals.js"),
        )
        style = self.css[
            self.css.index("#explorer-ctx-menu .explorer-ctx-separator") :
            self.css.index("/* placeholder states */")
        ]
        self.assertIn("var(--gv-danger)", style)
        self.assertIn("var(--t-accent)", style)
        self.assertNotRegex(style, r":\s*#[0-9a-fA-F]{3,8}\b")
        for call in ("window.confirm(", "window.alert(", "window.prompt("):
            self.assertNotIn(call, self.controller)


if __name__ == "__main__":
    unittest.main()
