"""Bounded directory ZIP behavior shared by local and SFTP explorers."""

import io
import os
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from web.explorer import _LocalExplorerBackend
from web.explorer_download import (
    ExplorerDirectoryDownloadLimitError,
    build_explorer_directory_archive,
)


class ExplorerDirectoryDownloadTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = _LocalExplorerBackend(
            SimpleNamespace(
                directory=str(self.root),
                explorer_root_directory=str(self.root),
                mode="wsl",
                startup_mode="explorer",
            )
        )

    def _archive(self, path, *, max_bytes=1024 * 1024, chunk_bytes=7):
        archive = build_explorer_directory_archive(
            self.backend,
            path,
            max_bytes=max_bytes,
            chunk_bytes=chunk_bytes,
        )
        self.addCleanup(archive.handle.close)
        return archive

    def test_nested_files_and_empty_directories_keep_one_folder_root(self):
        source = self.root / "assets"
        (source / "nested" / "empty").mkdir(parents=True)
        (source / "hello.txt").write_text("hello", encoding="utf-8")
        (source / "nested" / "data.bin").write_bytes(b"\x00\x01\xff")

        archive = self._archive("assets")

        self.assertEqual(archive.filename, "assets.zip")
        self.assertEqual(archive.size, len(archive.handle.read()))
        archive.handle.seek(0)
        with zipfile.ZipFile(archive.handle) as zipped:
            self.assertEqual(
                zipped.namelist(),
                [
                    "assets/",
                    "assets/nested/",
                    "assets/nested/empty/",
                    "assets/hello.txt",
                    "assets/nested/data.bin",
                ],
            )
            self.assertEqual(zipped.read("assets/hello.txt"), b"hello")
            self.assertEqual(zipped.read("assets/nested/data.bin"), b"\x00\x01\xff")

    def test_known_content_over_the_cap_is_rejected_before_a_file_is_opened(self):
        source = self.root / "large"
        source.mkdir()
        (source / "one.bin").write_bytes(b"1" * 9)
        (source / "two.bin").write_bytes(b"2" * 9)

        with patch.object(self.backend, "open_file_stream") as open_stream:
            with self.assertRaisesRegex(
                ExplorerDirectoryDownloadLimitError, "contents exceed"
            ):
                build_explorer_directory_archive(
                    self.backend,
                    "large",
                    max_bytes=16,
                    chunk_bytes=8,
                )

        open_stream.assert_not_called()

    def test_archive_overhead_is_also_inside_the_download_cap(self):
        (self.root / "empty").mkdir()

        with self.assertRaisesRegex(
            ExplorerDirectoryDownloadLimitError, "archive exceeds"
        ):
            build_explorer_directory_archive(
                self.backend,
                "empty",
                max_bytes=16,
                chunk_bytes=8,
            )

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks are unavailable")
    def test_links_are_not_followed_into_an_archive(self):
        source = self.root / "linked"
        source.mkdir()
        target = self.root / "target.txt"
        target.write_text("outside the selected folder", encoding="utf-8")
        link = source / "alias.txt"
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        with self.assertRaisesRegex(ValueError, "unsupported entry"):
            self._archive("linked")

    def test_a_backend_link_entry_is_refused_before_any_open(self):
        class LinkBackend:
            def resolve_dir(self, _requested):
                return "/root", "/root/folder"

            def basename(self, _path):
                return "folder"

            def rel_explorer_path(self, root, path):
                return path.removeprefix(f"{root}/")

            def fs_listdir(self, _path):
                return [("alias", {"kind": "link", "size": 4})]

            def fs_join(self, parent, name):
                return f"{parent}/{name}"

            def open_file_stream(self, _path):
                raise AssertionError("a link must never be opened")

        with self.assertRaisesRegex(ValueError, "unsupported entry"):
            build_explorer_directory_archive(
                LinkBackend(),
                "folder",
                max_bytes=1024,
                chunk_bytes=8,
            )

    def test_a_file_that_grows_after_listing_is_stopped_by_the_read_ceiling(self):
        reads = []

        class GrowingBackend:
            def resolve_dir(self, _requested):
                return "/root", "/root/folder"

            def basename(self, _path):
                return "folder"

            def rel_explorer_path(self, root, path):
                return path.removeprefix(f"{root}/")

            def resolve_file(self, requested):
                return "/root", f"/root/{requested}"

            def fs_listdir(self, path):
                if path == "/root/folder":
                    return [("growing.bin", {"kind": "file", "size": 0})]
                return []

            def fs_join(self, parent, name):
                return f"{parent}/{name}"

            def open_file_stream(self, _path):
                class RecordingStream(io.BytesIO):
                    def read(self, size=-1):
                        reads.append(size)
                        return super().read(size)

                return RecordingStream(b"x" * 300)

        with self.assertRaisesRegex(
            ExplorerDirectoryDownloadLimitError, "contents exceed"
        ):
            build_explorer_directory_archive(
                GrowingBackend(),
                "folder",
                max_bytes=256,
                chunk_bytes=7,
            )

        self.assertTrue(reads)
        self.assertLessEqual(max(reads), 7)


if __name__ == "__main__":
    unittest.main()
