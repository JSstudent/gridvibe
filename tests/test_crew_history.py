"""Restored crew links: the history store and its two translations.

Pinned here against the module itself, with no Flask and no files:

- **Capture and restore round-trip.** Links become entries keyed by snapshot
  coordinates and come back keyed by the new session ids.
- **History, never work in flight.** A ``working`` link is stored as
  ``ended`` with the ``restarted`` key, on capture and again on restore.
- **Only what the board shows.** Same-workspace agent endpoints only, newest
  per pair, a live link over a restored one, capped at ``MAX_ASSIGNMENTS``.
- **Nothing private.** Output keys are exactly ``LINK_FIELDS`` (plus
  ``restored``): never the report text, the receipt or the handoff id.
- **Held apart from ``ResultStore``.** Installing history leaves the live store
  empty, and a closed pane takes its history with it.
"""

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import crew_history  # noqa: E402
from web.agent_results import (  # noqa: E402
    _ENDED_REASONS,
    LINK_FIELDS,
    MAX_ASSIGNMENTS,
    ResultStore,
)
from web.agent_results import results as live_results  # noqa: E402
from web.crew_history import (  # noqa: E402
    RESTARTED,
    CrewHistory,
    translate_for_capture,
    translate_for_restore,
)

STORED_KEYS = {
    "requester",
    "worker",
    "state",
    "read",
    "status",
    "collected",
    "handed_at",
    "reported_at",
    "reason",
    "round",
    "label",
}


def make_link(requester="a", worker="b", **overrides):
    link = {
        "link_id": "feedfacefeedface",
        "requester_session_id": requester,
        "worker_session_id": worker,
        "state": "reported",
        "read": True,
        "status": "done",
        "collected": False,
        "handed_at": "2026-10-05T10:00:00+00:00",
        "reported_at": "2026-10-05T10:05:00+00:00",
        "reason": "",
        "round": 2,
        "label": "Check the build",
    }
    link.update(overrides)
    return link


COORDS = {"a": ("g1", 0), "b": ("g1", 1), "c": ("g1", 2), "x": ("g2", 0)}
AGENTS = {"a", "b", "c", "x"}


class TranslationTests(unittest.TestCase):
    def test_capture_writes_coordinates_and_the_public_fields_only(self):
        entries = translate_for_capture([make_link()], COORDS, AGENTS)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(set(entry), STORED_KEYS)
        self.assertEqual(entry["requester"], {"group": "g1", "pane": 0})
        self.assertEqual(entry["worker"], {"group": "g1", "pane": 1})
        self.assertEqual(entry["state"], "reported")
        self.assertEqual(entry["round"], 2)
        self.assertEqual(entry["label"], "Check the build")
        for forbidden in ("link_id", "requester_session_id", "worker_session_id", "restored"):
            self.assertNotIn(forbidden, entry)

    def test_round_trip_through_both_translations(self):
        original = make_link(state="ended", status="", reason="pane closed", round=3)
        entries = translate_for_capture([original], COORDS, AGENTS)
        # Through the file: nothing but JSON types may be needed.
        entries = json.loads(json.dumps(entries))
        restored = translate_for_restore(
            entries, {("g1", 0): "new-a", ("g1", 1): "new-b", ("g1", 2): "new-c"}
        )
        self.assertEqual(len(restored), 1)
        link = restored[0]
        self.assertEqual(link["requester_session_id"], "new-a")
        self.assertEqual(link["worker_session_id"], "new-b")
        for name in LINK_FIELDS:
            if name in ("link_id", "requester_session_id", "worker_session_id"):
                continue
            self.assertEqual(link[name], original[name], name)

    def test_working_is_demoted_to_ended_restarted_on_capture(self):
        working = make_link(state="working", status="", reported_at="", reason="", read=True)
        entry = translate_for_capture([working], COORDS, AGENTS)[0]
        self.assertEqual(entry["state"], "ended")
        self.assertEqual(entry["reason"], RESTARTED)
        self.assertIn(RESTARTED, _ENDED_REASONS)

    def test_working_is_demoted_again_on_restore(self):
        entry = translate_for_capture([make_link()], COORDS, AGENTS)[0]
        entry.update(state="working", status="", reason="")
        link = translate_for_restore([entry], {("g1", 0): "p", ("g1", 1): "q"})[0]
        self.assertEqual(link["state"], "ended")
        self.assertEqual(link["reason"], RESTARTED)

    def test_reported_and_ended_states_keep_their_own_reason(self):
        reported = make_link("a", "b", state="reported", reason="")
        ended = make_link("a", "c", state="ended", status="", reason="agent exited")
        entries = translate_for_capture([reported, ended], COORDS, AGENTS)
        self.assertEqual([(e["state"], e["reason"]) for e in entries],
                         [("reported", ""), ("ended", "agent exited")])

    def test_capture_drops_cross_workspace_and_non_agent_endpoints(self):
        links = [
            make_link("a", "b"),
            make_link("a", "x"),  # x is not in this workspace's coordinates
            make_link("a", "c"),  # c is not an agent pane
            make_link("a", "gone"),
            make_link("a", "a"),
        ]
        coords = {"a": ("g1", 0), "b": ("g1", 1), "c": ("g1", 2)}
        entries = translate_for_capture(links, coords, {"a", "b"})
        self.assertEqual([e["worker"]["pane"] for e in entries], [1])

    def test_capture_keeps_the_newest_link_per_pair(self):
        older = make_link(round=1, label="first")
        newer = make_link(round=2, label="second")
        other = make_link("a", "c", round=1)
        entries = translate_for_capture([older, other, newer], COORDS, AGENTS)
        self.assertEqual(
            [(e["worker"]["pane"], e["label"]) for e in entries],
            [(2, "Check the build"), (1, "second")],
        )

    def test_capture_prefers_a_live_link_over_a_restored_one(self):
        live = make_link(round=3, label="live")
        restored = dict(make_link(round=2, label="restored"), restored=True)
        for ordering in ([restored, live], [live, restored]):
            entries = translate_for_capture(ordering, COORDS, AGENTS)
            self.assertEqual([e["label"] for e in entries], ["live"])

    def test_capture_caps_at_max_assignments_keeping_the_newest(self):
        total = MAX_ASSIGNMENTS + 5
        coords = {"r": ("g", 0)}
        coords.update({f"w{i}": ("g", i + 1) for i in range(total)})
        links = [make_link("r", f"w{i}", round=1, label=str(i)) for i in range(total)]
        entries = translate_for_capture(links, coords, set(coords))
        self.assertEqual(len(entries), MAX_ASSIGNMENTS)
        self.assertEqual(entries[0]["label"], "5")
        self.assertEqual(entries[-1]["label"], str(total - 1))

    def test_restore_drops_what_does_not_resolve_or_validate(self):
        good = translate_for_capture([make_link()], COORDS, AGENTS)[0]
        mapping = {("g1", 0): "p", ("g1", 1): "q"}

        def broken(**changes):
            entry = json.loads(json.dumps(good))
            entry.update(changes)
            return entry

        bad = [
            "not an entry",
            None,
            broken(requester={"group": "gone", "pane": 0}),
            broken(worker={"group": "g1", "pane": 9}),
            broken(requester={"group": "g1", "pane": True}),
            broken(worker={"group": "g1", "pane": -1}),
            broken(requester="g1"),
            broken(state="pending"),
            broken(state=None),
            broken(status="great"),
            broken(read="yes"),
            broken(collected=1),
            broken(reason="invented"),
            broken(reason=None),
            broken(round="2"),
            broken(round=True),
            broken(round=0),
            broken(handed_at=5),
            broken(handed_at="x" * 200),
            broken(label=3),
            broken(label="two\nlines"),
            broken(label="y" * 500),
            broken(requester={"group": "g1", "pane": 0}, worker={"group": "g1", "pane": 0}),
        ]
        restored = translate_for_restore(bad + [good], mapping)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]["requester_session_id"], "p")

    def test_restore_does_not_coerce_types(self):
        entry = translate_for_capture([make_link()], COORDS, AGENTS)[0]
        entry["round"] = 2.0
        self.assertEqual(translate_for_restore([entry], {("g1", 0): "p", ("g1", 1): "q"}), [])

    def test_restore_caps_a_runaway_block(self):
        entry = translate_for_capture([make_link()], COORDS, AGENTS)[0]
        restored = translate_for_restore(
            [entry] * (MAX_ASSIGNMENTS + 10), {("g1", 0): "p", ("g1", 1): "q"}
        )
        self.assertEqual(len(restored), MAX_ASSIGNMENTS)

    def test_empty_inputs_translate_to_nothing(self):
        self.assertEqual(translate_for_capture([], {}, []), [])
        self.assertEqual(translate_for_restore([], {}), [])


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.history = CrewHistory()

    def install(self, workspace="w1", **kwargs):
        links = [make_link(**kwargs)]
        return self.history.install(workspace, translate_for_restore(
            translate_for_capture(links, COORDS, AGENTS),
            {("g1", 0): "a", ("g1", 1): "b", ("g1", 2): "c"},
        ))

    def test_snapshot_is_link_fields_plus_restored_with_a_fresh_link_id(self):
        self.assertEqual(self.install(), 1)
        links = self.history.snapshot()
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(set(link), set(LINK_FIELDS) | {"restored"})
        self.assertIs(link["restored"], True)
        self.assertTrue(link["link_id"])
        self.assertNotEqual(link["link_id"], "feedfacefeedface")
        for forbidden in ("text", "result", "receipt", "handoff_id", "revision"):
            self.assertNotIn(forbidden, link)

    def test_install_rebuilds_from_link_fields_so_extras_cannot_be_held(self):
        link = translate_for_restore(
            translate_for_capture([make_link()], COORDS, AGENTS),
            {("g1", 0): "a", ("g1", 1): "b"},
        )[0]
        link.update(text="secret report", receipt="r3c3ipt", handoff_id="h", link_id="chosen")
        self.history.install("w1", [link, "junk", {"requester_session_id": "a"}])
        held = self.history.snapshot()
        self.assertEqual(len(held), 1)
        self.assertEqual(set(held[0]), set(LINK_FIELDS) | {"restored"})
        self.assertNotEqual(held[0]["link_id"], "chosen")
        self.assertNotIn("secret", json.dumps(held))

    def test_snapshot_returns_copies(self):
        self.install()
        self.history.snapshot()[0]["state"] = "tampered"
        self.assertEqual(self.history.snapshot()[0]["state"], "reported")

    def test_install_replaces_that_workspaces_links_only(self):
        self.install("w1")
        self.install("w2", label="other")
        self.assertEqual(self.history.count(), 2)
        self.install("w1", label="again")
        labels = sorted(link["label"] for link in self.history.snapshot())
        self.assertEqual(labels, ["again", "other"])
        self.history.install("w1", [])
        self.assertEqual([link["label"] for link in self.history.snapshot()], ["other"])

    def test_forget_session_drops_every_link_naming_the_pane(self):
        self.install("w1")
        self.history.install("w2", [make_link("c", "a", state="ended", status="")])
        self.history.install("w3", [make_link("c", "z", state="ended", status="")])
        self.assertEqual(self.history.forget_session("a"), 2)
        remaining = self.history.snapshot()
        self.assertEqual([link["worker_session_id"] for link in remaining], ["z"])
        self.assertEqual(self.history.forget_session("a"), 0)
        self.assertEqual(self.history.forget_session(""), 0)

    def test_supersede_drops_history_for_those_pairs_only(self):
        self.history.install(
            "w1", [make_link("a", "b"), make_link("a", "c"), make_link("b", "a")]
        )
        self.assertEqual(self.history.supersede([("a", "b")]), 1)
        pairs = {
            (link["requester_session_id"], link["worker_session_id"])
            for link in self.history.snapshot()
        }
        self.assertEqual(pairs, {("a", "c"), ("b", "a")})
        self.assertEqual(self.history.supersede([]), 0)
        self.assertEqual(self.history.supersede([("", "")]), 0)

    def test_a_workspace_is_capped_keeping_its_newest_links(self):
        history = CrewHistory(max_links=3)
        history.install("w1", [make_link("a", f"b{i}", label=str(i)) for i in range(5)])
        self.assertEqual([link["label"] for link in history.snapshot()], ["2", "3", "4"])

    def test_the_cap_is_per_workspace_never_evicting_another_workspace(self):
        history = CrewHistory()
        first = MAX_ASSIGNMENTS - 56
        second = 100
        history.install("w1", [make_link("a", f"b{i}", label=str(i)) for i in range(first)])
        history.install("w2", [make_link("c", f"d{i}", label=str(i)) for i in range(second)])
        self.assertGreater(first + second, MAX_ASSIGNMENTS)
        self.assertEqual(history.count(), first + second)
        held = history.snapshot()
        requesters = [link["requester_session_id"] for link in held]
        self.assertEqual(requesters.count("a"), first)
        self.assertEqual(requesters.count("c"), second)

    def test_reset_empties_the_store(self):
        self.install()
        self.history.reset()
        self.assertEqual(self.history.snapshot(), [])
        self.assertEqual(self.history.count(), 0)

    def test_installed_links_are_ended_never_working(self):
        self.history.install("w1", [make_link(state="working", status="", reason="")])
        link = self.history.snapshot()[0]
        self.assertEqual((link["state"], link["reason"]), ("ended", RESTARTED))

    def test_module_level_history_is_invisible_to_the_result_store(self):
        crew_history.reset()
        before = live_results.count()
        try:
            crew_history.install("w1", [make_link("a", "b")])
            self.assertEqual(crew_history.snapshot()[0]["worker_session_id"], "b")
            self.assertEqual(live_results.count(), before)
            self.assertIsNone(live_results.live_assignment("b"))
            self.assertEqual(live_results.links_snapshot(), [])
            self.assertEqual(crew_history.forget_session("b"), 1)
            self.assertEqual(crew_history.snapshot(), [])
        finally:
            crew_history.reset()

    def test_a_private_store_stays_independent_of_the_history(self):
        store = ResultStore()
        self.install()
        self.assertEqual(store.count(), 0)
        self.assertEqual(store.links_snapshot(), [])


if __name__ == "__main__":
    unittest.main()
