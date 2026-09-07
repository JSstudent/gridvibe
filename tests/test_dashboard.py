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
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
from web import api  # noqa: E402
from web import terminal_io as web_terminal_io  # noqa: E402
from web.agent_activity import (  # noqa: E402
    ACTIVITY_UNKNOWN,
    ACTIVITY_WORKING,
    blank_agent_activity,
    note_agent_output,
)
from web.dashboard import PANE_FIELDS, compose_dashboard  # noqa: E402

ESC = "\x1b"
BEL = "\x07"


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
            snapshot["totals"], {"workspaces": 1, "sessions": 1, "agents": 1}
        )

    def test_a_server_with_no_agent_anywhere_composes_an_empty_tree(self):
        snapshot = self._compose(
            sessions_by_group={"g1": [plain_session("s1", "g1")]}
        )
        self.assertEqual(snapshot["workspaces"], [])
        self.assertEqual(
            snapshot["totals"], {"workspaces": 0, "sessions": 0, "agents": 0}
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
            {"workspaces": 2, "sessions": 2, "agents": 2},
        )


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
            payload["totals"], {"workspaces": 1, "sessions": 1, "agents": 1}
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

    def test_an_empty_server_answers_rather_than_failing(self):
        payload = self.client.get("/api/dashboard").get_json()
        self.assertEqual(payload["workspaces"], [])
        self.assertEqual(payload["totals"]["agents"], 0)

    def test_the_dashboard_page_is_served(self):
        """Its own page, in no workspace, so it takes no workspace argument."""
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("agentDashboardBody", body)
        self.assertIn("dashboard-window.js", body)


if __name__ == "__main__":
    unittest.main()
