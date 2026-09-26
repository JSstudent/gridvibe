"""The native-mode intent store, and the four routes over it.

The store exists for one reason: two open GridVibe pages polling the same
pending intent would both open the workspace, and the user would get two
windows. So the case that matters most here is the second claimant losing.

Everything else follows from the store being in-memory and TTL-bounded: an
intent nobody claimed is not worth remembering, and an unknown id reads
`expired` rather than erroring — the sidecar polling it needs an answer, not an
exception.
"""

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import api  # noqa: E402
from web.window_intents import (  # noqa: E402
    BLOCKED,
    CLAIMED,
    EXPIRED,
    OPENED,
    PENDING,
    REFUSED,
    SPLIT,
    SPLIT_KIND,
    WINDOW_KIND,
    WindowIntentStore,
    window_intents,
)


class WindowIntentStoreTestCase(unittest.TestCase):
    def setUp(self):
        self.store = WindowIntentStore(ttl_seconds=15.0, claim_ttl_seconds=20.0)

    def test_a_recorded_intent_is_pending_and_says_how_long_it_has(self):
        intent = self.store.open("ws-1", "g-1", now=100.0)

        self.assertEqual(intent["workspace_id"], "ws-1")
        self.assertEqual(intent["group_id"], "g-1")
        self.assertEqual(intent["state"], PENDING)
        self.assertEqual(intent["expires_in"], 15.0)
        self.assertTrue(intent["intent_id"])

    def test_exactly_one_of_two_claimants_wins(self):
        intent = self.store.open("ws-1", now=100.0)

        first_ok, first = self.store.claim(intent["intent_id"], "window-a", now=100.1)
        second_ok, second = self.store.claim(intent["intent_id"], "window-b", now=100.2)

        self.assertTrue(first_ok)
        self.assertEqual(first["state"], CLAIMED)
        self.assertFalse(second_ok)
        # The loser is told why, rather than being handed a pending record it
        # would act on.
        self.assertEqual(second["state"], CLAIMED)
        self.assertIn("already took", second["error"])

    def test_a_claimed_intent_leaves_the_pending_list(self):
        intent = self.store.open("ws-1", now=100.0)
        self.assertEqual(len(self.store.pending(now=100.1)), 1)

        self.store.claim(intent["intent_id"], "window-a", now=100.2)

        self.assertEqual(self.store.pending(now=100.3), [])

    def test_each_outcome_is_recorded_and_readable(self):
        for outcome in (OPENED, BLOCKED):
            with self.subTest(outcome=outcome):
                intent = self.store.open("ws-1", now=100.0)
                self.store.claim(intent["intent_id"], "window-a", now=100.1)

                recorded, _payload = self.store.record_result(
                    intent["intent_id"], outcome, "detail here", now=100.2
                )

                self.assertTrue(recorded)
                read = self.store.read(intent["intent_id"], now=100.3)
                self.assertEqual(read["state"], outcome)
                self.assertEqual(read["detail"], "detail here")

    def test_an_outcome_the_store_does_not_know_is_refused(self):
        intent = self.store.open("ws-1", now=100.0)

        recorded, payload = self.store.record_result(
            intent["intent_id"], "probably", now=100.1
        )

        self.assertFalse(recorded)
        self.assertIn("opened", payload["error"])

    def test_an_unclaimed_intent_expires_rather_than_waiting_forever(self):
        intent = self.store.open("ws-1", now=100.0)

        # One tick past the TTL: no page was there to claim it.
        read = self.store.read(intent["intent_id"], now=115.1)

        self.assertEqual(read["state"], EXPIRED)
        self.assertEqual(self.store.pending(now=115.1), [])

    def test_a_claim_extends_the_deadline_so_a_slow_open_is_not_lost(self):
        intent = self.store.open("ws-1", now=100.0)
        self.store.claim(intent["intent_id"], "window-a", now=110.0)

        # Past the original 15s TTL, but inside the claim's own 20s.
        self.assertEqual(self.store.read(intent["intent_id"], now=120.0)["state"], CLAIMED)

    def test_an_unknown_id_reads_expired_rather_than_raising(self):
        read = self.store.read("no-such-intent", now=100.0)

        self.assertEqual(read["state"], EXPIRED)
        self.assertEqual(read["intent_id"], "no-such-intent")

    def test_the_store_is_bounded(self):
        store = WindowIntentStore(ttl_seconds=1000.0, max_intents=4)

        for index in range(10):
            store.open(f"ws-{index}", now=100.0 + index)

        # A sidecar in a retry loop cannot grow the store without bound.
        self.assertLessEqual(len(store.pending(now=110.0)), 4)


class WaitForSettledTestCase(unittest.TestCase):
    """The in-process read, which exists so one caller need not poll.

    ``web/mcp_http.py`` runs the sidecar's own wait loop *inside* a Flask
    request handler, where every tick used to be a loopback GET back into this
    same server. The store is in that process, so the loop can wait on it --
    and what it gets back has to be what the route would have said, or the two
    transports would answer differently.
    """

    def test_an_intent_that_has_already_settled_answers_at_once(self):
        store = WindowIntentStore()
        intent = store.open("ws1")
        store.claim(intent["intent_id"], "page-1")
        store.record_result(intent["intent_id"], OPENED)

        started = time.monotonic()
        record = store.wait_for_settled(intent["intent_id"], 30.0)

        self.assertEqual(record["state"], OPENED)
        self.assertLess(time.monotonic() - started, 5.0)

    def test_a_settling_write_wakes_the_waiter(self):
        store = WindowIntentStore()
        intent = store.open("ws1")

        def page():
            time.sleep(0.2)
            store.claim(intent["intent_id"], "page-1")
            store.record_result(intent["intent_id"], BLOCKED, "A window refused.")

        thread = threading.Thread(target=page, daemon=True)
        started = time.monotonic()
        thread.start()
        record = store.wait_for_settled(intent["intent_id"], 30.0)
        thread.join(5)

        self.assertEqual(record["state"], BLOCKED)
        self.assertEqual(record["detail"], "A window refused.")
        self.assertLess(time.monotonic() - started, 10.0)

    def test_an_unclaimed_intent_ends_at_its_own_expiry_not_the_timeout(self):
        """Nothing further can happen to a pruned intent, so waiting out the
        rest of the caller's deadline would hold a thread for no answer."""
        store = WindowIntentStore(ttl_seconds=0.2, claim_ttl_seconds=20.0)
        intent = store.open("ws1")

        started = time.monotonic()
        record = store.wait_for_settled(intent["intent_id"], 30.0)

        self.assertEqual(record["state"], EXPIRED)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_a_claim_extends_what_the_waiter_sleeps_against(self):
        """The claim TTL is the claimant's, and the waiter honours it: a page
        that claims just before the claim window shuts still gets its own."""
        store = WindowIntentStore(ttl_seconds=0.3, claim_ttl_seconds=5.0)
        intent = store.open("ws1")

        def page():
            time.sleep(0.1)
            store.claim(intent["intent_id"], "page-1")
            time.sleep(0.4)  # past the 0.3s a page had to claim it
            store.record_result(intent["intent_id"], OPENED)

        thread = threading.Thread(target=page, daemon=True)
        thread.start()
        record = store.wait_for_settled(intent["intent_id"], 30.0)
        thread.join(5)

        self.assertEqual(record["state"], OPENED)

    def test_the_wait_is_bounded_by_the_timeout_it_was_given(self):
        store = WindowIntentStore(ttl_seconds=30.0, claim_ttl_seconds=30.0)
        intent = store.open("ws1")

        started = time.monotonic()
        record = store.wait_for_settled(intent["intent_id"], 0.2)

        self.assertEqual(record["state"], PENDING)
        self.assertLess(time.monotonic() - started, 10.0)

    def test_an_id_the_store_never_had_is_expired_rather_than_a_wait(self):
        store = WindowIntentStore()

        started = time.monotonic()
        record = store.wait_for_settled("not-an-intent", 30.0)

        self.assertEqual(record["state"], EXPIRED)
        self.assertLess(time.monotonic() - started, 5.0)


class WindowIntentRouteTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        window_intents.reset()
        self.addCleanup(window_intents.reset)

    def test_health_publishes_the_window_mode(self):
        payload = self.client.get("/api/health").get_json()

        # The one thing a process outside the browser cannot work out itself.
        self.assertIn(payload["window_mode"], ("browser", "native"))

    def test_an_intent_round_trips_through_the_routes(self):
        # No group: a named group is checked against the live registry, which
        # `tests/test_navigation.py` pins.
        created = self.client.post("/api/windows/open", json={"workspace_id": "ws-1"})
        self.assertEqual(created.status_code, 201)
        intent_id = created.get_json()["intent_id"]

        listed = self.client.get("/api/windows/intents").get_json()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["intents"][0]["intent_id"], intent_id)

        claimed = self.client.post(f"/api/windows/intents/{intent_id}/claim", json={})
        self.assertEqual(claimed.status_code, 200)

        reported = self.client.post(
            f"/api/windows/intents/{intent_id}/result", json={"outcome": "opened"}
        )
        self.assertEqual(reported.status_code, 200)

        read = self.client.get(f"/api/windows/intents/{intent_id}").get_json()
        self.assertEqual(read["state"], "opened")

    def test_a_second_claim_is_a_conflict_not_a_second_window(self):
        intent_id = self.client.post(
            "/api/windows/open", json={"workspace_id": "ws-1"}
        ).get_json()["intent_id"]

        first = self.client.post(f"/api/windows/intents/{intent_id}/claim", json={})
        second = self.client.post(f"/api/windows/intents/{intent_id}/claim", json={})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)

    def test_an_intent_with_no_workspace_is_refused(self):
        response = self.client.post("/api/windows/open", json={})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(window_intents.pending(), [])

    def test_the_pending_list_is_almost_always_empty(self):
        payload = self.client.get("/api/windows/intents").get_json()

        self.assertEqual(payload, {"intents": [], "count": 0})

    def test_a_result_the_store_does_not_know_is_refused(self):
        intent_id = self.client.post(
            "/api/windows/open", json={"workspace_id": "ws-1"}
        ).get_json()["intent_id"]

        response = self.client.post(
            f"/api/windows/intents/{intent_id}/result", json={"outcome": "maybe"}
        )

        self.assertEqual(response.status_code, 400)

    def test_nothing_about_an_intent_is_durable(self):
        self.client.post("/api/windows/open", json={"workspace_id": "ws-1"})

        # The store is memory only: no file, no lock, nothing to quarantine.
        self.assertFalse(hasattr(window_intents, "path"))
        window_intents.reset()
        self.assertEqual(self.client.get("/api/windows/intents").get_json()["count"], 0)


class SplitIntentStoreTestCase(unittest.TestCase):
    """The second kind, in the same store, under the same claim-once rule.

    A split is an intent for the same reason a window is: the axis never
    reaches the server. The page computes the rectangles and owns every
    refusal, measured off the live terminal -- so a process that cannot measure
    a pane leaves a request and a page that can performs it.
    """

    def setUp(self):
        self.store = WindowIntentStore(ttl_seconds=15.0, claim_ttl_seconds=20.0)

    def test_a_split_intent_carries_the_pane_the_axis_and_the_request(self):
        intent = self.store.open_split(
            "pane-1",
            "horizontal",
            group_id="g-1",
            workspace_id="ws-1",
            split_request={"kind": "agent", "agent": "claude"},
            now=100.0,
        )

        self.assertEqual(intent["kind"], SPLIT_KIND)
        self.assertEqual(intent["session_id"], "pane-1")
        self.assertEqual(intent["axis"], "horizontal")
        self.assertEqual(intent["split_request"]["agent"], "claude")
        self.assertEqual(intent["state"], PENDING)

    def test_a_window_intent_still_says_it_is_one(self):
        """An older reader that ignores the kind gets the kind it expects."""
        self.assertEqual(self.store.open("ws-1", now=100.0)["kind"], WINDOW_KIND)

    def test_a_split_reports_split_or_refused_and_nothing_else(self):
        for outcome in (SPLIT, REFUSED):
            with self.subTest(outcome=outcome):
                intent = self.store.open_split("pane-1", "vertical", now=100.0)
                self.store.claim(intent["intent_id"], "window-a", now=100.1)

                recorded, _payload = self.store.record_result(
                    intent["intent_id"], outcome, "because", now=100.2
                )

                self.assertTrue(recorded)
                self.assertEqual(
                    self.store.read(intent["intent_id"], now=100.3)["state"], outcome
                )

    def test_a_page_cannot_report_a_windows_verb_on_a_split(self):
        """Reporting `opened` on a pane would be reporting someone else's work."""
        intent = self.store.open_split("pane-1", "vertical", now=100.0)

        recorded, payload = self.store.record_result(
            intent["intent_id"], OPENED, now=100.1
        )

        self.assertFalse(recorded)
        self.assertIn("split", payload["error"])
        self.assertEqual(self.store.read(intent["intent_id"], now=100.2)["state"], PENDING)

    def test_a_page_cannot_report_a_splits_verb_on_a_window(self):
        intent = self.store.open("ws-1", now=100.0)

        recorded, payload = self.store.record_result(
            intent["intent_id"], SPLIT, now=100.1
        )

        self.assertFalse(recorded)
        self.assertIn("opened", payload["error"])

    def test_a_settled_split_carries_the_pane_it_made_through_a_field_list(self):
        intent = self.store.open_split("pane-1", "vertical", now=100.0)
        self.store.claim(intent["intent_id"], "window-a", now=100.1)

        self.store.record_result(
            intent["intent_id"],
            SPLIT,
            "",
            {
                "session_id": "pane-2",
                "title": "Terminal 2",
                "index": 1,
                "password": "hunter2",
            },
            now=100.2,
        )

        read = self.store.read(intent["intent_id"], now=100.3)
        self.assertEqual(read["result"]["session_id"], "pane-2")
        self.assertEqual(read["result"]["index"], 1)
        # A field list, so a page cannot widen what a settled intent publishes.
        self.assertNotIn("password", read["result"])

    def test_exactly_one_of_two_pages_takes_a_split(self):
        """Two open windows produce one new pane, as they produce one window."""
        intent = self.store.open_split("pane-1", "vertical", now=100.0)

        first_ok, _first = self.store.claim(intent["intent_id"], "window-a", now=100.1)
        second_ok, second = self.store.claim(intent["intent_id"], "window-b", now=100.2)

        self.assertTrue(first_ok)
        self.assertFalse(second_ok)
        self.assertIn("already took", second["error"])

    def test_an_unclaimed_split_expires_like_any_other_intent(self):
        intent = self.store.open_split("pane-1", "vertical", now=100.0)

        self.assertEqual(
            self.store.read(intent["intent_id"], now=115.1)["state"], EXPIRED
        )

    def test_both_kinds_share_one_pending_list_and_one_ceiling(self):
        store = WindowIntentStore(ttl_seconds=1000.0, max_intents=4)
        for index in range(5):
            store.open(f"ws-{index}", now=100.0 + index)
            store.open_split(f"pane-{index}", "vertical", now=100.0 + index)

        pending = store.pending(now=200.0)
        self.assertLessEqual(len(pending), 4)
        self.assertTrue(all("kind" in intent for intent in pending))


class SplitIntentRouteTestCase(unittest.TestCase):
    """The route that records one, and everything it refuses before it does."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        window_intents.reset()
        self.addCleanup(window_intents.reset)

    def _pane(self, **overrides):
        group = api.session_manager.create_group(
            name="Local", connection_mode="wsl", layout="single", terminal_count=1
        )
        fields = {
            "group_id": group.group_id,
            "host": "cmd",
            "directory": "C:/repo",
            "mode": "wsl",
            "startup_mode": "terminal",
        }
        fields.update(overrides)
        session = api.session_manager.create_session(**fields)
        return group, api.session_manager.get_session(session.session_id)

    def test_an_intent_names_the_pane_the_axis_and_the_group(self):
        group, pane = self._pane()

        response = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent",
            json={"axis": "horizontal"},
        )

        self.assertEqual(response.status_code, 201)
        payload = response.get_json()
        self.assertEqual(payload["kind"], "split")
        self.assertEqual(payload["session_id"], pane.session_id)
        self.assertEqual(payload["axis"], "horizontal")
        self.assertEqual(payload["group_id"], group.group_id)

    def test_an_axis_that_is_not_one_of_the_two_is_refused_before_recording(self):
        _group, pane = self._pane()

        response = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent", json={"axis": "diagonal"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(window_intents.pending(), [])

    def test_an_unknown_agent_is_refused_now_rather_than_after_the_wait(self):
        """A refusal here costs nothing; one after a claim costs the whole TTL."""
        _group, pane = self._pane()

        response = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent",
            json={"axis": "vertical", "kind": "agent", "agent": "not-an-agent"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(window_intents.pending(), [])

    def test_an_agent_pane_with_no_agent_is_refused(self):
        _group, pane = self._pane()

        response = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent",
            json={"axis": "vertical", "kind": "agent"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("agent", response.get_json()["error"])

    def test_a_browser_pane_is_refused_on_a_remote_source(self):
        """GridVibe draws a browser pane, so it belongs to a local group."""
        _group, pane = self._pane(mode="ssh", host="example.com", username="ubuntu")

        response = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent",
            json={"axis": "vertical", "kind": "browser", "url": "http://localhost:3000"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("this machine", response.get_json()["error"])

    def test_a_pane_that_is_not_open_is_a_404(self):
        response = self.client.post(
            "/api/sessions/no-such-pane/split-intent", json={"axis": "vertical"}
        )

        self.assertEqual(response.status_code, 404)

    def test_the_creator_stamp_is_read_from_the_registry_not_believed(self):
        """A caller cannot claim a pane that is not open as its own lineage."""
        _group, pane = self._pane()
        _group2, caller = self._pane()

        claimed = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent",
            json={"axis": "vertical", "origin_session_id": caller.session_id},
        ).get_json()

        self.assertEqual(
            claimed["split_request"]["created_by_session_id"], caller.session_id
        )

    def test_an_origin_that_is_not_open_is_refused_rather_than_unstamped(self):
        """Stating a pane that is gone is not the same as stating none.

        An omitted origin is a person's own split and starts its own recursion
        budget. A stated one that names nothing open used to be written down as
        exactly that -- so the split was performed, the new pane was stamped
        with nobody, and an agent about to be handed it got a fresh
        `agent_depth` of 0. The lineage gate answers it now, and nothing is
        recorded for a page to claim.
        """
        _group, pane = self._pane()

        response = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent",
            json={"axis": "vertical", "origin_session_id": "ghost-pane"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertIn("[lineage gate]", response.get_json()["error"])
        self.assertEqual(window_intents.pending(), [])

    def test_a_creator_that_closed_before_the_page_split_is_refused(self):
        """The race the whole record-then-perform shape leaves open.

        The stamp is validated when the intent is recorded, and the split
        happens whenever a page next claims it -- so the pane that asked can
        have gone by then. The body the page posts back still names it, and a
        pane stamped with nobody would start its own depth budget over.
        """
        group, pane = self._pane()
        _caller_group, caller = self._pane()

        recorded = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent",
            json={"axis": "vertical", "origin_session_id": caller.session_id},
        ).get_json()["split_request"]
        self.assertEqual(recorded["created_by_session_id"], caller.session_id)

        # The agent's own pane closes while the intent sits waiting for a page.
        self.assertEqual(
            self.client.delete(f"/api/sessions/{caller.session_id}").status_code, 200
        )

        performed = self.client.post(
            f"/api/sessions/{pane.session_id}/split", json=recorded
        )

        self.assertEqual(performed.status_code, 403)
        self.assertIn("lineage gate", performed.get_json()["error"])
        self.assertEqual(len(api.session_manager.get_group_sessions(group.group_id)), 1)

    def test_the_recorded_request_is_what_the_page_posts_back(self):
        """Built here, so the page forwards a validated body rather than one
        it composed."""
        _group, pane = self._pane()
        stated = tempfile.mkdtemp()
        self.addCleanup(os.rmdir, stated)

        payload = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent",
            json={
                "axis": "vertical",
                "kind": "terminal",
                "title": "Scratch",
                "directory": stated,
            },
        ).get_json()

        request_body = payload["split_request"]
        self.assertEqual(request_body["axis"], "vertical")
        self.assertEqual(request_body["kind"], "terminal")
        self.assertEqual(request_body["title"], "Scratch")
        # Under its own key: the page adds `directory` itself for an explorer
        # source, and a caller's stated path must not be read as that.
        self.assertEqual(request_body["stated_directory"], stated)
        self.assertNotIn("directory", request_body)

    def test_a_split_intent_appears_in_the_same_pending_list_as_a_window(self):
        _group, pane = self._pane()
        self.client.post("/api/windows/open", json={"workspace_id": "ws-1"})
        self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent", json={"axis": "vertical"}
        )

        listed = self.client.get("/api/windows/intents").get_json()

        self.assertEqual(listed["count"], 2)
        self.assertEqual(
            sorted(intent["kind"] for intent in listed["intents"]),
            ["split", "window"],
        )

    def test_a_settled_split_reports_its_pane_through_the_result_route(self):
        _group, pane = self._pane()
        intent_id = self.client.post(
            f"/api/sessions/{pane.session_id}/split-intent", json={"axis": "vertical"}
        ).get_json()["intent_id"]
        self.client.post(f"/api/windows/intents/{intent_id}/claim", json={})

        reported = self.client.post(
            f"/api/windows/intents/{intent_id}/result",
            json={
                "outcome": "split",
                "detail": "",
                "result": {"session_id": "pane-9", "index": 1},
            },
        )

        self.assertEqual(reported.status_code, 200)
        read = self.client.get(f"/api/windows/intents/{intent_id}").get_json()
        self.assertEqual(read["state"], "split")
        self.assertEqual(read["result"]["session_id"], "pane-9")


if __name__ == "__main__":
    unittest.main()
