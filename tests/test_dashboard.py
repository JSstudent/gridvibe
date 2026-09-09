"""The one reading of every agent that is running.

Three layers, each exercised where it actually decides something:

- **the composer** (`web/dashboard.py`), which is pure, so it is driven with
  plain dictionaries and asserted on the tree it returns;
- **the observer's stream side** (`web/terminal_io.py`), where the check is
  ownership rather than parsing: a pane's reading lives on its own connection
  entry, so a retired transport's record must never reach the dashboard;
- **the route**, end to end over the real manager and the real Flask app.

What is pinned:

- **Agents, and only agents.** A pane that is not running one is not a row, and
  a group or workspace left holding none is not a heading. The filter is the
  server's so the page and the payload cannot disagree about what "empty" is.
- **The filter does not renumber the panes.** A pane's `index` is its position
  in its *whole* group — it is what names the pane and what focuses it — so an
  agent sitting third in a four-pane group still says 2 after the two panes in
  front of it are dropped.
- **The payload is built, never filtered.** A pane row is assembled from a fixed
  field list, so a credential added to `TerminalSession` later cannot arrive
  here by default. The password case asserts that directly.
- **Order is the order every other surface uses** — workspaces as
  `list_live_workspaces` returns them, groups in display order, panes in pane
  order — because a dashboard row that is not where the window would put it is
  a row you have to search for.
- **`None` activity is not a blank reading.** Saying "observed nothing" about a
  pane with no transport would be a claim, not an absence.
- **The two locks are never nested.** The route reads the activity snapshot
  before it touches the manager, which is what keeps a busy pane's pump thread
  off a dashboard poll.
- **`totals.working` is the badge's number, and it is not `totals.agents`.** The
  badge on the dashboard button is the only reading a page has while the dialog
  is shut, and "how many agent panes are open" is something the reader already
  knows. So the working total counts the panes that are *connected* and
  announcing work -- the same override the row's own dot makes -- which is what
  keeps the number a tally of the dots it labels.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from sessions.manager import SessionStatus  # noqa: E402
from web import api  # noqa: E402
from web import terminal_io as web_terminal_io  # noqa: E402
from web.agent_activity import (  # noqa: E402
    ACTIVITY_IDLE,
    ACTIVITY_UNKNOWN,
    ACTIVITY_WORKING,
    blank_agent_activity,
    note_agent_output,
)
from web.dashboard import PANE_FIELDS, compose_dashboard  # noqa: E402

ESC = "\x1b"
BEL = "\x07"
CRLF = "\r\n"


def workspace(workspace_id, label="", active_group_id=""):
    return {
        "workspace_id": workspace_id,
        "label": label,
        "created_at": 1000.0,
        "active_group_id": active_group_id,
        "group_count": 0,
        "retain_when_empty": False,
    }


def group(group_id, workspace_id, name="Session"):
    return {
        "group_id": group_id,
        "workspace_id": workspace_id,
        "name": name,
        "connection_mode": "ssh",
        "layout": "grid",
        "saved_session_id": "",
        "created_at": 2000.0,
    }


def session(session_id, group_id, **overrides):
    """One agent pane, which is the only kind this surface lists."""
    payload = {
        "session_id": session_id,
        "group_id": group_id,
        "host": "10.0.0.5",
        "directory": "/srv/app",
        "current_directory": None,
        "title": "Terminal 1",
        "mode": "ssh",
        "startup_mode": "agent",
        "agent_selection": "claude",
        "custom_agent": "",
        "agent_auto_mode": False,
        "use_wsl": False,
        "use_powershell": False,
        "distribution": "",
        "status": "connected",
        "password": "hunter2",
        "username": "root",
        "port": 22,
    }
    payload.update(overrides)
    return payload


def plain_session(session_id, group_id, **overrides):
    """A pane that is not an agent, and so is not a row."""
    payload = {"startup_mode": "terminal", "agent_selection": ""}
    payload.update(overrides)
    return session(session_id, group_id, **payload)


class DashboardComposerTestCase(unittest.TestCase):
    def _compose(self, **overrides):
        arguments = {
            "workspaces": [workspace("default", active_group_id="g1")],
            "groups_by_workspace": {"default": [group("g1", "default")]},
            "sessions_by_group": {"g1": [session("s1", "g1")]},
            "activity": {},
            "now": 500.0,
        }
        arguments.update(overrides)
        return compose_dashboard(**arguments)

    def test_the_tree_nests_workspace_session_agent(self):
        snapshot = self._compose()
        self.assertEqual(len(snapshot["workspaces"]), 1)
        workspace_row = snapshot["workspaces"][0]
        self.assertEqual(workspace_row["workspace_id"], "default")
        self.assertEqual(workspace_row["group_count"], 1)
        self.assertEqual(workspace_row["agent_count"], 1)
        pane = workspace_row["groups"][0]["panes"][0]
        self.assertEqual(pane["session_id"], "s1")
        self.assertEqual(pane["workspace_id"], "default")
        self.assertEqual(pane["index"], 0)

    def test_a_pane_never_carries_a_credential(self):
        pane = self._compose()["workspaces"][0]["groups"][0]["panes"][0]
        self.assertNotIn("password", pane)
        self.assertNotIn("username", pane)
        # And the field list is the whole of what a pane may publish.
        self.assertEqual(
            set(pane) - set(PANE_FIELDS),
            {"workspace_id", "index", "directory", "activity"},
        )

    def test_a_pane_publishes_what_it_runs_on(self):
        """The three transport facts the tag is named from, and nothing more."""
        pane = self._compose(
            sessions_by_group={
                "g1": [
                    session(
                        "s1",
                        "g1",
                        mode="wsl",
                        use_wsl=True,
                        distribution="Ubuntu",
                    )
                ]
            }
        )["workspaces"][0]["groups"][0]["panes"][0]
        self.assertEqual(pane["mode"], "wsl")
        self.assertTrue(pane["use_wsl"])
        self.assertFalse(pane["use_powershell"])
        self.assertEqual(pane["distribution"], "Ubuntu")

    def test_the_observed_directory_wins_over_the_launch_one(self):
        panes = self._compose(
            sessions_by_group={
                "g1": [session("s1", "g1", current_directory="/srv/app/api")]
            }
        )["workspaces"][0]["groups"][0]["panes"]
        self.assertEqual(panes[0]["directory"], "/srv/app/api")

    def test_panes_keep_the_order_they_were_handed_in(self):
        panes = self._compose(
            sessions_by_group={
                "g1": [session("s1", "g1"), session("s2", "g1"), session("s3", "g1")]
            }
        )["workspaces"][0]["groups"][0]["panes"]
        self.assertEqual([pane["session_id"] for pane in panes], ["s1", "s2", "s3"])
        self.assertEqual([pane["index"] for pane in panes], [0, 1, 2])

    def test_a_pane_that_is_not_an_agent_is_not_a_row(self):
        group_row = self._compose(
            sessions_by_group={
                "g1": [
                    plain_session("s1", "g1"),
                    session("s2", "g1"),
                    plain_session("s3", "g1", startup_mode="explorer"),
                    plain_session("s4", "g1", startup_mode="browser"),
                ]
            }
        )["workspaces"][0]["groups"][0]
        self.assertEqual([pane["session_id"] for pane in group_row["panes"]], ["s2"])
        self.assertEqual(group_row["agent_count"], 1)
        # The group still says how big it actually is, so "1 agent" cannot read
        # as "a one-pane session".
        self.assertEqual(group_row["pane_count"], 4)

    def test_the_filter_does_not_renumber_the_panes_it_keeps(self):
        """`index` names and focuses the pane, so it stays its real position."""
        panes = self._compose(
            sessions_by_group={
                "g1": [
                    plain_session("s1", "g1"),
                    plain_session("s2", "g1"),
                    session("s3", "g1"),
                    plain_session("s4", "g1"),
                    session("s5", "g1"),
                ]
            }
        )["workspaces"][0]["groups"][0]["panes"]
        self.assertEqual([pane["session_id"] for pane in panes], ["s3", "s5"])
        self.assertEqual([pane["index"] for pane in panes], [2, 4])

    def test_a_session_with_no_agent_is_dropped_with_its_workspace(self):
        snapshot = self._compose(
            workspaces=[workspace("default"), workspace("ws2", label="api")],
            groups_by_workspace={
                "default": [group("g1", "default"), group("g2", "default")],
                "ws2": [group("g3", "ws2")],
            },
            sessions_by_group={
                "g1": [plain_session("s1", "g1")],
                "g2": [session("s2", "g2")],
                "g3": [plain_session("s3", "g3"), plain_session("s4", "g3")],
            },
        )
        self.assertEqual(
            [row["workspace_id"] for row in snapshot["workspaces"]], ["default"]
        )
        self.assertEqual(
            [row["group_id"] for row in snapshot["workspaces"][0]["groups"]], ["g2"]
        )
        self.assertEqual(
            snapshot["totals"],
            {"workspaces": 1, "sessions": 1, "agents": 1, "working": 0},
        )

    def test_a_workspace_reports_what_it_lists_and_what_closing_it_would_end(self):
        """Two counts, and they are different numbers whenever a workspace holds
        a plain terminal beside its agents. `group_count` is what this surface
        lists; `live_group_count` is what the close confirmation states, because
        a prompt about an irreversible act must not understate it."""
        snapshot = self._compose(
            workspaces=[workspace("default")],
            groups_by_workspace={
                "default": [
                    group("g1", "default"),
                    group("g2", "default"),
                    group("g3", "default"),
                ]
            },
            sessions_by_group={
                "g1": [session("s1", "g1")],
                "g2": [plain_session("s2", "g2")],
                "g3": [plain_session("s3", "g3")],
            },
        )
        workspace_row = snapshot["workspaces"][0]
        self.assertEqual(workspace_row["group_count"], 1)
        self.assertEqual(workspace_row["live_group_count"], 3)
        # The listed count is still what the totals are built from: this
        # surface is about agents, and only the confirmation asks the other
        # question.
        self.assertEqual(snapshot["totals"]["sessions"], 1)

    def test_a_server_with_no_agent_anywhere_composes_an_empty_tree(self):
        snapshot = self._compose(
            sessions_by_group={"g1": [plain_session("s1", "g1")]}
        )
        self.assertEqual(snapshot["workspaces"], [])
        self.assertEqual(
            snapshot["totals"],
            {"workspaces": 0, "sessions": 0, "agents": 0, "working": 0},
        )

    def test_the_active_group_hint_marks_exactly_one_row(self):
        snapshot = self._compose(
            groups_by_workspace={
                "default": [group("g1", "default"), group("g2", "default")]
            },
            sessions_by_group={"g1": [session("s1", "g1")], "g2": [session("s2", "g2")]},
        )
        groups = snapshot["workspaces"][0]["groups"]
        self.assertEqual([row["is_active"] for row in groups], [True, False])

    def test_a_pane_with_no_transport_carries_no_reading_at_all(self):
        snapshot = self._compose(
            sessions_by_group={
                "g1": [session("s1", "g1"), session("s2", "g1")]
            },
            activity={"s2": note_agent_output(None, 499.0)},
        )
        panes = snapshot["workspaces"][0]["groups"][0]["panes"]
        self.assertIsNone(panes[0]["activity"])
        self.assertEqual(panes[1]["activity"]["state"], ACTIVITY_WORKING)

    def test_a_transport_that_has_said_nothing_reads_as_unknown(self):
        snapshot = self._compose(activity={"s1": blank_agent_activity()})
        pane = snapshot["workspaces"][0]["groups"][0]["panes"][0]
        self.assertEqual(pane["activity"]["state"], ACTIVITY_UNKNOWN)

    def test_totals_count_agents_across_every_workspace(self):
        snapshot = self._compose(
            workspaces=[workspace("default"), workspace("ws2", label="api")],
            groups_by_workspace={
                "default": [group("g1", "default")],
                "ws2": [group("g2", "ws2")],
            },
            sessions_by_group={
                "g1": [
                    session("s1", "g1", agent_selection="claude"),
                    plain_session("s2", "g1"),
                ],
                "g2": [session("s3", "g2", agent_selection="codex")],
            },
        )
        self.assertEqual(
            snapshot["totals"],
            {"workspaces": 2, "sessions": 2, "agents": 2, "working": 0},
        )

    def test_the_working_total_counts_only_the_agents_that_are_working(self):
        """The badge on the button paints this number and nothing else, so it is
        the one that has to mean something: four open agents sitting at a prompt
        is a window with nothing to go and look at."""
        snapshot = self._compose(
            sessions_by_group={
                "g1": [
                    session("s1", "g1"),
                    session("s2", "g1"),
                    session("s3", "g1"),
                ]
            },
            # s1 wrote a moment ago; s2 last wrote long enough ago to be idle;
            # s3 has a transport that has never said anything.
            activity={
                "s1": note_agent_output(None, 499.0),
                "s2": note_agent_output(None, 100.0),
                "s3": blank_agent_activity(),
            },
        )
        states = [
            pane["activity"]["state"]
            for pane in snapshot["workspaces"][0]["groups"][0]["panes"]
        ]
        self.assertEqual(
            states, [ACTIVITY_WORKING, ACTIVITY_IDLE, ACTIVITY_UNKNOWN]
        )
        self.assertEqual(snapshot["totals"]["agents"], 3)
        self.assertEqual(snapshot["totals"]["working"], 1)

    def test_a_pane_that_is_not_connected_is_never_counted_as_working(self):
        """A dead shell's last reading is not an observation of a live one, and a
        connecting pane has nothing to observe yet -- the same override the row's
        own dot makes, so the count is a tally of the dots."""
        snapshot = self._compose(
            sessions_by_group={
                "g1": [
                    session("s1", "g1", status="disconnected"),
                    session("s2", "g1", status="error"),
                    session("s3", "g1", status="connecting"),
                    session("s4", "g1", status="pending"),
                ]
            },
            # Every one of them wrote a moment ago, so the reading itself says
            # "working" in all four cases.
            activity={
                name: note_agent_output(None, 499.0)
                for name in ("s1", "s2", "s3", "s4")
            },
        )
        panes = snapshot["workspaces"][0]["groups"][0]["panes"]
        self.assertEqual(
            [pane["activity"]["state"] for pane in panes], [ACTIVITY_WORKING] * 4
        )
        self.assertEqual(snapshot["totals"]["agents"], 4)
        self.assertEqual(snapshot["totals"]["working"], 0)

    def test_a_pane_with_no_transport_is_not_working(self):
        snapshot = self._compose(
            sessions_by_group={"g1": [session("s1", "g1")]}, activity={}
        )
        self.assertIsNone(snapshot["workspaces"][0]["groups"][0]["panes"][0]["activity"])
        self.assertEqual(snapshot["totals"]["working"], 0)

    def test_working_agents_are_counted_across_every_workspace(self):
        snapshot = self._compose(
            workspaces=[workspace("default"), workspace("ws2", label="api")],
            groups_by_workspace={
                "default": [group("g1", "default")],
                "ws2": [group("g2", "ws2")],
            },
            sessions_by_group={
                "g1": [session("s1", "g1"), session("s2", "g1")],
                "g2": [session("s3", "g2")],
            },
            activity={
                "s1": note_agent_output(None, 499.0),
                "s2": note_agent_output(None, 100.0),
                "s3": note_agent_output(None, 500.0),
            },
        )
        self.assertEqual(snapshot["totals"]["agents"], 3)
        self.assertEqual(snapshot["totals"]["working"], 2)


class DashboardObservationOwnershipTestCase(unittest.TestCase):
    """A reading belongs to the transport that produced it, and to no other."""

    def setUp(self):
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections.clear()
        self.addCleanup(self._clear_connections)

    def _clear_connections(self):
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections.clear()

    def test_the_snapshot_reports_what_a_live_pane_announced(self):
        connection = {"kind": "ssh"}
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections["s1"] = connection
        web_terminal_io._observe_agent_activity(
            connection, f"{ESC}]0;Claude: writing tests{BEL}working..."
        )
        reading = web_terminal_io.agent_activity_snapshot()["s1"]
        self.assertEqual(reading["title"], "Claude: writing tests")
        self.assertGreater(reading["last_output_at"], 0.0)

    def test_a_replaced_transport_takes_its_reading_with_it(self):
        retired = {"kind": "ssh"}
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections["s1"] = retired
        web_terminal_io._observe_agent_activity(retired, f"{ESC}]0;old agent{BEL}")

        replacement = {"kind": "ssh"}
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections["s1"] = replacement
        # The retiring shell's last frames can still be in flight, and they must
        # not put the old agent's title back on the pane that replaced it.
        web_terminal_io._observe_agent_activity(retired, f"{ESC}]0;still old{BEL}")

        self.assertEqual(web_terminal_io.agent_activity_snapshot()["s1"]["title"], "")

    def test_a_pane_with_no_connection_is_absent_rather_than_blank(self):
        self.assertEqual(web_terminal_io.agent_activity_snapshot(), {})


class DashboardRouteTestCase(unittest.TestCase):
    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections.clear()
        self.addCleanup(self._reset)

    def _reset(self):
        api.session_manager.reset_sessions()
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections.clear()

    def _launch_group(self):
        created = api.session_manager.create_group(
            name="API work",
            connection_mode="ssh",
            layout="grid",
            terminal_count=2,
            workspace_id="default",
        )
        agent = api.session_manager.create_session(
            group_id=created.group_id,
            host="10.0.0.5",
            directory="/srv/app",
            password="hunter2",
            title="Terminal 1",
            startup_mode="agent",
            initial_command_mode="agent",
            agent_selection="claude",
        )
        plain = api.session_manager.create_session(
            group_id=created.group_id,
            host="10.0.0.5",
            directory="/srv/app",
            password="hunter2",
            title="Terminal 2",
        )
        return created, agent, plain

    def test_the_route_returns_the_live_tree_of_agents(self):
        created, agent, plain = self._launch_group()
        payload = self.client.get("/api/dashboard").get_json()

        self.assertEqual(
            payload["totals"],
            # Nothing is connected and nothing has written, so the badge's own
            # number is 0 while the list still holds a row.
            {"workspaces": 1, "sessions": 1, "agents": 1, "working": 0},
        )

        group_row = payload["workspaces"][0]["groups"][0]
        self.assertEqual(group_row["group_id"], created.group_id)
        self.assertEqual(group_row["name"], "API work")
        # The plain terminal beside it is a pane of the group and not a row.
        self.assertEqual(group_row["pane_count"], 2)
        self.assertEqual(
            [pane["session_id"] for pane in group_row["panes"]],
            [agent.session_id],
        )
        self.assertNotIn(
            plain.session_id, [pane["session_id"] for pane in group_row["panes"]]
        )
        self.assertEqual(group_row["panes"][0]["agent_selection"], "claude")
        self.assertEqual(group_row["panes"][0]["startup_mode"], "agent")

    def test_the_route_publishes_no_credential(self):
        self._launch_group()
        body = self.client.get("/api/dashboard").get_data(as_text=True)
        self.assertNotIn("hunter2", body)
        self.assertNotIn("password", body)

    def test_a_live_agents_reading_reaches_the_route(self):
        _, agent, _ = self._launch_group()
        connection = {"kind": "ssh"}
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections[agent.session_id] = connection
        web_terminal_io._observe_agent_activity(
            connection, f"{ESC}]2;Claude: fixing the parser{BEL}{ESC}]9;4;1;65{BEL}"
        )

        panes = self.client.get("/api/dashboard").get_json()["workspaces"][0]["groups"][0]["panes"]
        reading = panes[0]["activity"]
        self.assertEqual(reading["title"], "Claude: fixing the parser")
        self.assertEqual(reading["state"], ACTIVITY_WORKING)
        self.assertEqual(reading["progress_value"], 65)

    def test_the_working_total_follows_the_reading_the_route_publishes(self):
        """End to end, the number the button paints and the row's own dot come
        out of one pass: the pane has to be connected *and* announcing work."""
        _, agent, _ = self._launch_group()
        connection = {"kind": "ssh"}
        with web_terminal_io.connection_lock:
            web_terminal_io.ssh_connections[agent.session_id] = connection
        web_terminal_io._observe_agent_activity(connection, "Reticulating splines" + CRLF)

        # Still pending as far as the transport is concerned, so the reading is
        # not yet a reading of anything.
        before = self.client.get("/api/dashboard").get_json()
        self.assertEqual(before["totals"]["agents"], 1)
        self.assertEqual(before["totals"]["working"], 0)

        api.session_manager.update_session_status(
            agent.session_id, SessionStatus.CONNECTED
        )
        after = self.client.get("/api/dashboard").get_json()
        pane = after["workspaces"][0]["groups"][0]["panes"][0]
        self.assertEqual(pane["activity"]["state"], ACTIVITY_WORKING)
        self.assertEqual(after["totals"]["working"], 1)

        # And a pane whose transport goes away stops being counted, however
        # recently it wrote.
        api.session_manager.update_session_status(
            agent.session_id, SessionStatus.DISCONNECTED
        )
        gone = self.client.get("/api/dashboard").get_json()
        self.assertEqual(gone["totals"]["agents"], 1)
        self.assertEqual(gone["totals"]["working"], 0)

    def test_an_empty_server_answers_rather_than_failing(self):
        payload = self.client.get("/api/dashboard").get_json()
        self.assertEqual(payload["workspaces"], [])
        self.assertEqual(payload["totals"]["agents"], 0)
        self.assertEqual(payload["totals"]["working"], 0)

    def test_both_pages_carry_the_dialog_and_no_page_serves_it(self):
        """It is a dialog on the page that opened it, so it is one partial on
        both pages and no route of its own -- and a `/dashboard` that still
        answered would be a second, divergent copy of this surface reachable by
        typing a URL."""
        for path in ("/", "/terminals"):
            with self.subTest(path=path):
                body = self.client.get(path).get_data(as_text=True)
                self.assertIn('id="agentDashboardShell"', body)
                self.assertIn("agentDashboardBody", body)
                self.assertIn("dashboard-dialog.js", body)
                # The button that raises it, and the badge that is the reason
                # to.
                self.assertIn('id="dashboardBtn"', body)
        self.assertEqual(self.client.get("/dashboard").status_code, 404)


if __name__ == "__main__":
    unittest.main()
