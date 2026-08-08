"""Stage 1: durability of ``saved_sessions.json`` and the Fernet key (SGP-05).

The preset store is the only GridVibe file that holds an encrypted SSH
password, and it used to be written with three independent load-modify-save
pairs, a truncating ``open(..., "w")``, and a read path that turned an
unreadable file into an empty store the next save made permanent. These tests
pin the properties the store must now have:

* one locked read-modify-write transaction, so a concurrent unrelated change
  survives;
* a unique temporary file plus atomic replace, so a failed commit leaves the
  previous presets readable and no scratch file behind;
* quarantine plus last-good recovery instead of a silent empty store;
* honest acknowledgement — a failed write or delete raises and the route
  answers retryable, never "saved";
* no plaintext password in the file, the backup, the quarantine copy, an
  exception message, or a log line;
* first-run encryption-key creation that is exclusive and never observable
  half-written.
"""

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import web.api as api  # noqa: E402
import web.saved_session_store as web_saved_session_store  # noqa: E402
import web.saved_sessions as web_saved_sessions  # noqa: E402
import web.secrets as web_secrets  # noqa: E402
import web.state_files as web_state_files  # noqa: E402

PLAINTEXT_PASSWORD = "correct-horse-battery-staple"


def _config(host: str = "10.0.0.5", password: str = "") -> dict:
    """A minimal launcher preset config."""
    return {
        "connection_mode": "ssh",
        "terminal_count": 1,
        "ssh": {"host": host, "username": "ubuntu", "password": password, "port": 22},
    }


class SavedSessionStoreTestBase(unittest.TestCase):
    """Redirect the preset file at the module global every test patches."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store_path = Path(self.temp_dir.name) / "saved_sessions.json"
        patcher = patch.object(
            web_saved_sessions, "SAVED_SESSIONS_PATH", str(self.store_path)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _stored_ids(self):
        return [entry["id"] for entry in web_saved_sessions.load_saved_sessions()]

    def _temp_files(self):
        return [entry.name for entry in Path(self.temp_dir.name).iterdir() if entry.name.endswith(".tmp")]

    def _quarantined(self):
        return [entry for entry in Path(self.temp_dir.name).iterdir() if ".corrupt-" in entry.name]


class SavedSessionTransactionTestCase(SavedSessionStoreTestBase):
    """One locked read-modify-write per mutation, not a load/save pair."""

    def test_concurrent_upserts_of_different_presets_all_survive(self):
        """The lost-update path: every writer's own preset must still be there."""
        writers = 8
        ready = threading.Barrier(writers)
        created = []
        lock = threading.Lock()

        def write(index: int):
            ready.wait(10)
            entry = web_saved_sessions.upsert_saved_session(
                config=_config(host=f"10.0.0.{index}"),
                name=f"Preset {index}",
            )
            with lock:
                created.append(entry["id"])

        threads = [threading.Thread(target=write, args=(index,)) for index in range(writers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)

        self.assertEqual(len(created), writers)
        self.assertEqual(sorted(self._stored_ids()), sorted(created))

    def test_a_concurrent_delete_and_upsert_leave_both_intentions_applied(self):
        keeper = web_saved_sessions.upsert_saved_session(config=_config(), name="Keep")
        doomed = web_saved_sessions.upsert_saved_session(config=_config(), name="Delete me")
        ready = threading.Barrier(2)

        def delete():
            ready.wait(10)
            web_saved_sessions.delete_saved_sessions([doomed["id"]])

        def add():
            ready.wait(10)
            web_saved_sessions.upsert_saved_session(config=_config(), name="Newcomer")

        threads = [threading.Thread(target=delete), threading.Thread(target=add)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)

        stored = self._stored_ids()
        self.assertIn(keeper["id"], stored)
        self.assertNotIn(doomed["id"], stored)
        self.assertIn("Newcomer", [entry["name"] for entry in web_saved_sessions.load_saved_sessions()])

    def test_a_paused_writer_blocks_the_next_transaction_until_it_commits(self):
        """The next writer must read the paused writer's result, not the old file."""
        store = web_saved_sessions._saved_session_store
        entered = threading.Event()
        release = threading.Event()
        observed = {}
        committed = {"last_session": "", "sessions": []}

        def slow(_stored):
            entered.set()
            release.wait(10)
            return committed, None

        def follower(stored):
            observed["payload"] = stored
            return web_saved_session_store.UNCHANGED, None

        original = web_saved_sessions.upsert_saved_session(config=_config(), name="Original")

        writer = threading.Thread(target=store.transaction, args=(slow,))
        writer.start()
        self.assertTrue(entered.wait(10))

        second = threading.Thread(target=store.transaction, args=(follower,))
        second.start()
        second.join(0.4)

        # Still blocked: it must not have read (and later replaced) stale state.
        self.assertTrue(second.is_alive())
        self.assertNotIn("payload", observed)

        release.set()
        writer.join(10)
        second.join(10)

        # It read the paused writer's commit, never the file that preceded it.
        self.assertEqual(observed["payload"], committed)
        self.assertNotIn(
            original["id"],
            [entry["id"] for entry in observed["payload"]["sessions"]],
        )

    def test_the_cross_process_lock_is_exclusive_and_released(self):
        lock = web_saved_session_store._CrossProcessSavedSessionLock(str(self.store_path))
        blocked = {}

        with lock:
            def contend():
                try:
                    with web_saved_session_store._CrossProcessSavedSessionLock(
                        str(self.store_path), timeout=0.2
                    ):
                        blocked["acquired"] = True
                except web_saved_session_store.SavedSessionsPersistenceError as exc:
                    blocked["error"] = exc

            worker = threading.Thread(target=contend)
            worker.start()
            worker.join(10)

        self.assertNotIn("acquired", blocked)
        self.assertIn("error", blocked)
        with web_saved_session_store._CrossProcessSavedSessionLock(str(self.store_path), timeout=2):
            pass

    def test_selecting_the_built_in_default_on_an_empty_store_writes_nothing(self):
        state = web_saved_sessions.set_last_saved_session("does-not-exist")

        self.assertEqual(state["sessions"], [])
        self.assertFalse(self.store_path.exists())


class SavedSessionWriteFailureTestCase(SavedSessionStoreTestBase):
    """A commit that did not reach the disk must not be reported as stored."""

    def test_a_failed_replace_keeps_the_previous_presets_and_leaves_no_temp_file(self):
        keeper = web_saved_sessions.upsert_saved_session(config=_config(), name="Keep")
        before = self.store_path.read_bytes()

        with patch("os.replace", side_effect=OSError("locked by antivirus")):
            with self.assertRaises(web_saved_session_store.SavedSessionsPersistenceError):
                web_saved_sessions.upsert_saved_session(config=_config(), name="Doomed")

        self.assertEqual(self.store_path.read_bytes(), before)
        self.assertEqual(self._stored_ids(), [keeper["id"]])
        self.assertEqual(self._temp_files(), [])

    def test_a_failed_temporary_write_raises_instead_of_truncating_the_store(self):
        keeper = web_saved_sessions.upsert_saved_session(config=_config(), name="Keep")
        before = self.store_path.read_bytes()

        # Scoped to the writer: the old in-place `open(path, "w")` truncated the
        # live file before it could fail, which is the torn-write path itself.
        with patch(
            "web.state_files.open",
            side_effect=OSError("No space left on device"),
            create=True,
        ):
            with self.assertRaises(web_saved_session_store.SavedSessionsPersistenceError):
                web_saved_sessions.upsert_saved_session(config=_config(), name="Doomed")

        self.assertEqual(self.store_path.read_bytes(), before)
        self.assertEqual(self._stored_ids(), [keeper["id"]])
        self.assertEqual(self._temp_files(), [])

    def test_a_failed_delete_of_the_last_preset_raises_instead_of_reporting_success(self):
        doomed = web_saved_sessions.upsert_saved_session(config=_config(), name="Only one")

        with patch("os.remove", side_effect=OSError("permission denied")):
            with self.assertRaises(web_saved_session_store.SavedSessionsPersistenceError):
                web_saved_sessions.delete_saved_sessions([doomed["id"]])

        # The presets are still on disk, which is exactly why the caller must
        # not have been told the deletion succeeded.
        self.assertTrue(self.store_path.exists())
        self.assertEqual(self._stored_ids(), [doomed["id"]])

    def test_every_commit_uses_its_own_temporary_path(self):
        seen = []
        real_replace = os.replace

        def record(src, dst):
            seen.append(os.path.basename(str(src)))
            return real_replace(src, dst)

        with patch("os.replace", side_effect=record):
            web_saved_sessions.upsert_saved_session(config=_config(), name="One")
            web_saved_sessions.upsert_saved_session(config=_config(), name="Two")

        self.assertEqual(len(seen), 2)
        self.assertEqual(len(set(seen)), 2)


class SavedSessionRecoveryTestCase(SavedSessionStoreTestBase):
    """A damaged file is quarantined and recovered, never laundered to empty."""

    def _two_commits(self):
        first = web_saved_sessions.upsert_saved_session(config=_config(), name="First")
        second = web_saved_sessions.upsert_saved_session(config=_config(), name="Second")
        # The second commit backs the first one up, so a last-good file exists.
        self.assertTrue(Path(f"{self.store_path}.bak").exists())
        return first, second

    def test_a_corrupt_primary_recovers_the_last_good_backup(self):
        first, _second = self._two_commits()
        self.store_path.write_text("{ this is not json", encoding="utf-8")

        stored = self._stored_ids()

        self.assertEqual(stored, [first["id"]])
        self.assertEqual(len(self._quarantined()), 1)

    def test_an_unsupported_payload_is_quarantined_rather_than_read_as_empty(self):
        first, _second = self._two_commits()
        self.store_path.write_text('"not a preset store"', encoding="utf-8")

        self.assertEqual(self._stored_ids(), [first["id"]])
        self.assertEqual(len(self._quarantined()), 1)

    def test_a_corrupt_store_survives_the_next_successful_save(self):
        """The damaged bytes must outlive the save that replaces them."""
        self._two_commits()
        corrupt = "{ half-written"
        self.store_path.write_text(corrupt, encoding="utf-8")

        web_saved_sessions.upsert_saved_session(config=_config(), name="Later")

        quarantined = self._quarantined()
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(encoding="utf-8"), corrupt)

    def test_a_missing_file_is_an_empty_store_not_a_recovery(self):
        self.assertEqual(web_saved_sessions.load_saved_sessions(), [])
        self.assertEqual(self._quarantined(), [])


class SavedSessionSecretHandlingTestCase(SavedSessionStoreTestBase):
    """The one file holding an SSH password must not leak it anywhere."""

    def _save_with_password(self):
        return web_saved_sessions.upsert_saved_session(
            config=_config(password=PLAINTEXT_PASSWORD), name="Secret"
        )

    def test_the_stored_file_backup_and_quarantine_carry_no_plaintext(self):
        self._save_with_password()
        web_saved_sessions.upsert_saved_session(config=_config(), name="Second")
        self.store_path.write_text("{ broken", encoding="utf-8")
        web_saved_sessions.load_saved_sessions()

        for path in Path(self.temp_dir.name).iterdir():
            if path.is_file():
                self.assertNotIn(
                    PLAINTEXT_PASSWORD,
                    path.read_text(encoding="utf-8", errors="ignore"),
                    f"{path.name} leaked the plaintext password",
                )

    def test_the_password_still_round_trips_through_the_store(self):
        entry = self._save_with_password()

        reloaded = [
            item for item in web_saved_sessions.load_saved_sessions()
            if item["id"] == entry["id"]
        ][0]

        self.assertEqual(reloaded["config"]["ssh"]["password"], PLAINTEXT_PASSWORD)

    def test_a_write_failure_reports_no_plaintext_in_the_error_or_the_log(self):
        with self.assertLogs("web", level="DEBUG") as captured:
            with patch("os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(
                    web_saved_session_store.SavedSessionsPersistenceError
                ) as raised:
                    self._save_with_password()

        self.assertNotIn(PLAINTEXT_PASSWORD, str(raised.exception))
        for line in captured.output:
            self.assertNotIn(PLAINTEXT_PASSWORD, line)


class SavedSessionRouteFailureTestCase(SavedSessionStoreTestBase):
    """A route never claims success for a preset that did not reach the disk."""

    def setUp(self):
        super().setUp()
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()

    def test_saving_a_preset_answers_retryable_instead_of_201(self):
        with patch(
            "web.api.upsert_saved_session",
            side_effect=web_saved_session_store.SavedSessionsPersistenceError("disk full"),
        ):
            response = self.client.post(
                "/api/saved-sessions",
                json={"name": "Secret", "config": _config(password=PLAINTEXT_PASSWORD)},
            )

        self.assertEqual(response.status_code, 503)
        body = response.get_json()
        self.assertTrue(body["retryable"])
        self.assertFalse(body["saved"])
        self.assertNotIn(PLAINTEXT_PASSWORD, response.get_data(as_text=True))

    def test_deleting_a_preset_answers_retryable_instead_of_success(self):
        entry = web_saved_sessions.upsert_saved_session(config=_config(), name="Keep")

        with patch(
            "web.api.delete_saved_sessions",
            side_effect=web_saved_session_store.SavedSessionsPersistenceError("read-only fs"),
        ):
            response = self.client.delete("/api/saved-sessions", json={"ids": [entry["id"]]})

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.get_json()["retryable"])
        # The preset is still there, which is what the caller was told.
        self.assertEqual(self._stored_ids(), [entry["id"]])

    def test_persisting_the_last_selection_answers_retryable(self):
        entry = web_saved_sessions.upsert_saved_session(config=_config(), name="Keep")

        with patch(
            "web.api.set_last_saved_session",
            side_effect=web_saved_session_store.SavedSessionsPersistenceError("disk full"),
        ):
            response = self.client.post(
                "/api/session-config", json={"saved_session_id": entry["id"]}
            )

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.get_json()["retryable"])


class EncryptionKeyCreationTestCase(unittest.TestCase):
    """First-run key creation is exclusive, complete, and never overwritten."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.key_path = Path(self.temp_dir.name) / ".encryption_key"
        patcher = patch.object(web_secrets, "ENCRYPTION_KEY_PATH", str(self.key_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_two_first_run_processes_converge_on_one_key(self):
        starters = 6
        ready = threading.Barrier(starters)
        keys = []
        lock = threading.Lock()

        def start():
            ready.wait(10)
            key = web_secrets._get_encryption_key()
            with lock:
                keys.append(key)

        threads = [threading.Thread(target=start) for _ in range(starters)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)

        self.assertEqual(len(keys), starters)
        self.assertEqual(len(set(keys)), 1, "first-run processes generated different keys")
        self.assertEqual(keys[0], self.key_path.read_bytes().strip())
        # The surviving key must be the one every cipher was built from.
        Fernet(keys[0])

    def test_the_key_name_only_appears_once_the_content_is_complete(self):
        """No importer can observe the zero-byte window `open(path, "wb")` left."""
        paused = threading.Event()
        release = threading.Event()
        real_fsync = os.fsync

        def hooked_fsync(fd):
            real_fsync(fd)
            paused.set()
            release.wait(10)

        results = []
        with patch.object(web_state_files.os, "fsync", hooked_fsync):
            worker = threading.Thread(
                target=lambda: results.append(web_secrets._get_encryption_key())
            )
            worker.start()
            self.assertTrue(paused.wait(10))

            # Mid-write: the real name does not exist yet, and the scratch file
            # already holds a complete, usable key.
            self.assertFalse(self.key_path.exists())
            scratch = [
                entry for entry in Path(self.temp_dir.name).iterdir()
                if entry.name.endswith(".tmp")
            ]
            self.assertEqual(len(scratch), 1)
            Fernet(scratch[0].read_bytes().strip())

            release.set()
            worker.join(10)

        self.assertTrue(self.key_path.exists())
        self.assertEqual(results, [self.key_path.read_bytes().strip()])

    def test_an_existing_key_is_never_replaced(self):
        original = Fernet.generate_key()
        self.key_path.write_bytes(original)

        self.assertEqual(web_secrets._get_encryption_key(), original)
        self.assertEqual(self.key_path.read_bytes(), original)

    def test_an_exclusive_claim_never_clobbers_another_writer(self):
        winner = Fernet.generate_key()
        self.key_path.write_bytes(winner)

        created = web_state_files.create_file_exclusively(
            Fernet.generate_key(), str(self.key_path)
        )

        self.assertFalse(created)
        self.assertEqual(self.key_path.read_bytes(), winner)
        self.assertEqual(
            [entry.name for entry in Path(self.temp_dir.name).iterdir() if entry.name.endswith(".tmp")],
            [],
        )

    def test_an_empty_key_file_fails_loudly_rather_than_building_a_broken_cipher(self):
        self.key_path.write_bytes(b"")

        with patch.object(web_secrets, "_KEY_READ_ATTEMPTS", 2):
            with patch.object(web_secrets, "_KEY_READ_RETRY_SECONDS", 0.01):
                with self.assertRaises(RuntimeError) as raised:
                    web_secrets._get_encryption_key()

        self.assertIn(str(self.key_path), str(raised.exception))


class SavedSessionStoreFileShapeTestCase(SavedSessionStoreTestBase):
    """The public JSON shape and encryption format are unchanged by Stage 1."""

    def test_the_committed_file_keeps_the_documented_shape(self):
        entry = web_saved_sessions.upsert_saved_session(
            config=_config(password=PLAINTEXT_PASSWORD), name="Shape"
        )

        payload = json.loads(self.store_path.read_text(encoding="utf-8"))

        self.assertEqual(set(payload), {"last_session", "sessions"})
        self.assertEqual(payload["last_session"], entry["id"])
        stored_entry = payload["sessions"][0]
        self.assertEqual(
            set(stored_entry), {"id", "name", "created_at", "updated_at", "config"}
        )
        self.assertNotEqual(
            stored_entry["config"]["ssh"]["password"], PLAINTEXT_PASSWORD
        )

    def test_a_legacy_bare_list_file_still_loads(self):
        self.store_path.write_text(
            json.dumps([
                {
                    "id": "legacy-1",
                    "name": "Legacy",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "config": _config(),
                }
            ]),
            encoding="utf-8",
        )

        self.assertEqual(self._stored_ids(), ["legacy-1"])
        self.assertEqual(self._quarantined(), [])


if __name__ == "__main__":
    unittest.main()
