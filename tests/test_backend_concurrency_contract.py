"""ISSUE-2026-041, -042, and -043: three backend concurrency defects.

Companion to `tests/test_git_process_bounds.py`, written the same way: every
test asserts the behaviour the fix must deliver, not the defect of the day.
Each pinned case carried ``@unittest.expectedFailure`` until its fix landed and
lost the decorator in the same change, so nothing here is decorated any more —
an unexpected success fails the run, which is what made a stale decorator loud.
The undecorated cases were controls: behaviour that already worked and that the
rewrite had to preserve. A failure below is now a regression.

Issue → test case:

- ISSUE-2026-041, RuntimeConfig publication  `RuntimeConfigPublicationTestCase`
- ISSUE-2026-042, workspace-label claims     `WorkspaceLabelClaimTestCase`
- ISSUE-2026-043, pooled SSH reservation     `PooledSshReservationTestCase`

All three are Guardrail 2 cases — "do check-then-act and multi-value snapshots
of shared state inside a single lock hold" — approached from three different
directions:

1. `RuntimeConfig.refresh()` assigned ``app_config``, the section dictionaries,
   and every derived attribute one at a time, and readers took no lock at all.
   A multi-field consumer such as ``_public_app_config()`` reads roughly
   fifteen attributes independently, so it could serve half of one generation
   and half of the next. A lock added only around ``refresh()`` would not have
   fixed a reader that does not take it, which is why the fix is one immutable
   generation plus a ``snapshot()`` every multi-field consumer reads once.
2. `workspace_label_conflict()` documented itself as "a check, not a mutex",
   and the create/rename/launch-into-new paths called it and *then* called a
   separately locked manager mutation, so two concurrent requests could both
   pass. That was an accepted local-single-user tradeoff, but it contradicted
   the stronger contract `AGENTS.md` and `CLAUDE.md` state: a non-empty label
   identifies at most one workspace across live and saved state. The claim now
   holds a lock of its own across the verdict and the mutation.
3. `_acquire_ssh_sftp()` read the pool entry under the lock but incremented
   ``in_use`` only after ``open_sftp()`` returned, so the idle reaper could
   close the selected transport mid-open. There was no channel *leak* — release
   always closes the channel and an unpooled client — but the request failed
   for no reason the user did anything to cause. The fix is a two-phase
   reservation, not moving network I/O under the pool lock, which Guardrails 2
   and 3 forbid.

The pool case extends the technique `SshSftpPoolTestCase` in `tests/test_api.py`
already uses for the *counted* window (`test_a_request_in_flight_past_the_idle_
timeout_is_not_reaped`) to the window before the count existed.
"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import api
from web import api as web_api
from web import config as web_config
from web import explorer as web_explorer
from web import runtime_state as web_runtime_state
from web import terminal_io as web_terminal_io
from web import voice as web_voice
from web import workspaces as web_workspaces

#: The one-shot snapshot ISSUE-2026-041 must publish so a multi-field consumer
#: can read a whole generation instead of racing attribute by attribute. Frozen
#: here so the fix cannot satisfy the interleaving test with a lock that only
#: covers the writer. The fix may rename it, but only by changing this constant
#: in the same commit — never by adding a second parallel surface.
RUNTIME_CONFIG_SNAPSHOT_ATTR = "snapshot"

#: How long a barrier may wait for the other thread before giving up. A timeout
#: rather than an indefinite wait: if a stage serializes the operation under one
#: lock, the second thread cannot reach the barrier at all, and the test must
#: still finish and assert the *outcome* rather than deadlock on the mechanism.
BARRIER_TIMEOUT_SECONDS = 2.0


def _config_generation(*, theme, max_sessions, autosave, surface_mode, whisper_model):
    """Build one complete, distinguishable app-config generation."""
    return {
        "appearance": {"theme": theme},
        "terminal": {"max_sessions": max_sessions},
        "workspace": {
            "surface_mode": surface_mode,
            "autosave_interval_minutes": autosave,
        },
        "voice_input": {"whisper_model": whisper_model},
    }


#: Two generations that differ in every field the payload reads, so a mixed
#: read is unambiguous rather than a coincidence of shared defaults.
GENERATION_A = _config_generation(
    theme="light",
    max_sessions=5,
    autosave=7,
    surface_mode="normal",
    whisper_model="small",
)
GENERATION_B = _config_generation(
    theme="dark",
    max_sessions=9,
    autosave=11,
    surface_mode="max",
    whisper_model="medium",
)


class RuntimeConfigPublicationTestCase(unittest.TestCase):
    """ISSUE-2026-041 — a reader sees one whole generation, never a mixture."""

    def setUp(self):
        self.runtime_config = web_config.runtime_config
        # Restore the process-wide singleton from the real files afterwards;
        # every other suite reads this same instance.
        self.addCleanup(self.runtime_config.refresh)
        self._install(GENERATION_A)

    def _install(self, generation):
        with patch.object(web_config, "load_config", return_value=generation):
            self.runtime_config.refresh()

    def _payload_generations(self, payload):
        """Map each field of an app-config payload back to its generation."""
        readings = {
            "theme": payload["appearance"]["theme"],
            "max_sessions": payload["terminal"]["max_sessions"],
            "autosave": payload["workspace"]["autosave_interval_minutes"],
            "surface_mode": payload["workspace"]["surface_mode"],
            "whisper_model": payload["voice_input"]["whisper_model"],
        }
        expected = {
            "A": {
                "theme": "light",
                "max_sessions": 5,
                "autosave": 7,
                "surface_mode": "normal",
                "whisper_model": "small",
            },
            "B": {
                "theme": "dark",
                "max_sessions": 9,
                "autosave": 11,
                "surface_mode": "max",
                "whisper_model": "medium",
            },
        }
        return {
            field: next(
                (name for name, values in expected.items() if values[field] == value),
                f"unknown:{value!r}",
            )
            for field, value in readings.items()
        }

    def test_generation_a_reads_back_whole(self):
        """Control: the mapping helper agrees with a quiet, settled config."""
        self.assertEqual(
            set(self._payload_generations(web_api._public_app_config()).values()),
            {"A"},
        )

    def test_a_reader_during_a_refresh_never_sees_a_mixed_generation(self):
        """The defect, forced rather than raced for.

        `refresh()` is paused immediately after it publishes one derived field,
        with the rest of the instance still on the previous generation, and the
        payload is built from that state. Today it comes back part light/5/7 and
        part dark — a response no config file ever described.

        The pause hooks attribute assignment because that is what publication is
        today. Once publication swaps one reference, the hook may never fire; the
        test then reads a settled config instead, and the same invariant holds.
        """
        paused = threading.Event()
        reader_done = threading.Event()
        original_setattr = web_config.RuntimeConfig.__setattr__

        def pausing_setattr(instance, name, value):
            original_setattr(instance, name, value)
            # `app_theme` is derived and published partway through the sequence:
            # the terminal fields are already on the new generation, the
            # workspace and voice fields are not.
            if name == "app_theme" and not paused.is_set():
                paused.set()
                reader_done.wait(BARRIER_TIMEOUT_SECONDS)

        def refresh_to_b():
            with patch.object(web_config, "load_config", return_value=GENERATION_B):
                self.runtime_config.refresh()

        with patch.object(web_config.RuntimeConfig, "__setattr__", pausing_setattr):
            writer = threading.Thread(target=refresh_to_b, daemon=True)
            writer.start()
            # If publication is atomic there is nothing to pause on, and the
            # refresh simply completes — a settled generation B is a valid read.
            paused.wait(BARRIER_TIMEOUT_SECONDS)
            try:
                generations = self._payload_generations(web_api._public_app_config())
            finally:
                reader_done.set()
                writer.join(BARRIER_TIMEOUT_SECONDS * 2)

        self.assertFalse(writer.is_alive(), "the refresh thread never finished")
        self.assertEqual(
            len(set(generations.values())),
            1,
            f"the payload mixed two config generations: {generations}",
        )

    def test_a_refresh_caught_mid_normalization_has_published_nothing(self):
        """Publication is all-or-nothing, asserted where the work now happens.

        The test above hooks attribute assignment, which is exactly what the
        fix stops doing — so it can only prove the *settled* invariant. This
        one pauses the refresh inside the normalization of the next generation
        and reads a payload from underneath it: an in-flight refresh must still
        be serving the whole previous generation, not a half-built one.
        """
        building = threading.Event()
        may_finish = threading.Event()
        real_build = web_config._build_runtime_state

        def pausing_build(app_config):
            state = real_build(app_config)
            if app_config is GENERATION_B:
                building.set()
                may_finish.wait(BARRIER_TIMEOUT_SECONDS)
            return state

        def refresh_to_b():
            with patch.object(web_config, "load_config", return_value=GENERATION_B):
                self.runtime_config.refresh()

        with patch.object(web_config, "_build_runtime_state", pausing_build):
            writer = threading.Thread(target=refresh_to_b, daemon=True)
            writer.start()
            self.assertTrue(
                building.wait(BARRIER_TIMEOUT_SECONDS),
                "the refresh never reached the normalization step",
            )
            try:
                during = self._payload_generations(web_api._public_app_config())
            finally:
                may_finish.set()
                writer.join(BARRIER_TIMEOUT_SECONDS * 2)

        self.assertFalse(writer.is_alive(), "the refresh thread never finished")
        self.assertEqual(
            set(during.values()),
            {"A"},
            f"a refresh still normalizing had already published: {during}",
        )
        self.assertEqual(
            set(self._payload_generations(web_api._public_app_config()).values()),
            {"B"},
            "the finished refresh never became visible",
        )

    def test_runtime_config_publishes_a_readable_snapshot(self):
        """Multi-field consumers need one captured generation to read from.

        Named in `RUNTIME_CONFIG_SNAPSHOT_ATTR`, because a lock around the
        writer alone leaves every existing reader racing exactly as before.
        """
        snapshot = getattr(self.runtime_config, RUNTIME_CONFIG_SNAPSHOT_ATTR)()
        self.assertEqual(snapshot.app_theme, "light")
        self.assertEqual(snapshot.max_sessions, 5)

        self._install(GENERATION_B)

        # The captured generation is a snapshot, so republishing cannot rewrite
        # a payload that is already half-built.
        self.assertEqual(snapshot.app_theme, "light")
        self.assertEqual(snapshot.max_sessions, 5)
        self.assertEqual(self.runtime_config.app_theme, "dark")


class WorkspaceLabelClaimTestCase(unittest.TestCase):
    """ISSUE-2026-042 — a non-empty label is claimed once, not checked twice."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        self.addCleanup(self._close_created_workspaces)
        self._created = set(
            str(getattr(workspace, "workspace_id", ""))
            for workspace in api.session_manager.get_all_workspaces()
        )

    def _close_created_workspaces(self):
        for workspace in list(api.session_manager.get_all_workspaces()):
            workspace_id = str(getattr(workspace, "workspace_id", ""))
            if workspace_id not in self._created:
                api.session_manager.remove_workspace(workspace_id)

    def _concurrent_claims(self, request_factory, count=2):
        """Run `count` claims so none commits before the others have checked.

        The barrier sits in a wrapper around the namespace check, which is the
        seam the defect lives in: it guarantees every request passes the check
        while the namespace is still free. It waits with a timeout so that a
        stage which serializes check-and-commit under one lock cannot deadlock
        here — the second thread never reaches the barrier, the wait lapses, and
        the assertions below still judge the outcome. That lapse is now the
        *expected* path for a non-empty label: the claim in `web.workspaces`
        holds the namespace across check and commit, so the second thread is
        still waiting for the lock when the first reaches the barrier.
        """
        barrier = threading.Barrier(count, timeout=BARRIER_TIMEOUT_SECONDS)
        real_conflict = web_workspaces.workspace_label_conflict

        def synchronized_conflict(*args, **kwargs):
            result = real_conflict(*args, **kwargs)
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
            return result

        responses = [None] * count

        def claim(index):
            responses[index] = request_factory(index)

        with patch.object(web_workspaces, "workspace_label_conflict", synchronized_conflict):
            threads = [
                threading.Thread(target=claim, args=(index,), daemon=True)
                for index in range(count)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(BARRIER_TIMEOUT_SECONDS * 4)

        self.assertNotIn(None, responses, "a concurrent claim never completed")
        return responses

    def _labels_in_use(self, label):
        normalized = label.strip().casefold()
        return [
            workspace
            for workspace in api.session_manager.get_all_workspaces()
            if str(getattr(workspace, "label", "") or "").strip().casefold() == normalized
        ]

    def test_concurrent_creates_of_one_label_leave_a_single_workspace(self):
        """Two windows, one name, one winner and one actionable 409."""
        label = "Concurrent Claim"

        responses = self._concurrent_claims(
            lambda _index: self.client.post("/api/workspaces", json={"label": label})
        )

        statuses = sorted(response.status_code for response in responses)
        self.assertEqual(statuses, [201, 409])
        self.assertEqual(len(self._labels_in_use(label)), 1)

        loser = next(response for response in responses if response.status_code == 409)
        body = loser.get_json()
        self.assertEqual(body["conflict"], "workspace_label_taken")
        self.assertEqual(body["label"], label)
        self.assertIn("error", body)

    def test_concurrent_create_and_rename_cannot_collide_on_one_label(self):
        """The rename path is the same check-then-act through a second route."""
        label = "Shared Name"
        existing = api.session_manager.create_workspace(
            label="Renamable", retain_when_empty=True
        )

        def request_factory(index):
            if index == 0:
                return self.client.post("/api/workspaces", json={"label": label})
            return self.client.patch(
                f"/api/workspaces/{existing.workspace_id}",
                json={"label": label},
            )

        responses = self._concurrent_claims(request_factory)

        statuses = sorted(response.status_code for response in responses)
        self.assertIn(409, statuses)
        self.assertEqual(len(self._labels_in_use(label)), 1)

    def test_concurrent_renames_onto_one_label_leave_a_single_holder(self):
        """Two renames, one name — the third of the four colliding paths."""
        label = "Renamed Together"
        first = api.session_manager.create_workspace(label="First", retain_when_empty=True)
        second = api.session_manager.create_workspace(label="Second", retain_when_empty=True)
        targets = [first.workspace_id, second.workspace_id]

        responses = self._concurrent_claims(
            lambda index: self.client.patch(
                f"/api/workspaces/{targets[index]}",
                json={"label": label},
            )
        )

        statuses = sorted(response.status_code for response in responses)
        self.assertEqual(statuses, [200, 409])
        self.assertEqual(len(self._labels_in_use(label)), 1)
        self.assertEqual(
            next(
                response for response in responses if response.status_code == 409
            ).get_json()["conflict"],
            "workspace_label_taken",
        )

    def test_concurrent_launches_into_one_new_label_leave_a_single_workspace(self):
        """Launch-into-new claims through the same owner as create and rename.

        Exercised at `resolve_launch_destination`, which is where a launch (and
        a move into a new workspace) takes its name: going through the launch
        route would spawn real shells to prove a naming rule.
        """
        label = "Launched Into"
        outcomes = [None, None]

        def claim(index):
            try:
                outcomes[index] = web_workspaces.resolve_launch_destination(
                    {"new_workspace": True, "workspace_label": label}
                )
            except web_workspaces.WorkspaceRequestError as exc:
                outcomes[index] = exc

        self._concurrent_claims(lambda index: claim(index) or "done")

        refusals = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        self.assertEqual(len(refusals), 1, f"expected exactly one refusal: {outcomes}")
        self.assertEqual(refusals[0].status, 409)
        self.assertEqual(refusals[0].payload["conflict"], "workspace_label_taken")
        self.assertEqual(len(self._labels_in_use(label)), 1)

    def test_a_claim_reads_the_saved_slots_without_the_manager_lock(self):
        """Durable-file I/O never happens under `SessionManager.lock`.

        The saved half of the namespace lives in `runtime_state.json` and its
        cross-process lock, so a claim that read it while holding the manager
        lock would stall every session operation in this process behind another
        process's write — the half of Guardrail 2 the fix must not trade away
        to get the other half.
        """
        manager_lock_free = threading.Event()
        real_list = web_runtime_state.list_restorable_workspaces

        def probe_manager_lock():
            if api.session_manager.lock.acquire(timeout=BARRIER_TIMEOUT_SECONDS):
                try:
                    manager_lock_free.set()
                finally:
                    api.session_manager.lock.release()

        def probing_list(*args, **kwargs):
            # A separate thread, because the manager lock is re-entrant and the
            # claiming thread could re-acquire one it is already holding.
            probe = threading.Thread(target=probe_manager_lock, daemon=True)
            probe.start()
            probe.join(BARRIER_TIMEOUT_SECONDS * 2)
            return real_list(*args, **kwargs)

        with patch.object(
            web_runtime_state, "list_restorable_workspaces", probing_list
        ):
            response = self.client.post("/api/workspaces", json={"label": "Lock Probe"})

        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            manager_lock_free.is_set(),
            "the manager lock was held across the saved-slot read",
        )

    def test_concurrent_empty_label_claims_are_all_allowed(self):
        """Control: an empty label is not a name and stays unconstrained.

        Positional labels already disambiguate unlabelled workspaces, so the
        claim ISSUE-2026-042 introduces must not start refusing them.
        """
        responses = self._concurrent_claims(
            lambda _index: self.client.post("/api/workspaces", json={"label": ""})
        )

        self.assertEqual(
            [response.status_code for response in responses],
            [201, 201],
        )

    def test_a_settled_duplicate_label_is_still_refused(self):
        """Control: the ordinary, uncontended refusal keeps working."""
        label = "Settled Name"
        first = self.client.post("/api/workspaces", json={"label": label})
        self.assertEqual(first.status_code, 201)

        second = self.client.post("/api/workspaces", json={"label": label})
        self.assertEqual(second.status_code, 409)
        self.assertEqual(second.get_json()["conflict"], "workspace_label_taken")


class PooledSshReservationTestCase(unittest.TestCase):
    """ISSUE-2026-043 — a transport chosen for a channel open is reserved."""

    def setUp(self):
        if web_explorer.paramiko is None:
            self.skipTest("paramiko is not installed")
        api._evict_all_pooled_ssh_clients()
        self.addCleanup(api._evict_all_pooled_ssh_clients)

    def _fake_client(self, open_sftp=None):
        client = web_explorer.paramiko.SSHClient()
        transport = MagicMock()
        transport.is_active.return_value = True
        client.get_transport = MagicMock(return_value=transport)
        client.open_sftp = MagicMock(
            side_effect=open_sftp if open_sftp is not None else (lambda: MagicMock())
        )
        client.close = MagicMock()
        return client

    def _pool(self, session_id, client):
        with web_explorer._ssh_client_pool_lock:
            web_explorer._ssh_client_pool[session_id] = web_explorer._PooledSSHClient(client)

    def _age_out(self, session_id):
        with web_explorer._ssh_client_pool_lock:
            entry = web_explorer._ssh_client_pool[session_id]
            entry.last_used -= web_explorer.SSH_CLIENT_POOL_IDLE_TIMEOUT + 1

    def test_a_transport_mid_channel_open_is_not_reaped(self):
        """The window between selecting an entry and counting the holder.

        The count is incremented only after `open_sftp()` returns, so for the
        length of that round trip the entry looks idle to any reaper — and every
        acquire on any session runs one. An entry picked up at 59 s of a 60 s
        idle timeout is closed underneath the request that just chose it.
        """
        session = SimpleNamespace(session_id="reserve-mid-open")
        opening = threading.Event()
        may_finish = threading.Event()

        def blocking_open_sftp():
            opening.set()
            may_finish.wait(BARRIER_TIMEOUT_SECONDS * 2)
            return MagicMock()

        client = self._fake_client(open_sftp=blocking_open_sftp)
        self._pool("reserve-mid-open", client)

        acquired = {}

        def acquire():
            acquired["result"] = api._acquire_ssh_sftp(session)

        worker = threading.Thread(target=acquire, daemon=True)
        worker.start()
        self.assertTrue(
            opening.wait(BARRIER_TIMEOUT_SECONDS),
            "the acquire never reached open_sftp",
        )

        # The entry has now been selected but not yet counted, and its stamp
        # ages past the timeout while the channel is opening.
        self._age_out("reserve-mid-open")
        web_explorer._reap_idle_pooled_ssh_clients()

        may_finish.set()
        worker.join(BARRIER_TIMEOUT_SECONDS * 4)
        self.assertFalse(worker.is_alive(), "the acquire never returned")

        client.close.assert_not_called()
        with web_explorer._ssh_client_pool_lock:
            self.assertIn("reserve-mid-open", web_explorer._ssh_client_pool)

        got_client, got_sftp = acquired["result"]
        self.assertIs(got_client, client)
        api._release_ssh_sftp(session, got_client, got_sftp)

    def test_a_reservation_that_lost_its_entry_mid_open_leaves_it_uncounted(self):
        """A reservation lives on the entry it was taken against, not the id.

        If that entry is evicted while the channel opens and another request
        pools its own transport under the same session, committing by session id
        would charge the replacement for a holder it never had — a count no
        release can give back, which spares that transport from the reaper for
        the life of the process.
        """
        session = SimpleNamespace(session_id="reserve-replaced")
        opening = threading.Event()
        may_finish = threading.Event()
        original_sftp = MagicMock()

        def blocking_open_sftp():
            opening.set()
            may_finish.wait(BARRIER_TIMEOUT_SECONDS * 2)
            return original_sftp

        original = self._fake_client(open_sftp=blocking_open_sftp)
        self._pool("reserve-replaced", original)

        acquired = {}

        def acquire():
            acquired["result"] = api._acquire_ssh_sftp(session)

        worker = threading.Thread(target=acquire, daemon=True)
        worker.start()
        self.assertTrue(
            opening.wait(BARRIER_TIMEOUT_SECONDS),
            "the acquire never reached open_sftp",
        )

        # The session's transport is torn down mid-open and a later request
        # pools a fresh one under the same id.
        api._evict_pooled_ssh_client("reserve-replaced", original)
        replacement = self._fake_client()
        self._pool("reserve-replaced", replacement)

        may_finish.set()
        worker.join(BARRIER_TIMEOUT_SECONDS * 4)
        self.assertFalse(worker.is_alive(), "the acquire never returned")

        got_client, got_sftp = acquired["result"]
        self.assertIs(got_client, original)
        with web_explorer._ssh_client_pool_lock:
            entry = web_explorer._ssh_client_pool["reserve-replaced"]
        self.assertIs(entry.client, replacement)
        self.assertEqual(entry.in_use, 0)

        # The orphaned handle closes on release; the replacement is untouched
        # and still reapable once it goes idle.
        api._release_ssh_sftp(session, got_client, got_sftp)
        got_sftp.close.assert_called_once()
        replacement.close.assert_not_called()
        self._age_out("reserve-replaced")
        web_explorer._reap_idle_pooled_ssh_clients()
        replacement.close.assert_called_once()

    def test_a_failed_open_after_the_entry_was_replaced_spares_the_replacement(self):
        """Rolling a reservation back drops its own entry, not the session's.

        The failure path closes the transport it could not open a channel on. It
        must find that transport by entry identity: a rollback that dropped
        whatever is under the session id would close a replacement another
        request is already using.
        """
        session = SimpleNamespace(session_id="reserve-replaced-failure")
        opening = threading.Event()
        may_finish = threading.Event()

        def failing_open_sftp():
            opening.set()
            may_finish.wait(BARRIER_TIMEOUT_SECONDS * 2)
            raise OSError("no channel")

        original = self._fake_client(open_sftp=failing_open_sftp)
        self._pool("reserve-replaced-failure", original)

        fresh = self._fake_client()
        fresh_sftp = MagicMock()
        acquired = {}

        def acquire():
            acquired["result"] = api._acquire_ssh_sftp(session)

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(fresh, fresh_sftp)):
            worker = threading.Thread(target=acquire, daemon=True)
            worker.start()
            self.assertTrue(
                opening.wait(BARRIER_TIMEOUT_SECONDS),
                "the acquire never reached open_sftp",
            )

            api._evict_pooled_ssh_client("reserve-replaced-failure", original)
            replacement = self._fake_client()
            self._pool("reserve-replaced-failure", replacement)

            may_finish.set()
            worker.join(BARRIER_TIMEOUT_SECONDS * 4)
            self.assertFalse(worker.is_alive(), "the acquire never returned")

        got_client, got_sftp = acquired["result"]
        self.assertIs(got_client, fresh)
        replacement.close.assert_not_called()
        with web_explorer._ssh_client_pool_lock:
            entry = web_explorer._ssh_client_pool["reserve-replaced-failure"]
        self.assertIs(entry.client, replacement)

        # The unpooled loser closes on release, as it always has.
        api._release_ssh_sftp(session, got_client, got_sftp)
        got_sftp.close.assert_called_once()
        fresh.close.assert_called_once()

    def test_a_failed_channel_open_evicts_only_the_failed_client(self):
        """Control: eviction matches by identity, and must keep doing so.

        A rollback that dropped whatever entry happened to be under the session
        id would close a replacement transport another request is holding.
        """
        session = SimpleNamespace(session_id="reserve-open-fails")
        failing = self._fake_client(open_sftp=MagicMock(side_effect=OSError("no channel")))
        self._pool("reserve-open-fails", failing)

        replacement = self._fake_client()
        replacement_sftp = MagicMock()
        with patch.object(
            web_explorer,
            "_open_ssh_sftp",
            return_value=(replacement, replacement_sftp),
        ):
            got_client, got_sftp = api._acquire_ssh_sftp(session)

        failing.close.assert_called_once()
        self.assertIs(got_client, replacement)
        replacement.close.assert_not_called()
        api._release_ssh_sftp(session, got_client, got_sftp)

    def test_release_after_a_reaped_entry_closes_the_orphaned_client(self):
        """Control: whatever the reservation does, release must not leak.

        This is the reason the current race is a spurious failure and not a
        channel leak, and it has to stay true through the reservation fix.
        """
        session = SimpleNamespace(session_id="reserve-orphan")
        client = self._fake_client()
        sftp = MagicMock()

        with patch.object(web_explorer, "_open_ssh_sftp", return_value=(client, sftp)):
            got_client, got_sftp = api._acquire_ssh_sftp(session)

        api._evict_all_pooled_ssh_clients()
        client.close.reset_mock()

        api._release_ssh_sftp(session, got_client, got_sftp)

        got_sftp.close.assert_called_once()
        client.close.assert_called_once()

def _operation_generation(index):
    """One complete settings generation, distinguishable by its index.

    Every field moves with `index`, and the voice engine alternates, so two
    *consecutive* generations can never agree by accident — which is what makes
    "did this operation read a second one?" answerable from the values it used
    rather than from how many reads preceded it.
    """
    return {
        "ssh": {
            "connection_timeout": 100 + index,
            "keepalive_interval": 200 + index,
        },
        # 1, 2, 3 ... clamped at MAX_SESSIONS_MAX; the capacity cases below stay
        # in the first few generations, where the clamp cannot flatten two.
        "terminal": {"max_sessions": 1 + index},
        "voice_input": {
            "enabled": True,
            "engine": "vosk" if index % 2 == 0 else "whisper",
            "vosk_service_url": "ws://localhost:%d" % (2700 + index),
            "vosk_startup_timeout_seconds": 30 + index,
        },
    }


class OperationScopedConfigTestCase(unittest.TestCase):
    """M-3 — an operation reads one settings generation, not several.

    ISSUE-2026-041 gave `RuntimeConfig` an immutable generation and a
    `snapshot()` for whole-payload readers, but several *operations* still read
    the singleton a field at a time: the SSH connect derived its timeout and its
    keepalive separately, the two capacity checks read `max_sessions` once for
    the verdict and again for the sentence quoting it, voice start read
    `enabled` and `engine` apart, the install broadcast named one engine and
    asked about another, and the vosk startup read its timeout three times.
    A concurrent App Settings refresh could therefore combine values no single
    config file ever contained.

    Each case forces that rather than racing for it. `_ticking_reads()` publishes
    a *fresh* generation on every settings access — a direct attribute read
    through `RuntimeConfig.__getattr__` and a whole-generation `snapshot()`
    alike — and records which generation each access was served. An operation
    that captures one snapshot therefore uses one generation throughout; an
    operation that reads N fields uses N.

    Every assertion is written against `_served[0]`, the generation the
    operation read *first*, because that is the contract itself: capture one
    snapshot at the start of the operation and use that object throughout. And
    they assert what the operation *did* with the values — the interval it kept
    the transport alive on, the limit its refusal quotes, the engine it started
    — never how many times it read them. One snapshot is the means; one
    generation is the contract.
    """

    def setUp(self):
        self.runtime_config = web_config.runtime_config
        # Restore the process-wide singleton from the real files afterwards;
        # every other suite reads this same instance.
        self.addCleanup(self.runtime_config.refresh)
        self._served = []

    def _ticking_reads(self):
        """Serve every settings access from its own fresh generation."""
        served = self._served
        original_getattr = web_config.RuntimeConfig.__getattr__
        original_snapshot = web_config.RuntimeConfig.snapshot

        def publish(instance):
            index = len(served)
            served.append(index)
            instance.__dict__["_state"] = web_config._build_runtime_state(
                _operation_generation(index)
            )

        def ticking_getattr(instance, name):
            if not name.startswith("_"):
                publish(instance)
            return original_getattr(instance, name)

        def ticking_snapshot(instance):
            publish(instance)
            return original_snapshot(instance)

        return patch.multiple(
            web_config.RuntimeConfig,
            __getattr__=ticking_getattr,
            snapshot=ticking_snapshot,
        )

    def _first_generation(self):
        """The generation the operation read first — the one it must have kept."""
        self.assertTrue(self._served, "the operation read no settings at all")
        return _operation_generation(self._served[0])

    # -- SSH connect ----------------------------------------------------

    def test_ssh_connect_opens_and_keeps_alive_on_one_generation(self):
        """The timeout the transport opened on and the interval it is kept
        alive on describe the same settings.

        `connect()` can sit for the length of the timeout it was handed, which
        is exactly the window an App Settings refresh lands in; reading the
        keepalive afterwards took it from whatever generation had replaced it.
        """
        opened = {}
        client = MagicMock()
        transport = MagicMock()
        client.get_transport.return_value = transport
        client.connect.side_effect = lambda **kwargs: opened.update(kwargs)
        # Bail after the keepalive rather than streaming: the connector's own
        # except branch handles OSError, so the path stays bounded.
        client.invoke_shell.side_effect = OSError("stop here")

        session = api.session_manager.create_session(
            group_id="cfg-ssh",
            host="127.0.0.1",
            directory="/tmp",
            username="root",
            password="pass",
        )
        self.addCleanup(api.session_manager.reset_sessions)

        fake_paramiko = MagicMock()
        fake_paramiko.SSHClient.return_value = client
        fake_paramiko.SSHException = type("SSHException", (Exception,), {})

        with patch.object(web_terminal_io, "paramiko", fake_paramiko), self._ticking_reads():
            web_terminal_io._connect_ssh_session(session.session_id, session)

        expected = self._first_generation()["ssh"]
        self.assertEqual(opened["timeout"], expected["connection_timeout"])
        transport.set_keepalive.assert_called_once_with(expected["keepalive_interval"])

    # -- Capacity -------------------------------------------------------

    def test_a_split_refusal_quotes_the_limit_it_refused_against(self):
        """The verdict and the sentence explaining it name one cap.

        Reading the cap twice does not merely misquote it: the refusal ends up
        reporting a limit at or above the pane count it has just refused to
        allow, which is advice the user cannot act on.
        """
        group = api.session_manager.create_group(
            name="Cap", connection_mode="wsl", layout="single", terminal_count=1
        )
        session = api.session_manager.create_session(
            group_id=group.group_id, host="cmd", directory="/tmp", mode="wsl"
        )
        self.addCleanup(api.session_manager.reset_sessions)
        api.app.config["TESTING"] = True
        client = api.app.test_client()

        with self._ticking_reads():
            response = client.post(f"/api/sessions/{session.session_id}/split", json={})

        self.assertEqual(response.status_code, 400)
        limit = self._first_generation()["terminal"]["max_sessions"]
        self.assertEqual(
            response.get_json()["error"], web_workspaces.capacity_refusal(2, limit)
        )
        self.assertLess(limit, 2, "the refusal must quote a limit below what it refused")

    def test_a_launch_refusal_logs_and_quotes_the_same_limit(self):
        """Verdict, log line and refusal text all come from one generation."""
        api.app.config["TESTING"] = True
        client = api.app.test_client()
        self.addCleanup(api.session_manager.reset_sessions)

        with self._ticking_reads():
            with self.assertLogs(web_workspaces.logger, level="WARNING") as logged:
                response = client.post(
                    "/api/sessions",
                    json={
                        "connection_mode": "wsl",
                        "sessions": [{"directory": "/tmp"}, {"directory": "/tmp"}],
                    },
                )

        self.assertEqual(response.status_code, 400)
        limit = self._first_generation()["terminal"]["max_sessions"]
        self.assertEqual(
            response.get_json()["error"], web_workspaces.capacity_refusal(2, limit)
        )
        self.assertIn(
            "Too many sessions requested: 2 > %d" % limit,
            "\n".join(logged.output),
        )

    # -- Voice ----------------------------------------------------------

    def test_voice_start_runs_the_engine_the_generation_that_allowed_it_named(self):
        """`enabled` and `engine` are one decision, not two reads."""
        started = []
        with api.app.test_request_context("/"):
            api.request.sid = "cfg-client"  # type: ignore[attr-defined]
            with patch.object(api, "emit"), patch.object(
                api, "_start_vosk_voice_session", lambda _id: started.append("vosk")
            ), patch.object(
                api, "_start_whisper_voice_session", lambda _id: started.append("whisper")
            ), self._ticking_reads():
                api.handle_voice_start({"session_id": "cfg-voice"})

        self.addCleanup(api.release_voice_session, "cfg-voice", "vosk")
        self.assertEqual(started, [self._first_generation()["voice_input"]["engine"]])

    def test_the_install_broadcast_answers_about_the_engine_it_names(self):
        """The engine in the payload is the engine availability was asked of."""
        asked = []

        def _record(engine=None, *_args, **_kwargs):
            asked.append(engine)
            return True

        emitted = {}

        def _emit(event, payload, **_kwargs):
            emitted[event] = payload

        with patch.object(api.socketio, "emit", _emit), patch.object(
            api, "_voice_engine_available", _record
        ), self._ticking_reads():
            api._broadcast_voice_install_finished({"status": "success"})

        payload = emitted["voice_availability_updated"]
        self.assertEqual(
            payload["engine"], self._first_generation()["voice_input"]["engine"]
        )
        self.assertEqual(asked, [payload["engine"]])

    def test_the_vosk_startup_waits_and_reports_on_one_generation(self):
        """One endpoint and one budget across a startup that read both twice."""
        probed = []
        waited = []

        def _reachable(timeout=2.0, service_url=None):
            probed.append(service_url)
            return False

        def _ready(process, timeout=30, service_url=None):
            waited.append((timeout, service_url))
            return False

        process = MagicMock()
        process.poll.return_value = None
        process.pid = 4242

        with patch.object(web_voice, "_vosk_service_reachable", _reachable), patch.object(
            web_voice, "_wait_for_vosk_ready", _ready
        ), patch.object(web_voice.subprocess, "Popen", return_value=process), patch.object(
            web_voice.os.path, "exists", return_value=True
        ), self._ticking_reads():
            with self.assertLogs(web_voice.logger, level="ERROR") as logged:
                started = web_voice._ensure_vosk_service()

        self.addCleanup(setattr, web_voice, "_vosk_process", None)
        self.assertFalse(started)
        expected = self._first_generation()["voice_input"]
        self.assertEqual(probed, [expected["vosk_service_url"]])
        self.assertEqual(
            waited,
            [(expected["vosk_startup_timeout_seconds"], expected["vosk_service_url"])],
        )
        self.assertIn(
            "not ready after %ss" % expected["vosk_startup_timeout_seconds"],
            "\n".join(logged.output),
        )

if __name__ == "__main__":
    unittest.main()
