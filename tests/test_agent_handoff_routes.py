"""Handing an agent its task, end to end through GridVibe's own routes.

Three ways a task reaches an agent, and the one place it is announced:

- **At a split (1a).** The intent carries only a handle -- every polling page is
  shown the intent -- and the split route takes it exactly once, for its own
  source pane, *before* it appends anything. A replayed or foreign handle is
  refused with the group untouched.
- **At a launch (1a).** Each pane's task is taken off its config before
  anything reads it, so a saved preset and the runtime snapshot never hold it.
- **At a relaunch (1b).** Validated before any gate; bound only after every
  refusal and just before the new shell starts, so a refused relaunch leaves
  nothing behind; and never onto another machine, override or not.
- **At the startup sequence**, where the launch line gains the one constant
  sentence -- or the pane's *output* says why it could not.

Every refusal here is also asserted on what did not happen: no intent, no
handoff, no pane, no file.
"""

import json
import os
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import tests  # noqa: E402,F401 - redirects durable state away from the real files
import tests.test_session_shell as shell_tests  # noqa: E402
from tests.test_agent_handoff_files import FakeSftp  # noqa: E402
from web import agents as web_agents  # noqa: E402
from web import api, mcp_launch  # noqa: E402
from web import saved_sessions as web_saved_sessions  # noqa: E402
from web import terminal_io as web_terminal_io  # noqa: E402
from web.agent_activity import blank_agent_activity, note_agent_output  # noqa: E402
from web.agent_handoffs import (  # noqa: E402
    ANNOUNCED,
    FILE,
    HANDOFF_OPENING_PROMPT,
    INLINE,
    INLINE_TASK_MAX_CHARS,
    MAX_TASK_BYTES,
    PAGED,
    UNDELIVERABLE,
    WAITING,
)
from web.agent_handoffs import handoffs as store  # noqa: E402
from web.window_intents import window_intents  # noqa: E402

QUOTED = f'"{HANDOFF_OPENING_PROMPT}"'
BRIEF = "Findings: the retry loop in sync.py never backs off. Proposed fix: cap it."


def _detect_found(target, binary):
    return {"found": True, "path": f"/usr/bin/{binary}"}


class _RouteCase(unittest.TestCase):
    """A real app client, an empty handoff store and an empty intent store."""

    def setUp(self):
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        store.reset()
        self.addCleanup(store.reset)
        window_intents.reset()
        self.addCleanup(window_intents.reset)
        with api.connection_lock:
            api.ssh_connections.clear()
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def _group(self, mode="wsl"):
        return api.session_manager.create_group(
            name="G", connection_mode=mode, layout="single", terminal_count=1
        )

    def _pane(self, group=None, **overrides):
        group = group or self._group(overrides.get("mode", "wsl"))
        fields = {
            "group_id": group.group_id,
            "host": "cmd",
            "directory": self.temp.name,
            "mode": "wsl",
            "startup_mode": "terminal",
            "title": "Terminal",
        }
        fields.update(overrides)
        session = api.session_manager.create_session(**fields)
        api.session_manager.update_session_status(
            session.session_id, api.SessionStatus.CONNECTED
        )
        return api.session_manager.get_session(session.session_id)

    def _agent_pane(self, group=None, agent="claude", **overrides):
        fields = dict(
            startup_mode="agent",
            initial_command_mode="agent",
            initial_command=agent,
            agent_selection=agent,
            agent_mcp=True,
            title="Claude 1",
        )
        fields.update(overrides)
        return self._pane(group, **fields)

    def _ssh_pane(self, group=None, **overrides):
        fields = dict(host="example.com", username="ubuntu", port=22, mode="ssh", directory="/srv/app")
        fields.update(overrides)
        return self._pane(group or self._group("ssh"), **fields)

    def _group_state(self, group_id):
        return [
            session.session_id for session in api.session_manager.get_group_sessions(group_id)
        ]


# ==================== 1a: the split ====================


class SplitIntentTaskTestCase(_RouteCase):
    def _intent(self, source, body):
        with patch.object(web_agents, "_detect_agent_binary_cached", side_effect=_detect_found):
            return self.client.post(f"/api/sessions/{source.session_id}/split-intent", json=body)

    def _body(self, caller, **overrides):
        body = {
            "axis": "horizontal",
            "kind": "agent",
            "agent": "codex",
            "task": BRIEF,
            "origin_session_id": caller.session_id,
        }
        body.update(overrides)
        return body

    def test_only_a_handle_rides_in_the_intent(self):
        caller = self._agent_pane()

        response = self._intent(caller, self._body(caller))

        self.assertEqual(response.status_code, 201, response.get_json())
        intent = response.get_json()
        request = intent["split_request"]
        self.assertRegex(request["handoff_id"], r"^[0-9a-f]{32}$")
        self.assertTrue(request["mcp"])
        self.assertNotIn("task", request)
        self.assertEqual(intent["handoff"], {"state": "waiting", "chars": len(BRIEF), "delivery": None})
        # What every polling page is shown.
        polled = json.dumps(window_intents.pending())
        self.assertIn(request["handoff_id"], polled)
        self.assertNotIn(BRIEF, polled)
        self.assertNotIn("retry loop", json.dumps(self.client.get("/api/windows/intents").get_json()))
        self.assertEqual(store.count(), 1)

    def test_each_refusal_records_nothing(self):
        caller = self._agent_pane()
        cases = {
            "not an agent": ({"kind": "terminal", "agent": None}, "set kind to 'agent'"),
            "no kind": ({"kind": None, "agent": None}, "set kind to 'agent'"),
            "incapable agent": ({"agent": "grok"}, "grok cannot be handed a task"),
            "mcp false": ({"mcp": False}, "leave 'mcp' out"),
            "control character": ({"task": "a\x1bb"}, "U+001B"),
            "too large": ({"task": "a" * (MAX_TASK_BYTES + 1)}, "Nothing was truncated"),
            "empty": ({"task": "   "}, "is empty"),
            "not text": ({"task": ["x"]}, "must be text"),
            "no caller": ({"origin_session_id": None}, "names no open pane"),
            "closed caller": ({"origin_session_id": "gone"}, "names no open pane"),
        }
        for label, (overrides, expected) in cases.items():
            with self.subTest(label):
                body = self._body(caller, **overrides)
                body = {key: value for key, value in body.items() if value is not None}
                response = self._intent(caller, body)
                self.assertIn(response.status_code, (400, 403), response.get_json())
                self.assertIn(expected, response.get_json()["error"])
                self.assertEqual(window_intents.pending(), [])
                self.assertEqual(store.count(), 0)

    def test_a_task_never_goes_to_another_machine(self):
        caller = self._agent_pane()
        remote = self._ssh_pane()

        response = self._intent(remote, self._body(caller))

        payload = response.get_json()
        self.assertEqual(response.status_code, 403)
        self.assertTrue(payload["error"].startswith("[machine gate] This pane runs on example.com over SSH"))
        self.assertIn("the pane asking runs on GridVibe's own machine", payload["error"])
        self.assertIn("override does not change that", payload["error"])
        self.assertEqual((payload["gate"], payload["waivable"]), ("machine", False))
        self.assertNotIn("confirm", payload)
        self.assertEqual(window_intents.pending(), [])
        self.assertEqual(store.count(), 0)

    def test_a_split_with_no_task_on_another_machine_is_still_allowed(self):
        caller = self._agent_pane()
        remote = self._ssh_pane()
        body = self._body(caller)
        del body["task"]

        with patch.object(web_agents, "_detect_agent_binary_cached", side_effect=_detect_found), \
                patch.object(web_agents, "_agent_preflight_payload", return_value={"status": "present"}):
            response = self.client.post(f"/api/sessions/{remote.session_id}/split-intent", json=body)

        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertNotIn("handoff", response.get_json())

    def test_two_ssh_panes_on_the_same_host_user_and_port_pass(self):
        group = self._group("ssh")
        caller = self._ssh_pane(group, startup_mode="agent", initial_command="claude",
                                initial_command_mode="agent", agent_selection="claude")
        target = self._ssh_pane(group)

        with patch.object(web_agents, "_agent_preflight_payload", return_value={"status": "present"}):
            response = self._intent(target, self._body(caller))

        self.assertEqual(response.status_code, 201, response.get_json())


class SplitTakesTheHandleTestCase(_RouteCase):
    def _record(self, caller, source=None, **overrides):
        source = source or caller
        body = {
            "axis": "vertical", "kind": "agent", "agent": "codex", "task": BRIEF,
            "origin_session_id": caller.session_id,
        }
        body.update(overrides)
        with patch.object(web_agents, "_detect_agent_binary_cached", side_effect=_detect_found):
            response = self.client.post(f"/api/sessions/{source.session_id}/split-intent", json=body)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["split_request"]

    def _split(self, source, split_request, started=None):
        def start(target, session_id):
            if started is not None:
                started.append(store.public_state(session_id))

        with patch.object(web_agents, "_detect_agent_binary_cached", side_effect=_detect_found), \
                patch.object(web_terminal_io, "_resolve_live_terminal_cwd", return_value=None), \
                patch.object(api.socketio, "start_background_task", side_effect=start):
            return self.client.post(f"/api/sessions/{source.session_id}/split", json=split_request)

    def test_the_handle_is_bound_to_the_new_pane_before_it_connects(self):
        caller = self._agent_pane()
        split_request = self._record(caller)
        started = []

        response = self._split(caller, split_request, started)

        self.assertEqual(response.status_code, 201, response.get_json())
        created = response.get_json()["session"]
        self.assertEqual(created["startup_mode"], "agent")
        self.assertEqual(created["agent_selection"], "codex")
        self.assertTrue(created["agent_mcp"])
        self.assertEqual(created["handoff"]["state"], WAITING)
        self.assertEqual(created["handoff"]["from_session_id"], caller.session_id)
        # The connector found it already waiting.
        self.assertEqual(started[0]["state"], WAITING)
        self.assertNotIn("task", json.dumps(created))

    def test_a_source_that_closes_between_take_and_bind_costs_the_task_not_the_split(self):
        caller = self._agent_pane()
        split_request = self._record(caller)

        def gone(handoff_id, session_id):
            store.forget_session(caller.session_id)
            raise api.HandoffError("That task is no longer waiting for a pane.", 409)

        with patch.object(store, "bind", side_effect=gone):
            response = self._split(caller, split_request)

        self.assertEqual(response.status_code, 201, response.get_json())
        self.assertIsNone(response.get_json()["session"]["handoff"])
        self.assertEqual(store.count(), 0)

    def test_a_replayed_handle_is_refused_with_the_group_untouched(self):
        caller = self._agent_pane()
        split_request = self._record(caller)
        self.assertEqual(self._split(caller, split_request).status_code, 201)
        before = self._group_state(caller.group_id)

        response = self._split(caller, split_request)

        self.assertEqual(response.status_code, 409)
        self.assertIn("already used", response.get_json()["error"])
        self.assertIn("No pane was added", response.get_json()["error"])
        self.assertEqual(self._group_state(caller.group_id), before)

    def test_a_handle_posted_against_another_pane_is_refused_and_not_burnt(self):
        group = self._group()
        caller = self._agent_pane(group)
        other = self._pane(group)
        split_request = self._record(caller)
        before = self._group_state(group.group_id)

        response = self._split(other, split_request)

        self.assertEqual(response.status_code, 409)
        self.assertIn("different pane", response.get_json()["error"])
        self.assertEqual(self._group_state(group.group_id), before)
        # The split it was recorded for still works.
        self.assertEqual(self._split(caller, split_request).status_code, 201)

    def test_an_expired_handle_is_refused(self):
        caller = self._agent_pane()
        split_request = self._record(caller)
        store.reset()

        response = self._split(caller, split_request)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self._group_state(caller.group_id)), 1)

    def test_a_handle_on_a_pane_that_would_not_be_an_agent_is_refused_before_it_is_spent(self):
        caller = self._agent_pane()
        split_request = self._record(caller)
        tampered = dict(split_request, kind="terminal")

        response = self._split(caller, tampered)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(self._group_state(caller.group_id)), 1)
        self.assertEqual(self._split(caller, split_request).status_code, 201)

    def test_a_group_at_capacity_refuses_before_the_handle_is_spent(self):
        caller = self._agent_pane()
        split_request = self._record(caller)

        with patch.object(api.runtime_config, "snapshot", return_value=SimpleNamespace(max_sessions=1)):
            response = self._split(caller, split_request)

        self.assertEqual(response.status_code, 400)
        store.take(split_request["handoff_id"], caller.session_id)


# ==================== 1a: the launch ====================


class LaunchTaskTestCase(_RouteCase):
    def _launch(self, body, preflight=None):
        with patch.object(api.socketio, "start_background_task"), patch.object(
            web_agents, "_agent_preflight_payload",
            return_value=preflight or {"status": "present"},
        ):
            response = self.client.post("/api/sessions", json=body)
        return response.status_code, response.get_json()

    def _body(self, caller, panes, **overrides):
        body = {
            "connection_mode": "wsl",
            "origin_session_id": caller.session_id if caller else None,
            "new_workspace": True,
            "workspace_label": "handoff",
            "session_name": "handoff",
            "layout": "vertical",
            "sessions": panes,
        }
        body.update(overrides)
        return {key: value for key, value in body.items() if value is not None}

    def _agent_config(self, agent="codex", task=BRIEF, **overrides):
        config = {
            "title": f"{agent} 1",
            "directory": self.temp.name,
            "startup_mode": "agent",
            "initial_command_mode": "agent",
            "initial_command": agent,
            "agent_selection": agent,
            "agent_mcp": True,
            "agent_depth": 1,
        }
        if task is not None:
            config["task"] = task
        config.update(overrides)
        return config

    def test_each_pane_gets_its_own_task_bound_and_nothing_durable_holds_it(self):
        caller = self._agent_pane()
        second_brief = "Write tests for whatever claude changes."

        status, payload = self._launch(
            self._body(caller, [self._agent_config("claude", "Refactor the parser."),
                                self._agent_config("codex", second_brief)])
        )

        self.assertEqual(status, 201, payload)
        first, second = payload["sessions"]
        self.assertEqual(first["handoff"]["state"], WAITING)
        self.assertEqual(second["handoff"]["chars"], len(second_brief))
        self.assertEqual(store.pending_for(second["session_id"]).text, second_brief)
        self.assertEqual(store.pending_for(first["session_id"]).source_session_id, caller.session_id)
        # Never in a response, a session record, the runtime snapshot or a preset.
        for surface in (
            json.dumps(payload),
            json.dumps(api.session_manager.snapshot_live_workspaces(), default=str),
            json.dumps(
                web_saved_sessions._normalize_terminal_entries(
                    [api.session_manager.get_session(second["session_id"]).to_dict()],
                    "wsl",
                    minimum_count=1,
                ),
                default=str,
            ),
        ):
            self.assertNotIn(second_brief, surface)
            self.assertNotIn('"task"', surface)

    def test_a_pane_without_a_task_is_launched_as_before(self):
        caller = self._agent_pane()

        status, payload = self._launch(self._body(caller, [self._agent_config(task=None, agent_mcp=False)]))

        self.assertEqual(status, 201, payload)
        self.assertIsNone(payload["sessions"][0]["handoff"])
        self.assertFalse(payload["sessions"][0]["agent_mcp"])
        self.assertEqual(store.count(), 0)

    def test_a_task_turns_the_tools_on_when_they_were_not_stated(self):
        caller = self._agent_pane()
        config = self._agent_config()
        del config["agent_mcp"]

        status, payload = self._launch(self._body(caller, [config]))

        self.assertEqual(status, 201, payload)
        self.assertTrue(payload["sessions"][0]["agent_mcp"])

    def test_every_refusal_launches_nothing(self):
        caller = self._agent_pane()
        cases = {
            "no calling pane": (self._body(None, [self._agent_config()]), 403, "names no open pane"),
            # Refused earlier, by the launch's own origin rule: a closed pane
            # names no machine, task or not.
            "calling pane closed": (self._body(caller, [self._agent_config()], origin_session_id="gone"), 400, "no longer open"),
            "terminal pane": (self._body(caller, [self._agent_config(startup_mode="terminal", initial_command_mode="command")]), 400, "Pane 1: A task is handed to an agent pane"),
            "incapable agent": (self._body(caller, [self._agent_config("grok")]), 400, "grok cannot be handed a task"),
            "mcp false": (self._body(caller, [self._agent_config(agent_mcp=False)]), 400, "leave 'mcp' out"),
            "second pane bad": (self._body(caller, [self._agent_config(), self._agent_config(task="x\x07")]), 400, "Pane 2:"),
            "restore": (self._body(caller, [self._agent_config()], restore=True), 400, "A restore carries no task"),
        }
        for label, (body, expected_status, expected) in cases.items():
            with self.subTest(label):
                groups_before = len(api.session_manager.get_all_groups())
                status, payload = self._launch(body)
                self.assertEqual(status, expected_status, payload)
                self.assertIn(expected, payload["error"])
                self.assertEqual(len(api.session_manager.get_all_groups()), groups_before)
                self.assertEqual(store.count(), 0)

    def test_a_pane_whose_agent_is_not_installed_holds_no_task_and_says_so(self):
        caller = self._agent_pane()

        status, payload = self._launch(
            self._body(caller, [self._agent_config()]),
            preflight={"status": "missing", "message": "codex is not installed."},
        )

        self.assertEqual(status, 201, payload)
        self.assertIsNone(payload["sessions"][0]["handoff"])
        self.assertEqual(store.count(), 0)
        self.assertTrue(any("was not handed over" in item for item in payload["warnings"]))


# ==================== the handoff route ====================


class HandoffRouteTestCase(_RouteCase):
    def test_an_unknown_pane_is_404(self):
        self.assertEqual(self.client.get("/api/sessions/nope/handoff").status_code, 404)

    def test_nothing_handed_over_is_200_and_says_so(self):
        pane = self._agent_pane()

        response = self.client.get(f"/api/sessions/{pane.session_id}/handoff")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get_json()["handoff"])

    def test_an_announced_task_is_read_and_its_state_published(self):
        caller = self._agent_pane()
        pane = self._agent_pane(agent="codex")
        handoff_id = store.create(BRIEF, source_session_id=caller.session_id, session_id=pane.session_id)
        store.announce(handoff_id, delivery=INLINE)

        listed = self.client.get("/api/sessions").get_json()["sessions"]
        state = {row["session_id"]: row["handoff"] for row in listed}
        self.assertEqual(state[pane.session_id]["state"], ANNOUNCED)
        self.assertIsNone(state[caller.session_id])
        self.assertNotIn(BRIEF, json.dumps(listed))

        response = self.client.get(f"/api/sessions/{pane.session_id}/handoff")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["task"], BRIEF)
        single = self.client.get(f"/api/sessions/{pane.session_id}").get_json()
        self.assertEqual(single["handoff"]["state"], "read")
        self.assertNotIn(BRIEF, json.dumps(single))

    def test_a_bad_offset_is_400(self):
        pane = self._agent_pane()
        handoff_id = store.create("abc", source_session_id="x", session_id=pane.session_id)
        store.announce(handoff_id, delivery=PAGED)

        for offset in ("x", "9", "-1"):
            with self.subTest(offset=offset):
                response = self.client.get(f"/api/sessions/{pane.session_id}/handoff?offset={offset}")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.client.get(f"/api/sessions/{pane.session_id}/handoff?offset=1").get_json()["task"],
            "bc",
        )

    def test_closing_the_pane_drops_its_task(self):
        pane = self._agent_pane()
        store.create(BRIEF, source_session_id="x", session_id=pane.session_id)

        response = self.client.delete(f"/api/sessions/{pane.session_id}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.count(), 0)


# ==================== the startup sequence ====================


class StartupAnnouncementTestCase(_RouteCase):
    def setUp(self):
        super().setUp()
        self.config_path = Path(self.temp.name) / ".gridvibe_mcp.json"
        self.config_path.write_text(
            '{"mcpServers": {"gridvibe": {"command": "python", "args": ["m.py"]}}}',
            encoding="utf-8",
        )
        patcher = patch.object(mcp_launch, "mcp_config_path", lambda: str(self.config_path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _session(self, agent="claude", **overrides):
        fields = dict(
            session_id="pane-b", group_id="", directory="", initial_command=agent,
            initial_command_mode="agent", agent_selection=agent, agent_auto_mode=False,
            agent_mcp=True, mode="wsl", use_wsl=False, use_powershell=False,
        )
        fields.update(overrides)
        return SimpleNamespace(**fields)

    def _run(self, session, connection=None):
        connection = connection or {"kind": "local", "shell_kind": "cmd", "launch_cwd_applied": True}
        sent, published = [], []
        with patch.object(web_terminal_io, "_send_connection_input",
                          side_effect=lambda conn, data: sent.append(data)), \
                patch.object(web_terminal_io, "_publish_ssh_terminal_output",
                             side_effect=lambda sid, out, conn=None: published.append(out)), \
                patch.object(web_terminal_io, "_note_agent_conversation_command"), \
                patch.object(web_terminal_io.runtime_config, "terminal_shell_integration", False), \
                patch.object(web_terminal_io.time, "sleep"):
            web_terminal_io._run_startup_sequence(connection, session)
        return connection, sent, published

    def _bind(self, text=BRIEF, session_id="pane-b"):
        return store.create(text, source_session_id="pane-a", session_id=session_id,
                            from_title="Claude 1", from_agent="claude")

    def test_a_small_task_is_announced_inline_on_the_launch_line(self):
        handoff_id = self._bind()

        connection, sent, published = self._run(self._session())

        self.assertEqual(len(sent), 1)
        self.assertIn(f"claude {QUOTED} --mcp-config", sent[0])
        self.assertNotIn(BRIEF, sent[0])
        self.assertEqual(published, [])
        self.assertEqual(connection["handoff_id"], handoff_id)
        self.assertEqual(store.public_state("pane-b")["delivery"], INLINE)
        self.assertEqual(store.read("pane-b")["task"], BRIEF)

    def test_no_handoff_means_the_line_it_always_was(self):
        _connection, sent, _published = self._run(self._session())

        self.assertNotIn(QUOTED, sent[0])

    def test_a_large_task_travels_as_a_file_that_goes_with_the_connection(self):
        text = "line\n" * (INLINE_TASK_MAX_CHARS // 5 + 100)
        self._bind(text)

        connection, sent, _published = self._run(self._session())
        result = store.read("pane-b")

        self.assertEqual(result["delivery"], FILE)
        self.assertNotIn("task", result)
        path = Path(result["task_file"])
        self.assertTrue(path.is_file())
        self.assertTrue(path.read_text(encoding="utf-8").endswith(text))
        self.assertTrue(str(path).startswith(os.environ["GRIDVIBE_HANDOFF_DIR"]) or
                        os.path.realpath(path).startswith(os.path.realpath(os.environ["GRIDVIBE_HANDOFF_DIR"])))
        self.assertNotIn(str(path), sent[0])

        web_terminal_io._shutdown_connection(connection)

        self.assertFalse(path.exists())
        self.assertFalse(path.parent.exists())
        self.assertIsNone(store.public_state("pane-b"))

    @unittest.skipUnless(os.name == "nt", "a Windows path in its WSL form")
    def test_a_wsl_shell_is_given_the_mnt_form_of_the_path(self):
        self._bind("x" * (INLINE_TASK_MAX_CHARS + 1))

        self._run(self._session(use_wsl=True), {"kind": "local", "shell_kind": "wsl", "launch_cwd_applied": True})

        self.assertTrue(store.read("pane-b")["task_file"].startswith("/mnt/"))

    def test_a_file_that_cannot_be_written_falls_back_to_pages(self):
        text = "y" * (INLINE_TASK_MAX_CHARS * 2 + 1)
        self._bind(text)

        with patch.object(web_terminal_io, "write_local_handoff", return_value=None):
            _connection, sent, _published = self._run(self._session())

        self.assertIn(QUOTED, sent[0])
        first = store.read("pane-b")
        self.assertEqual(first["delivery"], PAGED)
        self.assertEqual(first["next_offset"], INLINE_TASK_MAX_CHARS)

    def test_an_ssh_pane_writes_its_file_on_its_own_host(self):
        self._bind("z" * (INLINE_TASK_MAX_CHARS + 1))
        sftp = FakeSftp()
        tunnel = {"sftp": sftp, "remote_path": "/home/ubuntu/.gridvibe/mcp-b.json",
                  "url": "http://127.0.0.1:41000/mcp/TOKEN"}

        connection, sent, _published = self._run(
            self._session(mode="ssh"),
            {"kind": "ssh", "shell_kind": "posix", "mcp_tunnel": tunnel},
        )

        result = store.read("pane-b")
        self.assertEqual(result["delivery"], FILE)
        self.assertTrue(result["task_file"].startswith("/home/ubuntu/.gridvibe/handoffs/"))
        self.assertIn(result["task_file"], sftp.files)
        self.assertEqual(tunnel["handoff_paths"], [result["task_file"]])
        self.assertIn(QUOTED, sent[0])

    def test_an_ssh_host_that_refuses_chmod_gets_pages_and_no_file(self):
        self._bind("z" * (INLINE_TASK_MAX_CHARS + 1))
        sftp = FakeSftp(refuse_chmod=True)
        tunnel = {"sftp": sftp, "remote_path": "/home/ubuntu/.gridvibe/mcp-b.json",
                  "url": "http://127.0.0.1:41000/mcp/TOKEN"}

        self._run(self._session(mode="ssh"), {"kind": "ssh", "shell_kind": "posix", "mcp_tunnel": tunnel})

        self.assertEqual(store.read("pane-b")["delivery"], PAGED)
        self.assertEqual(sftp.files, {})
        self.assertNotIn("handoff_paths", tunnel)

    def test_no_tools_means_no_sentence_and_the_output_says_why(self):
        self._bind()
        self.config_path.unlink()

        connection, sent, published = self._run(self._session())

        self.assertNotIn(QUOTED, sent[0])
        self.assertEqual(store.public_state("pane-b")["state"], UNDELIVERABLE)
        self.assertEqual(len(published), 1)
        self.assertIn("GridVibe: This pane was handed a task", published[0])
        self.assertIn("tool config", published[0])
        # Written to the output, never typed into the shell.
        self.assertFalse(any("handed a task" in line for line in sent))
        # Tied to this connection, so it goes when the connection does.
        web_terminal_io._shutdown_connection(connection)
        self.assertIsNone(store.public_state("pane-b"))

    def test_an_ssh_pane_whose_tunnel_was_refused_is_undeliverable(self):
        self._bind()

        _connection, sent, published = self._run(
            self._session(mode="ssh"), {"kind": "ssh", "shell_kind": "posix"}
        )

        self.assertNotIn(QUOTED, sent[0])
        self.assertIn("SSH tunnel", published[0])

    def test_an_unavailable_directory_starts_no_agent_and_delivers_no_task(self):
        self._bind()
        missing = str(Path(self.temp.name) / "gone")

        _connection, sent, published = self._run(
            self._session(directory=missing),
            {"kind": "local", "shell_kind": "cmd"},
        )

        self.assertFalse(any(QUOTED in line for line in sent))
        self.assertEqual(store.public_state("pane-b")["state"], UNDELIVERABLE)
        self.assertTrue(any("starting directory is not available" in line for line in published))

    def test_a_retired_connection_leaves_the_task_for_the_next_one(self):
        self._bind()
        connection = {"kind": "local", "shell_kind": "cmd", "launch_cwd_applied": True, "retired": True}

        self._run(self._session(), connection)

        self.assertNotIn("handoff_id", connection)
        self.assertEqual(store.public_state("pane-b")["state"], WAITING)
        # The replacement connection announces it.
        _next, sent, _published = self._run(self._session())
        self.assertIn(QUOTED, sent[0])
        self.assertEqual(store.public_state("pane-b")["state"], ANNOUNCED)

    def test_a_relaunched_connection_does_not_replay_an_announced_task(self):
        self._bind()
        first, _sent, _published = self._run(self._session())

        web_terminal_io._shutdown_connection(first)
        _second, sent, _published = self._run(self._session())

        self.assertNotIn(QUOTED, sent[0])
        self.assertEqual(store.count(), 0)

    def test_a_closed_pane_forgets_a_task_no_connection_announced(self):
        self._bind(session_id="closed-pane")

        web_terminal_io._close_ssh_connection("closed-pane")

        self.assertEqual(store.count(), 0)


# ==================== 1b: the relaunch ====================


def _pane_state(session_id):
    return shell_tests._pane_state(session_id)


class RelaunchWithTaskTestCase(shell_tests.ShellTransitionTestCase):
    def setUp(self):
        super().setUp()
        store.reset()
        self.addCleanup(store.reset)

    def _caller(self):
        caller, repo = self._local_pane(repo_name="caller")
        api.session_manager.update_session_metadata(
            caller.session_id, startup_mode="agent", initial_command_mode="agent",
            agent_selection="claude", initial_command="claude", agent_depth=0, title="Claude 1",
        )
        return api.session_manager.get_session(caller.session_id), repo

    def _target(self, caller, repo, **overrides):
        fields = {
            "group_id": caller.group_id, "host": "cmd", "directory": str(repo), "mode": "wsl",
            "startup_mode": "terminal", "created_by_session_id": caller.session_id,
            "title": "Terminal 3",
        }
        fields.update(overrides)
        target = api.session_manager.create_session(**fields)
        api.session_manager.update_session_status(target.session_id, api.SessionStatus.CONNECTED)
        return api.session_manager.get_session(target.session_id)

    def _relaunch(self, target_id, body, *, installed=True, close_for_real=False):
        started = []

        def detect(target, binary):
            registry = {str((spec or {}).get("binary") or key) for key, spec in web_agents.AGENT_REGISTRY.items()}
            if binary in registry and not installed:
                return {"found": False}
            return {"found": True, "path": f"/usr/bin/{binary}"}

        def start(function, session_id):
            started.append(store.public_state(session_id))

        patches = [
            patch.object(api.os, "name", "nt"),
            patch.object(web_terminal_io, "_resolve_live_terminal_cwd", return_value=None),
            patch.object(web_agents, "_detect_agent_binary_cached", side_effect=detect),
            patch.object(api.socketio, "start_background_task", side_effect=start),
        ]
        if not close_for_real:
            patches.append(patch.object(api, "_close_ssh_connection"))
        for patcher in patches:
            patcher.start()
        try:
            response = self.client.post(f"/api/sessions/{target_id}/agent-relaunch", json=body)
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        return response, started

    def _body(self, caller, **overrides):
        body = {"requested_by_session_id": caller.session_id, "agent": "codex", "task": BRIEF}
        body.update(overrides)
        return body

    def test_a_scratch_pane_this_agent_made_is_relaunched_with_its_task(self):
        caller, repo = self._caller()
        target = self._target(caller, repo)

        response, started = self._relaunch(target.session_id, self._body(caller))

        self.assertEqual(response.status_code, 200, response.get_json())
        payload = response.get_json()
        self.assertEqual(payload["agent_selection"], "codex")
        self.assertTrue(payload["agent_mcp"])
        self.assertEqual(payload["handoff"]["state"], WAITING)
        # Bound before the connector started, from the caller.
        self.assertEqual(started, [payload["handoff"]])
        self.assertEqual(store.pending_for(target.session_id).source_session_id, caller.session_id)
        self.assertNotIn(BRIEF, json.dumps(payload))

    def test_a_task_turns_the_tools_on(self):
        caller, repo = self._caller()
        target = self._target(caller, repo)
        body = self._body(caller)
        self.assertNotIn("mcp", body)

        response, _started = self._relaunch(target.session_id, body)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(api.session_manager.get_session(target.session_id).agent_mcp)

    def _assert_refused(self, response, target_id, before, *, status, gate=None, waivable=None):
        self.assertEqual(response.status_code, status, response.get_json())
        payload = response.get_json()
        if gate is not None:
            self.assertEqual(payload["gate"], gate)
            self.assertEqual(payload["waivable"], waivable)
            if not waivable:
                self.assertNotIn("confirm", payload)
        self.assertEqual(_pane_state(target_id), before)
        self.assertEqual(store.count(), 0)
        return payload

    def test_the_readers_own_terminal_asks_first(self):
        caller, repo = self._caller()
        target = self._target(caller, repo, created_by_session_id="")
        before = _pane_state(target.session_id)

        response, started = self._relaunch(target.session_id, self._body(caller))

        payload = self._assert_refused(response, target.session_id, before, status=403, gate="lineage", waivable=True)
        confirm = payload["confirm"]
        self.assertEqual(confirm["pane"]["session_id"], target.session_id)
        self.assertEqual(confirm["pane"]["title"], "Terminal 3")
        self.assertEqual(confirm["pane"]["index"], 1)
        self.assertIsNone(confirm["ends"]["agent"])
        self.assertEqual(
            confirm["question"],
            "Relaunching Terminal 3 ends the shell there and anything running in "
            "it, and starts a new codex with this task. Override Terminal 3?",
        )
        self.assertEqual(started, [])

    def test_a_pane_running_an_agent_asks_first_and_names_what_ends(self):
        caller, repo = self._caller()
        target = self._target(
            caller, repo, startup_mode="agent", initial_command_mode="agent",
            initial_command="codex", agent_selection="codex",
        )
        with api.connection_lock:
            api.ssh_connections[target.session_id] = {
                "agent_activity": note_agent_output(blank_agent_activity(), time.time())
            }
        before = _pane_state(target.session_id)

        response, _started = self._relaunch(target.session_id, self._body(caller))

        payload = self._assert_refused(response, target.session_id, before, status=403, gate="mode", waivable=True)
        self.assertEqual(payload["confirm"]["ends"], {"agent": "codex", "activity": "working"})
        self.assertIn("ends the codex agent there, which GridVibe last read as working, and its conversation",
                      payload["confirm"]["question"])

    def test_override_replaces_either_and_drops_the_old_agents_task(self):
        caller, repo = self._caller()
        target = self._target(
            caller, repo, created_by_session_id="", startup_mode="agent",
            initial_command_mode="agent", initial_command="codex", agent_selection="codex",
        )
        removed = []
        old_id = store.create("old brief", source_session_id="someone", session_id=target.session_id)
        store.announce(old_id, delivery=FILE, task_file="/old", cleanup=lambda: removed.append("old"))
        with api.connection_lock:
            api.ssh_connections[target.session_id] = {"handoff_id": old_id}

        response, started = self._relaunch(
            target.session_id, self._body(caller, override=True), close_for_real=True
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(removed, ["old"])
        pending = store.pending_for(target.session_id)
        self.assertEqual(pending.text, BRIEF)
        self.assertEqual(started[0]["state"], WAITING)

    def test_a_relaunch_without_a_task_never_hands_on_a_waiting_one(self):
        """The brief was for the agent it was handed to, not the next one."""
        caller, repo = self._caller()
        target = self._target(caller, repo)
        store.create("stale brief", source_session_id="someone", session_id=target.session_id)

        response, started = self._relaunch(
            target.session_id, {"requested_by_session_id": caller.session_id, "agent": "codex"}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(started, [None])
        self.assertEqual(store.count(), 0)

    def test_the_header_relaunch_drops_a_waiting_task_too(self):
        caller, repo = self._caller()
        target = self._target(caller, repo)
        store.create("stale brief", source_session_id="someone", session_id=target.session_id)

        response, _close, _start = self._post_shell(target.session_id, {"agent": "claude"})

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(store.count(), 0)

    def test_a_switch_to_an_explorer_drops_a_waiting_task(self):
        caller, repo = self._caller()
        target = self._target(caller, repo)
        store.create("stale brief", source_session_id="someone", session_id=target.session_id)

        with patch.object(web_terminal_io, "_resolve_live_terminal_cwd", return_value=None),                 patch.object(api, "_close_ssh_connection"),                 patch.object(api.socketio, "start_background_task"):
            response = self.client.post(
                f"/api/sessions/{target.session_id}/mode",
                json={"startup_mode": "explorer", "directory": str(repo)},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(store.count(), 0)

    def test_nothing_waives_another_machine(self):
        caller, _repo = self._caller()
        target = self._ssh_pane(created_by_session_id=caller.session_id)
        before = _pane_state(target.session_id)

        for override in (False, True):
            with self.subTest(override=override):
                response, started = self._relaunch(target.session_id, self._body(caller, override=override))
                payload = self._assert_refused(
                    response, target.session_id, before, status=403, gate="machine", waivable=False
                )
                self.assertIn("example.com", payload["error"])
                self.assertEqual(started, [])

    def test_another_machine_is_refused_before_a_waivable_gate(self):
        """Asking the person, then being refused anyway, is the worst order."""
        caller, _repo = self._caller()
        target = self._ssh_pane(created_by_session_id="")
        before = _pane_state(target.session_id)

        response, _started = self._relaunch(target.session_id, self._body(caller))

        self._assert_refused(response, target.session_id, before, status=403, gate="machine", waivable=False)

    def test_without_a_task_another_machine_is_what_it_always_was(self):
        caller, _repo = self._caller()
        target = self._ssh_pane(created_by_session_id=caller.session_id)

        response, _started = self._relaunch(
            target.session_id, {"requested_by_session_id": caller.session_id, "agent": "codex"}
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertNotIn("handoff", response.get_json())

    def test_self_explorer_and_browser_are_refused_with_no_question(self):
        caller, repo = self._caller()
        explorer = self._target(caller, repo, startup_mode="explorer")
        browser = self._target(caller, repo, startup_mode="browser")
        for label, target_id, gate in (
            ("self", caller.session_id, "self"),
            ("explorer", explorer.session_id, "mode"),
            ("browser", browser.session_id, "mode"),
        ):
            for override in (False, True):
                with self.subTest(label, override=override):
                    before = _pane_state(target_id)
                    response, _started = self._relaunch(target_id, self._body(caller, override=override))
                    self._assert_refused(response, target_id, before, status=403, gate=gate, waivable=False)

    def test_a_missing_binary_binds_nothing_and_writes_nothing(self):
        caller, repo = self._caller()
        target = self._target(caller, repo)
        before = _pane_state(target.session_id)

        response, started = self._relaunch(target.session_id, self._body(caller), installed=False)

        self._assert_refused(response, target.session_id, before, status=400)
        self.assertEqual(started, [])

    def test_the_task_is_refused_before_any_gate(self):
        caller, _repo = self._caller()
        cases = {
            "control character": ({"task": "a\x1b[2Jb"}, "U+001B"),
            "no agent": ({"agent": ""}, "name the agent"),
            "incapable agent": ({"agent": "kimi"}, "kimi cannot be handed a task"),
            "mcp false": ({"mcp": False}, "leave 'mcp' out"),
            "too large": ({"task": "a" * (MAX_TASK_BYTES + 1)}, "Nothing was truncated"),
        }
        for label, (overrides, expected) in cases.items():
            with self.subTest(label):
                # Aimed at the caller's own pane: the self gate would refuse,
                # but the task is what is wrong, so the task is what is said.
                before = _pane_state(caller.session_id)
                response, _started = self._relaunch(caller.session_id, self._body(caller, **overrides))
                payload = self._assert_refused(response, caller.session_id, before, status=400)
                self.assertIn(expected, payload["error"])
                self.assertNotIn("gate", payload)


class StructuredRefusalTestCase(shell_tests.ShellTransitionTestCase):
    """The other two gated verbs ask the same way, in their own words."""

    def _pair(self, **target_overrides):
        caller, repo = self._local_pane(repo_name="c")
        api.session_manager.update_session_metadata(
            caller.session_id, startup_mode="agent", initial_command_mode="agent",
            agent_selection="claude", initial_command="claude",
        )
        fields = {"group_id": caller.group_id, "host": "cmd", "directory": str(repo), "mode": "wsl",
                  "startup_mode": "terminal", "title": "Terminal 2",
                  "created_by_session_id": caller.session_id}
        fields.update(target_overrides)
        target = api.session_manager.create_session(**fields)
        return caller, target

    def test_a_mode_switch_on_an_agent_pane_asks_what_it_ends(self):
        caller, target = self._pair(startup_mode="agent", initial_command_mode="agent",
                                    initial_command="codex", agent_selection="codex")

        response = self.client.post(
            f"/api/sessions/{target.session_id}/agent-mode-switch",
            json={"requested_by_session_id": caller.session_id, "startup_mode": "explorer"},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 403)
        self.assertEqual((payload["gate"], payload["waivable"]), ("mode", True))
        self.assertEqual(
            payload["confirm"]["question"],
            "Switching Terminal 2 to a file explorer ends the codex agent there "
            "and its conversation. Override Terminal 2?",
        )

    def test_a_clear_on_the_readers_pane_asks_what_it_erases(self):
        caller, target = self._pair(created_by_session_id="")

        response = self.client.post(
            f"/api/sessions/{target.session_id}/clear",
            json={"requested_by_session_id": caller.session_id},
        )

        payload = response.get_json()
        self.assertEqual(response.status_code, 403)
        self.assertEqual((payload["gate"], payload["waivable"]), ("lineage", True))
        self.assertEqual(
            payload["confirm"]["question"],
            "Clearing Terminal 2 erases its scrollback, which cannot be read back. "
            "Override Terminal 2?",
        )

    def test_the_self_gate_carries_no_question_on_any_verb(self):
        caller, _target = self._pair()
        for path, body in (
            ("clear", {}),
            ("agent-mode-switch", {"startup_mode": "explorer"}),
            ("agent-relaunch", {"agent": "codex"}),
        ):
            with self.subTest(path):
                response = self.client.post(
                    f"/api/sessions/{caller.session_id}/{path}",
                    json={"requested_by_session_id": caller.session_id, **body},
                )
                payload = response.get_json()
                self.assertEqual(response.status_code, 403)
                self.assertEqual((payload["gate"], payload["waivable"]), ("self", False))
                self.assertNotIn("confirm", payload)

    def test_a_malformed_request_carries_no_gate_at_all(self):
        _caller, target = self._pair()

        response = self.client.post(f"/api/sessions/{target.session_id}/clear", json={})

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("gate", response.get_json())


if __name__ == "__main__":
    unittest.main()
