"""A Claude pane's session hook: the reading that survives an in-TUI /resume.

The phase-1 identity is assigned at launch and forgotten the moment the reader
submits ``/resume``, ``/clear`` or ``/fork`` -- correctly, because the picker
may land anywhere. These tests hold the half that brings it back: the
generated settings, the launch line that names them, the real hook script
posting to a real socket, the route's ownership gates, and the whole
``/resume`` -> save -> restore round trip.
"""

import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sessions.manager import SessionManager, SessionStatus, TerminalSession  # noqa: E402
from utils import agent_session_hook as hook_script  # noqa: E402
from web import agent_session_hooks as hooks  # noqa: E402
from web import api, runtime_state  # noqa: E402
from web import terminal_io as terminal  # noqa: E402
from web.agents import _compose_agent_startup_command  # noqa: E402
from web.workspaces import launch_session_group  # noqa: E402

ORIGINAL_ID = "019d2e46-065b-7b22-aa9e-51bb915be2ff"
RESUMED_ID = "3446e7e6-85e9-4006-b788-d18ffa764a4c"
OTHER_ID = "01a041fa-3a1a-71e3-8827-b75ec6aefe6f"
TOKEN = "pane-token-for-tests"


def claude_fields(**updates):
    fields = {
        "host": "local",
        "directory": "",
        "mode": "wsl",
        "startup_mode": "agent",
        "initial_command_mode": "agent",
        "initial_command": "claude",
        "agent_selection": "claude",
        "custom_agent": "",
        "agent_auto_mode": False,
        "agent_mcp": False,
    }
    fields.update(updates)
    return fields


class GeneratedSettingsTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.mkdtemp(prefix="gridvibe-hook-settings-")
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        self.path = os.path.join(directory, "settings.json")

    def test_settings_register_one_exec_form_session_start_hook(self):
        self.assertEqual(
            hooks.write_claude_settings(interpreter="C:/Py/python.exe", path=self.path),
            self.path,
        )
        with open(self.path, encoding="utf-8") as handle:
            document = json.load(handle)

        self.assertEqual(list(document["hooks"]), ["SessionStart"])
        (entry,) = document["hooks"]["SessionStart"]
        # No matcher: startup, resume, clear, compact and fork all name the
        # session the TUI is in afterwards.
        self.assertNotIn("matcher", entry)
        (command,) = entry["hooks"]
        self.assertEqual(command["type"], "command")
        # Exec form: the interpreter is spawned directly, no shell parses it.
        self.assertEqual(command["command"], "C:/Py/python.exe")
        self.assertEqual(command["args"], [hooks.HOOK_SCRIPT, "--provider", "claude"])
        self.assertTrue(os.path.isfile(command["args"][0]))

    def test_only_an_existing_quotable_file_is_offered_to_a_launch(self):
        self.assertEqual(hooks.available_claude_settings_path(self.path), "")
        hooks.write_claude_settings(interpreter="python", path=self.path)
        self.assertEqual(hooks.available_claude_settings_path(self.path), self.path)
        for unsafe in ('C:/a"b.json', "C:/$HOME.json", "C:/%TEMP%.json", "C:/`x`.json"):
            with self.subTest(path=unsafe):
                self.assertEqual(hooks.available_claude_settings_path(unsafe), "")
                self.assertEqual(hooks.claude_settings_fragment(unsafe), "")

    def test_the_script_and_the_server_agree_on_the_wire(self):
        self.assertEqual(hook_script.PANE_TOKEN_VARIABLE, hooks.PANE_TOKEN_VARIABLE)
        self.assertEqual(hook_script.PANE_TOKEN_HEADER, hooks.PANE_TOKEN_HEADER)
        rule = {str(rule) for rule in api.app.url_map.iter_rules()}
        self.assertIn(
            hook_script.REPORT_PATH.replace("{session_id}", "<session_id>"), rule
        )


class LaunchLineTestCase(unittest.TestCase):
    SETTINGS = "C:/GridVibe/.gridvibe_claude_settings.json"

    def session(self, **updates):
        fields = claude_fields(
            agent_conversation_provider="claude",
            agent_conversation_id=ORIGINAL_ID,
            agent_conversation_resume=True,
        )
        fields.update(updates)
        fields.setdefault("session_id", "pane")
        fields.setdefault("group_id", "group")
        return TerminalSession(**fields)

    def test_a_hooked_claude_launch_names_the_settings_once(self):
        command = _compose_agent_startup_command(
            self.session(agent_auto_mode=True),
            session_hook_settings=self.SETTINGS,
        )

        self.assertTrue(command.startswith(f"claude --resume {ORIGINAL_ID} "))
        self.assertEqual(command.count("--settings"), 1)
        self.assertIn(f'--settings "{self.SETTINGS}"', command)
        self.assertIn("--permission-mode auto", command)

    def test_no_settings_without_a_connection_that_can_report(self):
        self.assertEqual(
            _compose_agent_startup_command(self.session()),
            f"claude --resume {ORIGINAL_ID}",
        )

    def test_other_agents_and_custom_commands_are_never_handed_the_hook(self):
        codex = self.session(
            initial_command="codex",
            agent_selection="codex",
            agent_conversation_provider="",
            agent_conversation_id="",
            agent_conversation_resume=False,
        )
        custom = self.session(initial_command="claude --model opus")
        for session in (codex, custom):
            with self.subTest(command=session.initial_command):
                self.assertNotIn(
                    "--settings",
                    _compose_agent_startup_command(
                        session, session_hook_settings=self.SETTINGS
                    ),
                )


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server's naming
        length = int(self.headers.get("Content-Length") or 0)
        self.server.requests.append(
            {
                "path": self.path,
                "token": self.headers.get(hooks.PANE_TOKEN_HEADER),
                "body": json.loads(self.rfile.read(length) or b"null"),
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"accepted": true}')

    def log_message(self, *args):
        pass


class HookScriptTestCase(unittest.TestCase):
    """The real script, run as Claude runs it, against a real socket."""

    def setUp(self):
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
        self.server.requests = []
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def run_hook(self, hook_input, **env_overrides):
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "GRIDVIBE_URL": self.url,
            "GRIDVIBE_SESSION_ID": "pane 1",
            hooks.PANE_TOKEN_VARIABLE: TOKEN,
            # A proxy the user's shell exports must never see a loopback call.
            "HTTP_PROXY": "http://127.0.0.1:9",
        }
        environment.update(env_overrides)
        environment = {key: value for key, value in environment.items() if value is not None}
        return subprocess.run(
            [sys.executable, hooks.HOOK_SCRIPT, "--provider", "claude"],
            input=json.dumps(hook_input),
            capture_output=True,
            text=True,
            env=environment,
            timeout=30,
            cwd=tempfile.gettempdir(),
        )

    def test_a_resume_is_posted_to_the_pane_with_its_token_and_prints_nothing(self):
        result = self.run_hook(
            {"session_id": RESUMED_ID, "source": "resume", "hook_event_name": "SessionStart"}
        )

        self.assertEqual(result.returncode, 0)
        # SessionStart stdout becomes model context; the hook must say nothing.
        self.assertEqual(result.stdout, "")
        self.assertEqual(
            self.server.requests,
            [
                {
                    "path": "/api/sessions/pane%201/agent-conversation",
                    "token": TOKEN,
                    "body": {
                        "provider": "claude",
                        "conversation_id": RESUMED_ID,
                        "source": "resume",
                    },
                }
            ],
        )

    def test_a_subagent_or_a_pane_without_a_route_sends_nothing(self):
        cases = (
            ({"session_id": RESUMED_ID, "agent_id": "sub"}, {}),
            ({"session_id": RESUMED_ID}, {hooks.PANE_TOKEN_VARIABLE: None}),
            ({"session_id": RESUMED_ID}, {"GRIDVIBE_URL": None}),
            ({"source": "resume"}, {}),
        )
        for hook_input, env in cases:
            with self.subTest(hook_input=hook_input, env=env):
                result = self.run_hook(hook_input, **env)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.server.requests, [])

    def test_an_unreachable_gridvibe_costs_the_session_nothing(self):
        self.server.shutdown()
        self.server.server_close()

        result = self.run_hook({"session_id": RESUMED_ID}, GRIDVIBE_URL=self.url)

        self.assertEqual((result.returncode, result.stdout), (0, ""))


class ReportRouteTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager()
        self.registry = {}
        for name, value in (
            ("session_manager", self.manager),
            ("ssh_connections", self.registry),
        ):
            patcher = patch.object(terminal, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = api.app.test_client()
        self.session = self.manager.create_session(
            "group",
            **claude_fields(
                agent_conversation_provider="claude",
                agent_conversation_id=ORIGINAL_ID,
                agent_conversation_resume=True,
            ),
        )
        self.connection = {"conversation_report_token": TOKEN}
        self.registry[self.session.session_id] = self.connection

    def post(self, body, token=TOKEN, session_id=None):
        return self.client.post(
            f"/api/sessions/{session_id or self.session.session_id}/agent-conversation",
            json=body,
            headers={hooks.PANE_TOKEN_HEADER: token} if token is not None else {},
        )

    def identity(self):
        return (
            self.session.agent_conversation_provider,
            self.session.agent_conversation_id,
            self.session.agent_conversation_resume,
        )

    def test_the_owning_connection_replaces_the_identity_without_echoing_it(self):
        response = self.post(
            {"provider": "claude", "conversation_id": RESUMED_ID.upper(), "source": "resume"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"accepted": True})
        self.assertNotIn(RESUMED_ID, response.get_data(as_text=True))
        self.assertEqual(self.identity(), ("claude", RESUMED_ID, True))

    def test_a_started_or_cleared_session_is_known_but_not_yet_restorable(self):
        for source in ("startup", "clear"):
            with self.subTest(source=source):
                response = self.post(
                    {"provider": "claude", "conversation_id": RESUMED_ID, "source": source}
                )
                self.assertEqual(response.status_code, 200)
                # Claude writes the session on its first prompt; resuming it
                # before then would fail, so the snapshot leaves it out.
                self.assertEqual(self.identity(), ("claude", RESUMED_ID, False))
                self.assertEqual(
                    runtime_state._snapshot_session(self.session)["agent_conversation_id"],
                    "",
                )
                self.session.agent_conversation_id = ORIGINAL_ID

    def test_every_refusal_leaves_the_identity_alone(self):
        before = self.identity()
        good = {"provider": "claude", "conversation_id": RESUMED_ID}
        cases = (
            ("no token", good, None, 403),
            ("wrong token", good, "not-it", 403),
            ("subagent", {**good, "agent_id": "sub"}, TOKEN, 400),
            ("not a uuid", {**good, "conversation_id": "latest"}, TOKEN, 400),
            ("unreporting provider", {**good, "provider": "codex"}, TOKEN, 400),
            ("unknown source", {**good, "source": "picker"}, TOKEN, 400),
            ("not an object", ["claude", RESUMED_ID], TOKEN, 400),
        )
        for label, body, token, status in cases:
            with self.subTest(label):
                self.assertEqual(self.post(body, token=token).status_code, status)
                self.assertEqual(self.identity(), before)
        self.assertEqual(
            self.post(good, session_id="missing").status_code, 404
        )

    def test_a_replaced_connection_cannot_relabel_its_replacement(self):
        self.connection["retired"] = True
        self.registry[self.session.session_id] = {"conversation_report_token": "fresh"}

        response = self.post({"provider": "claude", "conversation_id": RESUMED_ID})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.session.agent_conversation_id, ORIGINAL_ID)

    def test_a_pane_that_is_no_longer_claude_takes_nothing(self):
        self.session.agent_selection = "codex"

        response = self.post({"provider": "claude", "conversation_id": RESUMED_ID})

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.session.agent_conversation_id, ORIGINAL_ID)

    def test_a_connection_without_a_token_hears_no_report(self):
        self.registry[self.session.session_id] = {}

        response = self.post({"provider": "claude", "conversation_id": RESUMED_ID})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.session.agent_conversation_id, ORIGINAL_ID)


class ResumeSaveRestoreTestCase(unittest.TestCase):
    """The reported bug: /resume, save, restart -- and the other conversation."""

    def setUp(self):
        self.manager = SessionManager()
        self.registry = {}
        for name, value in (
            ("session_manager", self.manager),
            ("ssh_connections", self.registry),
        ):
            patcher = patch.object(terminal, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("_broadcast_session_status", "_arm_agent_runtime_on_input"):
            patcher = patch.object(terminal, name)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = api.app.test_client()

    def test_a_picked_conversation_is_the_one_restore_resumes(self):
        session = self.manager.create_session(
            "group",
            **claude_fields(
                agent_conversation_provider="claude",
                agent_conversation_id=ORIGINAL_ID,
                agent_conversation_resume=True,
            ),
        )
        connection = {"kind": "local", "conversation_report_token": TOKEN}
        self.registry[session.session_id] = connection

        # The reader submits /resume: the old id may no longer be true, so it
        # is forgotten before the picker is even drawn.
        terminal._track_current_terminal_agent_input(
            session.session_id, connection, "/resume\r"
        )
        self.assertEqual(session.agent_conversation_id, "")

        # They pick a conversation; Claude's SessionStart hook reports it.
        response = self.client.post(
            f"/api/sessions/{session.session_id}/agent-conversation",
            json={"provider": "claude", "conversation_id": RESUMED_ID, "source": "resume"},
            headers={hooks.PANE_TOKEN_HEADER: TOKEN},
        )
        self.assertEqual(response.status_code, 200)

        snapshot = runtime_state._snapshot_session(session)
        self.assertEqual(snapshot["agent_conversation_provider"], "claude")
        self.assertEqual(snapshot["agent_conversation_id"], RESUMED_ID)

        restored_manager = SessionManager()
        from web.app import socketio

        with (
            patch("web.workspaces._manager", return_value=restored_manager),
            patch.object(socketio, "start_background_task"),
            patch("web.terminal_io._broadcast_session_groups_updated"),
            patch("web.terminal_io._broadcast_session_status"),
        ):
            _payload, status = launch_session_group(
                {
                    "sessions": [
                        claude_fields(
                            mode="ssh",
                            agent_conversation_provider=snapshot["agent_conversation_provider"],
                            agent_conversation_id=snapshot["agent_conversation_id"],
                        )
                    ],
                    "connection_mode": "ssh",
                    "workspace_id": "default",
                    "restore": True,
                }
            )

        self.assertEqual(status, 201)
        (restored,) = restored_manager.get_all_sessions()
        self.assertEqual(
            _compose_agent_startup_command(restored),
            f"claude --resume {RESUMED_ID}",
        )

    def test_the_resumed_topic_title_does_not_erase_what_the_hook_reported(self):
        """The regression: a Claude title change is a topic, not a destination.

        After /resume the hook reports the picked session, and then Claude
        retitles the pane with that conversation's topic. Treating that title
        change as "landed somewhere unidentifiable" wiped the reported id, so
        the next save restored a fresh Claude.
        """
        session = self.manager.create_session(
            "group",
            **claude_fields(
                agent_conversation_provider="claude",
                agent_conversation_id=ORIGINAL_ID,
                agent_conversation_resume=True,
            ),
        )
        connection = {
            "kind": "local",
            "conversation_report_token": TOKEN,
            "agent_activity": {"title": "Claude Code", "title_at": 1.0},
        }
        self.registry[session.session_id] = connection
        terminal._note_agent_conversation(
            session.session_id, connection, connection["agent_activity"]
        )

        terminal._track_current_terminal_agent_input(
            session.session_id, connection, "/resume\r"
        )
        self.client.post(
            f"/api/sessions/{session.session_id}/agent-conversation",
            json={"provider": "claude", "conversation_id": RESUMED_ID, "source": "resume"},
            headers={hooks.PANE_TOKEN_HEADER: TOKEN},
        )
        for title, at in (("Claude Code", 2.0), ("Explain dry-run cycle isolation", 3.0)):
            terminal._note_agent_conversation(
                session.session_id, connection, {"title": title, "title_at": at}
            )

        snapshot = runtime_state._snapshot_session(session)
        self.assertEqual(snapshot["agent_conversation_provider"], "claude")
        self.assertEqual(snapshot["agent_conversation_id"], RESUMED_ID)

    def test_without_a_report_the_switch_still_restores_fresh_not_stale(self):
        session = self.manager.create_session(
            "group",
            **claude_fields(
                agent_conversation_provider="claude",
                agent_conversation_id=ORIGINAL_ID,
                agent_conversation_resume=True,
            ),
        )
        connection = {"kind": "local", "conversation_report_token": TOKEN}
        self.registry[session.session_id] = connection

        terminal._track_current_terminal_agent_input(
            session.session_id, connection, "/resume\r"
        )

        snapshot = runtime_state._snapshot_session(session)
        self.assertFalse(snapshot["agent_conversation_id"])
        self.assertNotEqual(snapshot["agent_conversation_id"], ORIGINAL_ID)


class LocalSpawnTokenTestCase(unittest.TestCase):
    """Each native local connection carries its own token; WSL carries none."""

    BASE_ENVIRONMENT = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": "C:\\Windows"}

    def setUp(self):
        self.registry = {}
        for name, value in (
            ("ssh_connections", self.registry),
            ("session_output_buffers", {}),
            ("session_terminal_sizes", {}),
        ):
            patcher = patch.object(terminal, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ("session_manager", "_broadcast_session_status"):
            patcher = patch.object(terminal, name)
            setattr(self, name, patcher.start())
            self.addCleanup(patcher.stop)
        self.session_manager.groups = {}

    def spawn(self, **overrides):
        fields = dict(
            distribution="", username="", directory="", initial_command="",
            use_wsl=False, use_powershell=False, mode="wsl", startup_mode="terminal",
            status=SessionStatus.CONNECTED, group_id="group-1", agent_depth=0,
        )
        fields.update(overrides)
        session = SimpleNamespace(**fields)
        self.session_manager.get_session.return_value = session
        winpty = MagicMock()
        with patch.dict(terminal.os.environ, self.BASE_ENVIRONMENT, clear=True), \
                patch.object(terminal.os, "name", "nt"), \
                patch.object(terminal, "WinPtyProcess", winpty), \
                patch.object(terminal, "_drain_until_prompt"), \
                patch.object(terminal, "_run_startup_sequence"), \
                patch.object(terminal, "_stream_local_output"):
            terminal._connect_local_session("pane-1", session)
        return winpty.spawn.call_args.kwargs["env"], self.registry.get("pane-1") or {}

    def test_a_native_pane_carries_the_token_its_connection_keeps(self):
        first_env, first_connection = self.spawn()
        token = first_env[hooks.PANE_TOKEN_VARIABLE]

        self.assertTrue(token)
        self.assertEqual(first_connection["conversation_report_token"], token)
        second_env, _ = self.spawn()
        self.assertNotEqual(second_env[hooks.PANE_TOKEN_VARIABLE], token)

    def test_a_wsl_pane_is_not_handed_a_route_it_cannot_reach(self):
        environment, connection = self.spawn(use_wsl=True, distribution="Ubuntu")

        self.assertNotIn(hooks.PANE_TOKEN_VARIABLE, environment)
        self.assertNotIn("conversation_report_token", connection)


class StartupSequenceTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager()
        self.registry = {}
        for name, value in (
            ("session_manager", self.manager),
            ("ssh_connections", self.registry),
        ):
            patcher = patch.object(terminal, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.settings = hooks.write_claude_settings(interpreter=sys.executable)
        # Leave no file behind for a later suite's startup sequence to find.
        self.addCleanup(os.remove, self.settings)

    def delivered_line(self, connection):
        session = self.manager.create_session(
            "group",
            **claude_fields(
                agent_conversation_provider="claude",
                agent_conversation_id=ORIGINAL_ID,
                agent_conversation_resume=True,
            ),
        )
        connection.update({"kind": "local", "shell_kind": "posix", "launch_cwd_applied": True})
        self.registry[session.session_id] = connection
        sent = []
        with patch.object(terminal, "_send_connection_input", side_effect=lambda _c, text: sent.append(text)), \
                patch.object(terminal, "_note_agent_conversation_command"):
            terminal._run_startup_sequence(connection, session)
        return "".join(sent)

    def test_only_a_reporting_connection_launches_claude_with_the_hook(self):
        self.assertIn(
            f'--settings "{self.settings}"',
            self.delivered_line({"conversation_report_token": TOKEN}),
        )
        self.assertNotIn("--settings", self.delivered_line({}))


if __name__ == "__main__":
    unittest.main()
