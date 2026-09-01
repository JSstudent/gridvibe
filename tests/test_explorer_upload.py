"""Behavioral coverage for the explorer's file upload — download's mirror.

Three layers, matching where the decisions actually live:

* the route and the mutation policy, exercised against a real temporary root,
  because "never overwrites" and "a refusal leaves nothing behind" are claims
  about a filesystem and are only worth as much as a filesystem answers them;
* a backend recorder, so the primitive contract (exclusive create only, the
  partial removed by the same code that reserved it) is asserted for *any*
  backend rather than for the local one that happens to be convenient;
* the DOM-free destination/refusal rules, executed in Node.

The frontend contract case at the end asserts the affordance is present
wherever download is, since that placement is the feature's whole premise.
"""

import errno
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import api
from web import explorer as web_explorer
from web import explorer_fs

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_JS = REPO_ROOT / "web" / "static" / "js"
UPLOAD_JS = STATIC_JS / "explorer-upload.js"

NODE = shutil.which("node")


def _canonical_temp_root(root: Path) -> Path:
    """Match the local backend's own ``realpath`` spelling of a temp root."""
    root.mkdir()
    return Path(os.path.realpath(root))


class _RecordingBackend:
    """The smallest backend the upload payload can run against.

    Records the filesystem primitives it is asked for, so the tests can assert
    *which* ones an upload is allowed to use. A backend that grew a truncating
    open would show up here as a call this recorder does not implement.
    """

    def __init__(
        self, *, root="/root", existing=None, write_error=None, lost_race=None
    ):
        self._root = root
        self.existing = set(existing or ())
        # Names that pass `lstat` but lose the exclusive create — the window
        # two concurrent uploads of one name actually race in.
        self.lost_race = set(lost_race or ())
        self.calls = []
        self.written = bytearray()
        self.closed = False
        self.removed = []
        self.write_error = write_error
        self.remove_error = None

    # ── root / path mechanics ──
    def root_directory(self):
        return self._root

    def resolve_dir(self, relative):
        self.calls.append(("resolve_dir", relative))
        return self._root, self._root if not relative else f"{self._root}/{relative}"

    def path_inside_root(self, root_path, path):
        return str(path).startswith(str(root_path))

    def rel_explorer_path(self, root_path, path):
        value = str(path)[len(str(root_path)):].lstrip("/")
        return value

    def fs_join(self, parent, name):
        return f"{parent}/{name}"

    def fs_claim_key(self, path):
        return f"claim::{path}"

    def fs_lstat(self, path):
        self.calls.append(("fs_lstat", path))
        return {"kind": "file", "size": 1} if path in self.existing else None

    def created_paths(self):
        return [path for call, path in self.calls if call == "fs_create_exclusive"]

    # ── the two primitives an upload is allowed to use ──
    def fs_create_exclusive(self, path):
        self.calls.append(("fs_create_exclusive", path))
        if path in self.lost_race:
            raise FileExistsError(errno.EEXIST, "File exists")
        backend = self

        class _Handle:
            def write(self, chunk):
                if backend.write_error is not None:
                    raise backend.write_error
                backend.written.extend(chunk)

            def flush(self):
                backend.calls.append(("flush", path))

            def close(self):
                backend.closed = True

        return _Handle()

    def fs_remove_file(self, path):
        self.calls.append(("fs_remove_file", path))
        if self.remove_error is not None:
            raise self.remove_error
        self.removed.append(path)


class ExplorerUploadPolicyTestCase(unittest.TestCase):
    """What the mutation payload refuses, and what it leaves behind when it does."""

    def setUp(self):
        web_explorer._explorer_save_claims.clear()

    def _upload(self, backend, content=b"body", **overrides):
        options = {
            "root_revision": web_explorer._fs_root_revision(backend.root_directory()),
            "destination_directory": "",
            "name": "picture.png",
            "stream": io.BytesIO(content),
            "session_id": "upload-policy",
        }
        options.update(overrides)
        return explorer_fs.upload_explorer_file_payload(backend, **options)

    def test_upload_uses_exclusive_create_only_and_reports_what_it_wrote(self):
        backend = _RecordingBackend()

        payload = self._upload(backend, content=b"0123456789")

        self.assertEqual(payload["destination_path"], "picture.png")
        self.assertEqual(payload["entry_kind"], "file")
        self.assertEqual(payload["size"], 10)
        self.assertEqual(bytes(backend.written), b"0123456789")
        self.assertTrue(backend.closed)
        # Exclusive create is the only way a destination is opened, and the
        # existence check runs before it.
        self.assertIn(("fs_create_exclusive", "/root/picture.png"), backend.calls)
        self.assertLess(
            backend.calls.index(("fs_lstat", "/root/picture.png")),
            backend.calls.index(("fs_create_exclusive", "/root/picture.png")),
        )
        self.assertEqual(backend.removed, [])

    def test_a_taken_name_is_numbered_and_the_existing_file_is_never_opened(self):
        backend = _RecordingBackend(
            existing={"/root/picture.png", "/root/picture (1).png"}
        )

        payload = self._upload(backend, content=b"newer")

        # The next free number, extension-aware.
        self.assertEqual(payload["destination_path"], "picture (2).png")
        self.assertEqual(payload["stored_name"], "picture (2).png")
        self.assertEqual(payload["requested_name"], "picture.png")
        self.assertIs(payload["renamed"], True)
        # The two names already on disk were never opened for writing, which is
        # the whole guarantee: numbering must never become overwriting.
        self.assertEqual(backend.created_paths(), ["/root/picture (2).png"])
        self.assertEqual(bytes(backend.written), b"newer")

    def test_a_free_name_is_used_as_asked_and_reports_no_rename(self):
        backend = _RecordingBackend()

        payload = self._upload(backend)

        self.assertEqual(payload["stored_name"], "picture.png")
        self.assertIs(payload["renamed"], False)

    def test_a_name_lost_to_a_race_steps_to_the_next_one(self):
        """`lstat` only saves a doomed create; ``EEXIST`` is what decides.

        Two uploads of one name can pass the existence check together, so the
        exclusive create has to be the thing that allocates — otherwise the
        loser would overwrite the winner.
        """
        backend = _RecordingBackend(lost_race={"/root/picture.png"})

        payload = self._upload(backend, content=b"second")

        self.assertEqual(payload["stored_name"], "picture (1).png")
        self.assertIs(payload["renamed"], True)
        # It tried the asked-for name, lost it, and took the next — it did not
        # fall back to writing over the winner.
        self.assertEqual(
            backend.created_paths(),
            ["/root/picture.png", "/root/picture (1).png"],
        )
        self.assertEqual(bytes(backend.written), b"second")

    def test_the_numbering_is_bounded_rather_than_looping_forever(self):
        crowded = {
            f"/root/{explorer_fs._upload_name_for_attempt('picture.png', attempt)}"
            for attempt in range(explorer_fs.EXPLORER_COPY_NAME_ATTEMPTS)
        }
        backend = _RecordingBackend(existing=crowded)

        with self.assertRaises(explorer_fs.ExplorerFsDestinationExistsError) as caught:
            self._upload(backend)

        self.assertIs(caught.exception.details["mutated"], False)
        self.assertEqual(backend.created_paths(), [])
        self.assertIn("picture.png", str(caught.exception))

    def test_a_body_past_the_ceiling_takes_its_partial_file_with_it(self):
        backend = _RecordingBackend()

        with patch.object(explorer_fs, "EXPLORER_UPLOAD_MAX_BYTES", 4):
            with self.assertRaises(explorer_fs.ExplorerFsUploadLimitError) as caught:
                self._upload(backend, content=b"far too many bytes")

        self.assertEqual(caught.exception.status_code, 413)
        # Cleaned up, so the failure is retry-safe rather than a half file the
        # user now has to find and delete.
        self.assertEqual(backend.removed, ["/root/picture.png"])
        self.assertIs(caught.exception.details["mutated"], False)

    def test_a_declared_size_past_the_ceiling_never_opens_a_destination(self):
        backend = _RecordingBackend()

        with patch.object(explorer_fs, "EXPLORER_UPLOAD_MAX_BYTES", 4):
            with self.assertRaises(explorer_fs.ExplorerFsUploadLimitError):
                self._upload(backend, content=b"x", declared_size=99)

        self.assertEqual(backend.calls, [])

    def test_a_write_failure_reports_mutation_only_when_cleanup_failed(self):
        cleaned = _RecordingBackend(write_error=OSError("disk went away"))
        with self.assertRaises(explorer_fs.ExplorerFsIoError) as clean_failure:
            self._upload(cleaned)
        self.assertIs(clean_failure.exception.details["mutated"], False)
        self.assertEqual(cleaned.removed, ["/root/picture.png"])

        stranded = _RecordingBackend(write_error=OSError("disk went away"))
        stranded.remove_error = OSError("cannot unlink either")
        with self.assertRaises(explorer_fs.ExplorerFsIoError) as stranded_failure:
            self._upload(stranded)
        # The file may really be there, so the answer says so rather than
        # inviting a retry that would then fail on a collision it caused.
        self.assertIs(stranded_failure.exception.details["mutated"], True)
        self.assertIn("may have been partly written", str(stranded_failure.exception))

    def test_names_follow_the_same_literal_leaf_rules_as_create(self):
        for name in ("../escape", "sub/dir.txt", "", ".", "..", ".git", "C:evil"):
            backend = _RecordingBackend()
            with self.subTest(name=name):
                with self.assertRaises(explorer_fs.ExplorerFsError):
                    self._upload(backend, name=name)
                self.assertEqual(backend.calls, [])

    def test_a_stale_root_revision_is_refused_before_the_destination_resolves(self):
        backend = _RecordingBackend()

        with self.assertRaises(explorer_fs.ExplorerFsRootChangedError):
            self._upload(backend, root_revision="0000000000000000")

        self.assertNotIn(("resolve_dir", ""), backend.calls)


class ExplorerUploadRouteTestCase(unittest.TestCase):
    """The HTTP surface: one file per request, no overwrite, nothing stranded."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = _canonical_temp_root(Path(self.temp_dir.name) / "repo")
        self.session_id = f"explorer-upload-{id(self)}"
        self.session = SimpleNamespace(
            session_id=self.session_id,
            mode="wsl",
            startup_mode="explorer",
            directory=str(self.root),
            explorer_root_directory=str(self.root),
        )
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        with api.session_manager.lock:
            api.session_manager.sessions[self.session_id] = self.session

    def tearDown(self):
        with api.session_manager.lock:
            api.session_manager.sessions.pop(self.session_id, None)
        web_explorer._explorer_save_claims.clear()

    def _root_revision(self):
        response = self.client.get(f"/api/explorer/{self.session_id}/entries")
        self.assertEqual(response.status_code, 200)
        return response.get_json()["root_revision"]

    def _upload(self, name, content=b"payload", destination="", **overrides):
        # `dict.pop(key, default)` evaluates its default eagerly, and this one
        # is a request — a caller that supplied a revision must not still be
        # charged a listing this test may have deliberately made impossible.
        if "root_revision" not in overrides:
            overrides["root_revision"] = self._root_revision()
        data = {
            "root_revision": overrides.pop("root_revision"),
            "destination_directory": destination,
            "name": name,
            "file": (io.BytesIO(content), overrides.pop("part_name", name or "part")),
        }
        data.update(overrides)
        return self.client.post(
            f"/api/explorer/{self.session_id}/upload",
            data=data,
            content_type="multipart/form-data",
        )

    def test_one_upload_writes_exact_bytes_and_shows_up_in_the_listing(self):
        (self.root / "assets").mkdir()

        response = self._upload("logo.bin", b"\x00\x01\x02\xff", destination="assets")

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["destination_path"], "assets/logo.bin")
        self.assertEqual(body["size"], 4)
        self.assertEqual((self.root / "assets" / "logo.bin").read_bytes(), b"\x00\x01\x02\xff")

        listing = self.client.get(
            f"/api/explorer/{self.session_id}/entries", query_string={"path": "assets"}
        ).get_json()
        self.assertEqual([entry["name"] for entry in listing["entries"]], ["logo.bin"])

    def test_an_existing_file_is_kept_and_the_upload_is_numbered_beside_it(self):
        target = self.root / "notes.txt"
        target.write_bytes(b"the original")

        first = self._upload("notes.txt", b"the newer one")
        second = self._upload("notes.txt", b"newer still")

        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertEqual(first.get_json()["destination_path"], "notes (1).txt")
        self.assertIs(first.get_json()["renamed"], True)
        self.assertEqual(second.get_json()["destination_path"], "notes (2).txt")

        # The reason to keep the old one is that it is the previous version, so
        # its bytes have to be exactly what they were.
        self.assertEqual(target.read_bytes(), b"the original")
        self.assertEqual((self.root / "notes (1).txt").read_bytes(), b"the newer one")
        self.assertEqual((self.root / "notes (2).txt").read_bytes(), b"newer still")

    def test_the_part_filename_is_used_only_when_the_client_states_no_name(self):
        response = self.client.post(
            f"/api/explorer/{self.session_id}/upload",
            data={
                "root_revision": self._root_revision(),
                "destination_directory": "",
                "file": (io.BytesIO(b"x"), "from-the-part.txt"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue((self.root / "from-the-part.txt").exists())

    def test_a_traversing_or_protected_name_is_refused_and_writes_nothing(self):
        (self.root / ".git").mkdir()

        for name, destination in (
            ("../escape.txt", ""),
            ("nested/deep.txt", ""),
            (".git", ""),
            ("config", ".git"),
        ):
            with self.subTest(name=name, destination=destination):
                response = self._upload(name, b"x", destination=destination)
                self.assertIn(response.status_code, (400, 403))
                self.assertIs(response.get_json()["mutated"], False)

        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [".git"])
        self.assertEqual(list((self.root / ".git").iterdir()), [])
        self.assertFalse((self.root.parent / "escape.txt").exists())

    def test_a_stale_root_revision_is_refused(self):
        response = self._upload("late.txt", root_revision="0000000000000000")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "root_changed")
        self.assertFalse((self.root / "late.txt").exists())

    def test_a_body_past_the_ceiling_is_refused_and_leaves_no_partial_file(self):
        with patch.object(explorer_fs, "EXPLORER_UPLOAD_MAX_BYTES", 8):
            response = self._upload("big.bin", b"x" * 64)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["code"], "upload_too_large")
        self.assertEqual(list(self.root.iterdir()), [])

    def test_an_oversized_request_is_refused_before_the_body_is_parsed(self):
        """The ceiling is a header read, not a 500 MB temp file.

        The body here is not valid multipart at all: if the route had reached
        ``request.files`` it would have answered "no file part" (400), so the
        413 is proof the length check ran first.
        """
        with patch.object(api, "EXPLORER_UPLOAD_MAX_REQUEST_BYTES", 4):
            response = self.client.post(
                f"/api/explorer/{self.session_id}/upload",
                data=b"this is not multipart at all",
                content_type="multipart/form-data; boundary=nope",
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["code"], "upload_too_large")
        self.assertIs(response.get_json()["mutated"], False)

    def test_the_request_ceiling_sits_above_the_file_ceiling(self):
        self.assertGreater(
            api.EXPLORER_UPLOAD_MAX_REQUEST_BYTES,
            explorer_fs.EXPLORER_UPLOAD_MAX_BYTES,
        )

    def test_a_missing_file_part_is_an_invalid_request(self):
        response = self.client.post(
            f"/api/explorer/{self.session_id}/upload",
            data={"root_revision": self._root_revision(), "destination_directory": ""},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "invalid_request")

    def test_missing_and_non_explorer_sessions_are_refused(self):
        missing = self.client.post(
            "/api/explorer/not-a-session/upload",
            data={"file": (io.BytesIO(b"x"), "a.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(missing.status_code, 404)

        revision = self._root_revision()
        self.session.startup_mode = "shell"
        try:
            response = self._upload("a.txt", root_revision=revision)
        finally:
            self.session.startup_mode = "explorer"
        self.assertEqual(response.status_code, 400)
        self.assertIn("file explorer pane", response.get_json()["error"])
        self.assertFalse((self.root / "a.txt").exists())

    def test_upload_uses_the_cross_origin_write_guard(self):
        with patch.object(api, "_allowed_write_origin_netlocs", return_value={"localhost:5050"}):
            response = self.client.post(
                f"/api/explorer/{self.session_id}/upload",
                data={
                    "root_revision": self._root_revision(),
                    "destination_directory": "",
                    "name": "evil.txt",
                    "file": (io.BytesIO(b"x"), "evil.txt"),
                },
                content_type="multipart/form-data",
                headers={"Origin": "http://evil.example"},
            )

        self.assertEqual(response.status_code, 403)
        self.assertFalse((self.root / "evil.txt").exists())

    def test_an_overlapping_claim_blocks_the_upload_without_touching_the_file(self):
        backend = web_explorer._LocalExplorerBackend(self.session)
        revision = self._root_revision()

        # An editor save (or another mutation) already owns the folder, so the
        # upload waits for it rather than racing it into the same directory.
        with web_explorer._explorer_path_claims((backend.fs_claim_key(str(self.root)),)):
            response = self._upload("held.txt", root_revision=revision)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "operation_in_progress")
        self.assertFalse((self.root / "held.txt").exists())


@unittest.skipUnless(NODE, "Node.js is required for the upload policy tests")
class ExplorerUploadDestinationTestCase(unittest.TestCase):
    """Where the bytes land, answered once for every surface that asks."""

    def _run_node(self, body: str):
        harness = "const upload = require(process.argv[2]);\n" + body
        with TemporaryDirectory() as script_dir:
            script_path = Path(script_dir) / "harness.js"
            script_path.write_text(harness, encoding="utf-8")
            completed = subprocess.run(
                [NODE, str(script_path), str(UPLOAD_JS)],
                capture_output=True,
                text=True,
                check=False,
            )
        if completed.returncode != 0:
            self.fail(f"node harness failed:\n{completed.stderr}")
        return json.loads(completed.stdout)

    def test_a_folder_names_itself_and_a_file_names_its_folder(self):
        answers = self._run_node(
            "const at = ctx => upload.uploadDestination(ctx);"
            "console.log(JSON.stringify({"
            "  treeFolder: at({ surface: 'tree', kind: 'directory', path: 'web/static' }),"
            "  treeFile: at({ surface: 'tree', kind: 'file', path: 'web/static/app.js' }),"
            "  treeRootFile: at({ surface: 'tree', kind: 'file', path: 'README.md' }),"
            "  treeBlank: at({ surface: 'tree-blank', kind: 'directory', path: '' }),"
            "  listingFolder: at({ surface: 'preview', kind: 'directory', path: 'docs/img',"
            "                      listingPath: 'docs' }),"
            "  listingFile: at({ surface: 'preview', kind: 'file', path: 'docs/a.md',"
            "                    listingPath: 'docs' }),"
            "  listingBlank: at({ surface: 'preview-blank', kind: 'directory',"
            "                     path: 'docs', listingPath: 'docs' }),"
            "  tab: at({ surface: 'tab', kind: 'file', path: 'web/api.py' }),"
            "  gitRow: at({ surface: 'git', kind: 'file', path: 'web/explorer.py' }),"
            "  openFile: at({ surface: 'file-view', kind: 'file', path: 'web/static/css/app.css' })"
            "}));"
        )

        self.assertEqual(answers["treeFolder"], "web/static")
        self.assertEqual(answers["treeFile"], "web/static")
        # A root-level file names the root, and '' is a real destination.
        self.assertEqual(answers["treeRootFile"], "")
        self.assertEqual(answers["treeBlank"], "")
        self.assertEqual(answers["listingFolder"], "docs/img")
        self.assertEqual(answers["listingFile"], "docs")
        self.assertEqual(answers["listingBlank"], "docs")
        self.assertEqual(answers["tab"], "web")
        self.assertEqual(answers["gitRow"], "web")
        self.assertEqual(answers["openFile"], "web/static/css")

    def test_the_pane_bar_uploads_into_whatever_the_bar_is_showing(self):
        answers = self._run_node(
            "console.log(JSON.stringify({"
            "  browsing: upload.uploadDestination({ surface: 'bar', kind: 'directory',"
            "                                       path: 'web/static' }),"
            "  root: upload.uploadDestination({ surface: 'bar', kind: 'directory', path: '' }),"
            "  openFile: upload.uploadDestination({ surface: 'bar', kind: 'file',"
            "                                       path: 'web/static/app.js' })"
            "}));"
        )

        # The same rule the reveal-in-OS button beside it follows: the listed
        # folder, or the open file's folder.
        self.assertEqual(answers["browsing"], "web/static")
        self.assertEqual(answers["root"], "")
        self.assertEqual(answers["openFile"], "web/static")

    def test_a_surface_that_cannot_name_a_folder_answers_null(self):
        answers = self._run_node(
            "console.log(JSON.stringify({"
            "  unknown: upload.uploadDestination({ surface: 'somewhere', kind: 'file', path: 'a' }),"
            "  pathless: upload.uploadDestination({ surface: 'tab', kind: 'file', path: '' }),"
            "  nothing: upload.uploadDestination({})"
            "}));"
        )

        # Never a silent fall back to the explorer root: that is the one place
        # the user was demonstrably not looking.
        self.assertIsNone(answers["unknown"])
        self.assertIsNone(answers["pathless"])
        self.assertIsNone(answers["nothing"])

    def test_the_plan_names_every_file_it_will_not_send(self):
        result = self._run_node(
            "const plan = upload.uploadPlan(["
            "  { name: 'ok.txt', size: 10 },"
            "  { name: 'sub/dir.txt', size: 10 },"
            "  { name: '.git', size: 1 },"
            "  { name: 'huge.bin', size: upload.UPLOAD_MAX_BYTES + 1 },"
            "  { name: 'ok.txt', size: 12 },"
            "  { name: '', size: 3 }"
            "]);"
            "console.log(JSON.stringify({"
            "  accepted: plan.accepted.map(row => row.name),"
            "  rejected: plan.rejected,"
            "  message: upload.uploadRejectionMessage(plan.rejected),"
            "  none: upload.uploadRejectionMessage([])"
            "}));"
        )

        # Both `ok.txt` entries are sent: the server numbers the second rather
        # than refusing it, so refusing it here would be inventing a limit the
        # server does not have.
        self.assertEqual(result["accepted"], ["ok.txt", "ok.txt"])
        self.assertEqual(
            [row["name"] for row in result["rejected"]],
            ["sub/dir.txt", ".git", "huge.bin", "File 6"],
        )
        # A separator is refused outright, never quietly trimmed to its leaf.
        self.assertIn("path separator", result["rejected"][0]["reason"])
        self.assertIn("4 files were not uploaded", result["message"])
        self.assertEqual(result["none"], "")

    def test_a_rename_is_reported_from_the_names_the_server_sent_back(self):
        """A silent rename is the one outcome an upload must not have."""
        result = self._run_node(
            "const row = (ok, data) => ({ ok, data });"
            "console.log(JSON.stringify({"
            "  none: upload.uploadRenameNote(["
            "    row(true, { renamed: false, requested_name: 'a', stored_name: 'a' })"
            "  ]),"
            "  one: upload.uploadRenameNote(["
            "    row(true, { renamed: false, requested_name: 'a', stored_name: 'a' }),"
            "    row(true, { renamed: true, requested_name: 'report.pdf',"
            "                stored_name: 'report (1).pdf' })"
            "  ]),"
            "  many: upload.uploadRenameNote(["
            "    row(true, { renamed: true, requested_name: 'a.txt', stored_name: 'a (1).txt' }),"
            "    row(true, { renamed: true, requested_name: 'b.txt', stored_name: 'b (3).txt' })"
            "  ]),"
            "  failures: upload.uploadRenameNote(["
            "    row(false, { renamed: true, requested_name: 'a', stored_name: 'a (1)' })"
            "  ]),"
            "  empty: upload.uploadRenameNote([])"
            "}));"
        )

        self.assertEqual(result["none"], "")
        self.assertEqual(result["empty"], "")
        # A row that failed never stored anything, so it names nothing.
        self.assertEqual(result["failures"], "")
        self.assertIn("report.pdf was kept as report (1).pdf", result["one"])
        self.assertIn("existing file is untouched", result["one"])
        self.assertIn("2 files were numbered", result["many"])
        # The name reported is the one the server sent, never re-derived here.
        self.assertIn("a (1).txt", result["many"])

    def test_an_accepted_entry_hands_back_the_handle_it_was_picked_with(self):
        """The regression: a plan entry *wraps* the picked object.

        Reading `.blob` straight off an accepted entry sent `undefined` to
        `FormData.append`, which every browser answers with "parameter 2 is not
        of type 'Blob'" — on every surface and both session kinds, because the
        mistake is in the one line all of them share.
        """
        result = self._run_node(
            "const blob = { marker: 'the real file' };"
            "const plan = upload.uploadPlan(["
            "  { name: 'browser.txt', size: 4, blob },"
            "  { name: 'native.txt', size: 8, sourcePath: '/home/me/native.txt' }"
            "]);"
            "const browser = upload.uploadEntrySource(plan.accepted[0]);"
            "const native = upload.uploadEntrySource(plan.accepted[1]);"
            "console.log(JSON.stringify({"
            "  browserIsTheSameBlob: browser.blob === blob,"
            "  browserName: browser.name,"
            "  browserPath: browser.sourcePath,"
            "  nativePath: native.sourcePath,"
            "  nativeBlob: native.blob,"
            "  rawPicked: upload.uploadEntrySource({ name: 'x', blob }).blob === blob,"
            "  handleless: upload.uploadEntrySource({ name: 'gone.txt', size: 1 }),"
            "  nothing: upload.uploadEntrySource(null)"
            "}));"
        )

        self.assertTrue(result["browserIsTheSameBlob"])
        self.assertEqual(result["browserName"], "browser.txt")
        self.assertEqual(result["browserPath"], "")
        self.assertEqual(result["nativePath"], "/home/me/native.txt")
        self.assertIsNone(result["nativeBlob"])
        # A retry may hand back the raw picked shape, so both are accepted.
        self.assertTrue(result["rawPicked"])
        # No handle means no request: a body that cannot name its bytes must
        # never reach FormData.
        self.assertIsNone(result["handleless"])
        self.assertIsNone(result["nothing"])

    def test_a_planned_browser_entry_builds_a_real_multipart_body(self):
        """Executed against Node's own FormData/File, which is the browser's.

        The unit under test is the exact expression the page runs, so a handle
        the plan does not actually carry fails here the same way it failed in
        the pane.
        """
        result = self._run_node(
            "const file = new File([new Uint8Array([1, 2, 3, 4])], 'logo.bin');"
            "const plan = upload.uploadPlan([{ name: file.name, size: file.size, blob: file }]);"
            "const entry = plan.accepted[0];"
            "const source = upload.uploadEntrySource(entry);"
            "const body = new FormData();"
            "body.append('root_revision', 'rev-1');"
            "body.append('destination_directory', 'docs');"
            "body.append('name', entry.name);"
            "body.append('file', source.blob, entry.name);"
            "const part = body.get('file');"
            "console.log(JSON.stringify({"
            "  isFile: part instanceof Blob,"
            "  partName: part.name,"
            "  size: part.size,"
            "  revision: body.get('root_revision'),"
            "  destination: body.get('destination_directory')"
            "}));"
        )

        self.assertTrue(result["isFile"])
        self.assertEqual(result["partName"], "logo.bin")
        self.assertEqual(result["size"], 4)
        self.assertEqual(result["revision"], "rev-1")
        self.assertEqual(result["destination"], "docs")

    def test_the_file_count_is_bounded_and_a_large_batch_asks_first(self):
        result = self._run_node(
            "const many = Array.from({ length: upload.UPLOAD_MAX_FILES + 3 },"
            "  (_, i) => ({ name: `f${i}.txt`, size: 1 }));"
            "const plan = upload.uploadPlan(many);"
            "console.log(JSON.stringify({"
            "  accepted: plan.accepted.length,"
            "  rejected: plan.rejected.length,"
            "  small: upload.uploadConfirmCopy(plan.accepted.slice(0, 3), 'docs'),"
            "  atThreshold: upload.uploadConfirmCopy("
            "    plan.accepted.slice(0, upload.UPLOAD_CONFIRM_THRESHOLD), 'docs'),"
            "  big: upload.uploadConfirmCopy(plan.accepted.slice(0, 25), ''),"
            "  label: upload.uploadMenuLabel(plan.accepted.slice(0, 4))"
            "}));"
        )

        self.assertEqual(result["accepted"], 500)
        self.assertEqual(result["rejected"], 3)
        self.assertIsNone(result["small"])
        self.assertIsNone(result["atThreshold"])
        self.assertEqual(result["big"]["title"], "Upload 25 files?")
        # The expensive half of the mistake is the folder, so it is named.
        self.assertIn("the explorer root", result["big"]["copy"])
        self.assertEqual(result["label"], "Upload 4 files")


class ExplorerUploadFrontendContractTestCase(unittest.TestCase):
    """Upload is offered wherever download is — that placement is the feature."""

    @classmethod
    def setUpClass(cls):
        cls.template = (REPO_ROOT / "templates" / "terminals.html").read_text(encoding="utf-8")
        cls.viewer = (STATIC_JS / "explorer-viewer.js").read_text(encoding="utf-8")
        cls.controller = (STATIC_JS / "explorer-fs.js").read_text(encoding="utf-8")
        cls.policy = UPLOAD_JS.read_text(encoding="utf-8")
        cls.css = (REPO_ROOT / "web" / "static" / "css" / "terminals.css").read_text(
            encoding="utf-8"
        )

    def test_the_policy_module_loads_before_both_of_its_adapters(self):
        for dependent in ("js/explorer-viewer.js", "js/explorer-fs.js"):
            self.assertLess(
                self.template.index("js/explorer-upload.js"),
                self.template.index(dependent),
            )

    def test_the_explorer_bar_carries_an_upload_button_on_both_render_paths(self):
        page = (STATIC_JS / "terminals.js").read_text(encoding="utf-8")
        # Both paths that build an explorer surface: the grid render and the
        # terminal-to-explorer mode switch. A button on only one of them is a
        # pane that loses it the moment its mode changes.
        self.assertEqual(page.count('class="explorer-bar-upload"'), 2)
        self.assertEqual(page.count("startExplorerPaneUpload("), 2)
        # Remote panes get it too — unlike reveal-in-OS, an upload means
        # something over SFTP.
        self.assertNotIn(
            "session.mode === 'ssh' ? '' : `<button type=\"button\" class=\"explorer-bar-upload\"",
            page,
        )

    def test_every_header_that_offers_download_offers_upload(self):
        download_buttons = self.viewer.count('class="explorer-download-btn"')
        upload_buttons = self.viewer.count('class="explorer-upload-btn"')
        self.assertEqual(download_buttons, 2)
        self.assertEqual(upload_buttons, download_buttons)
        self.assertIn('data-explorer-upload="${index}"', self.viewer)

    def test_the_context_menu_entry_is_derived_from_the_shared_policy(self):
        # The menu must not re-derive a destination of its own: one rule, so a
        # right-click and the header button cannot disagree about "here".
        self.assertIn("uploadPolicy.uploadDestination(", self.viewer)
        self.assertIn("startExplorerUpload(index, uploadContext)", self.viewer)

    def test_the_upload_button_shares_the_download_button_styles(self):
        for rule in (
            ".explorer-upload-btn,",
            ".explorer-upload-btn:hover,",
            ".explorer-upload-btn svg,",
        ):
            self.assertIn(rule, self.css)
        # Guardrail 7: no palette literal beside the reused rule.
        block = self.css[
            self.css.index(".explorer-download-btn,") : self.css.index(
                ".explorer-line-wrap-btn[hidden]"
            )
        ]
        self.assertNotRegex(block, r":\s*#[0-9a-fA-F]{3,8}\b")

    def test_the_controller_never_uses_a_blocking_browser_dialog(self):
        for call in ("window.confirm(", "window.alert(", "window.prompt("):
            self.assertNotIn(call, self.controller)
        # The batch rules the rest of the module lives by apply here too.
        self.assertIn("runExplorerFilesystemBatch(", self.controller)
        self.assertIn("openGenericConfirmModal(", self.controller)

    def test_the_policy_module_is_dom_free(self):
        for symbol in ("document.", "window.", "querySelector"):
            self.assertNotIn(symbol, self.policy)


if __name__ == "__main__":
    unittest.main()
