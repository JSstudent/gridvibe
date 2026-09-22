"""Exact Claude/Codex conversation identity across launch and restore."""

import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sessions.manager import SessionManager, TerminalSession  # noqa: E402
from web import agent_conversations as conversations  # noqa: E402
from web import runtime_state, session_shell  # noqa: E402
from web import terminal_io as terminal  # noqa: E402
from web.agent_activity import blank_agent_activity  # noqa: E402
from web.agents import AGENT_REGISTRY, _compose_agent_startup_command  # noqa: E402
from web.saved_sessions import _normalize_terminal_entries  # noqa: E402
from web.workspaces import _restore_group_request, launch_session_group  # noqa: E402

CLAUDE_ID = "019d2e46-065b-7b22-aa9e-51bb915be2ff"
CODEX_ID = "01a041fa-3a1a-71e3-8827-b75ec6aefe6f"


def setUpModule():
    # The feature is experimental and off by default. These cases exercise it,
    # so the module runs with its App Settings switch on;
    # `RestoreSwitchOffTestCase` turns it back off.
    patcher = patch.object(
        conversations.runtime_config, "workspace_agent_conversation_restore", True
    )
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)


def agent_config(provider: str, **updates):
    config = {
        "host": "local",
        "directory": "/srv/app",
        "mode": "ssh",
        "startup_mode": "agent",
        "initial_command_mode": "agent",
        "initial_command": provider,
        "agent_selection": provider,
        "custom_agent": "",
        "agent_auto_mode": False,
        "agent_mcp": False,
    }
    config.update(updates)
    return config


def launch_session(provider: str, **updates):
    config = agent_config(provider, **updates)
    config.setdefault("session_id", "pane")
    config.setdefault("group_id", "group")
    return TerminalSession(**config)


class ProviderPlanningTestCase(unittest.TestCase):
    def test_only_allowlisted_verified_registry_capabilities_are_executable(self):
        self.assertEqual(
            conversations.conversation_restore_capability(AGENT_REGISTRY, "claude")[
                "strategy"
            ],
            "assigned_uuid",
        )
        self.assertEqual(
            conversations.conversation_restore_capability(AGENT_REGISTRY, "codex")[
                "strategy"
            ],
            "osc_uuid",
        )
        altered = {
            "claude": {
                "conversation_restore": {
                    "strategy": "shell_template",
                    "create_style": "anything",
                    "resume_style": "anything",
                }
            }
        }
        self.assertEqual(
            conversations.conversation_restore_capability(altered, "claude"), {}
        )

    def test_a_fresh_claude_launch_gets_a_distinct_assigned_uuid(self):
        first = conversations.prepare_conversation_launch_fields(
            agent_config("claude"), AGENT_REGISTRY
        )
        second = conversations.prepare_conversation_launch_fields(
            agent_config("claude"), AGENT_REGISTRY
        )

        self.assertEqual(first["agent_conversation_provider"], "claude")
        self.assertEqual(str(uuid.UUID(first["agent_conversation_id"])), first["agent_conversation_id"])
        self.assertNotEqual(first["agent_conversation_id"], second["agent_conversation_id"])
        self.assertFalse(first["agent_conversation_resume"])

    def test_claude_create_and_resume_use_the_same_final_composer(self):
        creating = launch_session(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=False,
        )
        resuming = launch_session(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=True,
        )

        self.assertEqual(
            _compose_agent_startup_command(creating),
            f"claude --session-id {CLAUDE_ID}",
        )
        self.assertEqual(
            _compose_agent_startup_command(resuming),
            f"claude --resume {CLAUDE_ID}",
        )

    def test_codex_resume_keeps_title_and_auto_flags_exactly_once(self):
        session = launch_session(
            "codex",
            agent_auto_mode=True,
            agent_conversation_provider="codex",
            agent_conversation_id=CODEX_ID,
            agent_conversation_resume=True,
        )

        command = _compose_agent_startup_command(session)

        self.assertTrue(command.startswith(f"codex resume {CODEX_ID} "))
        self.assertEqual(command.count("tui.terminal_title"), 1)
        self.assertEqual(command.count("--sandbox workspace-write"), 1)

    def test_custom_and_modified_commands_remain_verbatim(self):
        custom = launch_session(
            "claude",
            initial_command=f"claude --resume {CLAUDE_ID}",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=True,
        )
        self.assertEqual(
            _compose_agent_startup_command(custom),
            f"claude --resume {CLAUDE_ID}",
        )

    def test_final_composer_refuses_a_provider_mismatch(self):
        mismatched = launch_session(
            "claude",
            agent_conversation_provider="codex",
            agent_conversation_id=CODEX_ID,
            agent_conversation_resume=True,
        )

        self.assertEqual(_compose_agent_startup_command(mismatched), "claude")

    def test_terminal_commands_are_used_only_when_they_name_an_exact_resume(self):
        self.assertEqual(
            conversations.command_conversation_identity(
                f"claude --resume {CLAUDE_ID}"
            ),
            ("claude", CLAUDE_ID),
        )
        self.assertEqual(
            conversations.command_conversation_identity(f"codex resume {CODEX_ID}"),
            ("codex", CODEX_ID),
        )
        self.assertEqual(
            conversations.command_conversation_identity(
                f"claude --session-id={CLAUDE_ID}"
            ),
            ("claude", CLAUDE_ID),
        )
        for command in ("claude", "codex", "claude --continue", "codex resume --last"):
            with self.subTest(command=command):
                self.assertEqual(
                    conversations.command_conversation_identity(command), ("", "")
                )


class PersistenceTestCase(unittest.TestCase):
    def test_public_session_payload_hides_identity_but_runtime_snapshot_carries_it(self):
        session = launch_session(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=True,
        )

        self.assertNotIn("agent_conversation_id", session.to_dict())
        durable = session.to_dict(include_conversation=True)
        self.assertEqual(durable["agent_conversation_id"], CLAUDE_ID)
        snapshot = runtime_state._snapshot_session(session)
        self.assertEqual(snapshot["agent_conversation_provider"], "claude")
        self.assertEqual(snapshot["agent_conversation_id"], CLAUDE_ID)
        self.assertNotIn("agent_conversation_resume", snapshot)

    def test_restore_marks_a_valid_pair_resume_only(self):
        snapshot = agent_config(
            "codex",
            agent_conversation_provider="codex",
            agent_conversation_id=CODEX_ID,
        )
        body, warning = _restore_group_request(
            {
                "sessions": [snapshot],
                "connection_mode": "ssh",
                "layout": "single",
                "name": "Agents",
            },
            "default",
        )

        self.assertEqual(warning, "")
        self.assertTrue(body["restore"])
        self.assertTrue(body["sessions"][0]["agent_conversation_resume"])

    def test_old_snapshots_still_validate_as_fresh_agents(self):
        validated = runtime_state._validate_session(agent_config("claude"))

        self.assertIsNotNone(validated)
        self.assertEqual(validated["agent_conversation_provider"], "")
        self.assertEqual(validated["agent_conversation_id"], "")

    def test_malformed_incomplete_and_provider_mismatched_pairs_are_rejected(self):
        invalid = (
            {"agent_conversation_provider": "claude"},
            {"agent_conversation_id": CLAUDE_ID},
            {
                "agent_conversation_provider": "claude",
                "agent_conversation_id": "not-a-uuid",
            },
            {
                "agent_conversation_provider": 7,
                "agent_conversation_id": CLAUDE_ID,
            },
            {
                "agent_conversation_provider": "codex",
                "agent_conversation_id": CODEX_ID,
            },
        )
        for fields in invalid:
            with self.subTest(fields=fields):
                self.assertIsNone(
                    runtime_state._validate_session(agent_config("claude", **fields))
                )

    def test_a_typed_exact_resume_is_a_valid_durable_shape(self):
        validated = runtime_state._validate_session(
            agent_config(
                "claude",
                initial_command=f"claude --resume {CLAUDE_ID}",
                agent_conversation_provider="claude",
                agent_conversation_id=CLAUDE_ID,
            )
        )

        self.assertIsNotNone(validated)
        self.assertEqual(validated["agent_conversation_id"], CLAUDE_ID)

    def test_reusable_preset_normalization_strips_conversation_identity(self):
        normalized = _normalize_terminal_entries(
            [
                agent_config(
                    "claude",
                    agent_conversation_provider="claude",
                    agent_conversation_id=CLAUDE_ID,
                    agent_conversation_resume=True,
                )
            ],
            minimum_count=1,
        )[0]

        self.assertNotIn("agent_conversation_provider", normalized)
        self.assertNotIn("agent_conversation_id", normalized)
        self.assertNotIn("agent_conversation_resume", normalized)


class LauncherAndResetIntegrationTestCase(unittest.TestCase):
    def test_launcher_assigns_distinct_claude_ids_without_returning_them(self):
        manager = SessionManager()
        from web.app import socketio

        def launch():
            with (
                patch("web.workspaces._manager", return_value=manager),
                patch("web.agents._sanitize_agent_launch_commands", return_value=[]),
                patch.object(socketio, "start_background_task"),
                patch("web.terminal_io._broadcast_session_groups_updated"),
                patch("web.terminal_io._broadcast_session_status"),
            ):
                return launch_session_group(
                    {
                        "sessions": [agent_config("claude")],
                        "connection_mode": "ssh",
                        "workspace_id": "default",
                    }
                )

        first_payload, first_status = launch()
        second_payload, second_status = launch()
        sessions = manager.get_all_sessions()

        self.assertEqual((first_status, second_status), (201, 201))
        self.assertEqual(len(sessions), 2)
        self.assertNotEqual(
            sessions[0].agent_conversation_id,
            sessions[1].agent_conversation_id,
        )
        self.assertNotIn("agent_conversation_id", first_payload["sessions"][0])
        self.assertNotIn("agent_conversation_id", second_payload["sessions"][0])

    def test_server_restore_launches_the_exact_claude_conversation(self):
        manager = SessionManager()
        from web.app import socketio

        with (
            patch("web.workspaces._manager", return_value=manager),
            patch.object(socketio, "start_background_task"),
            patch("web.terminal_io._broadcast_session_groups_updated"),
            patch("web.terminal_io._broadcast_session_status"),
        ):
            payload, status = launch_session_group(
                {
                    "sessions": [
                        agent_config(
                            "claude",
                            agent_conversation_provider="claude",
                            agent_conversation_id=CLAUDE_ID,
                        )
                    ],
                    "connection_mode": "ssh",
                    "workspace_id": "default",
                    "restore": True,
                }
            )

        session = manager.get_all_sessions()[0]
        self.assertEqual(status, 201)
        self.assertTrue(session.agent_conversation_resume)
        self.assertEqual(
            _compose_agent_startup_command(session),
            f"claude --resume {CLAUDE_ID}",
        )
        self.assertNotIn("agent_conversation_id", payload["sessions"][0])

    def test_terminal_reset_always_starts_a_fresh_conversation(self):
        manager = SessionManager()
        session = manager.create_session(
            "group",
            **agent_config(
                "claude",
                agent_conversation_provider="claude",
                agent_conversation_id=CLAUDE_ID,
                agent_conversation_resume=True,
            ),
        )
        effects = session_shell.ShellTransitionEffects(
            close_connection=Mock(),
            broadcast_status=Mock(),
            start_connector=Mock(),
        )

        with (
            patch.object(session_shell, "session_manager", manager),
            patch.object(session_shell, "_refuse_an_agent_that_is_not_installed"),
        ):
            # The same agent from the dropdown is a new process and a new
            # conversation: a fresh assigned id in create mode, never a resume.
            session_shell.apply_pane_shell_change(
                session.session_id, {"agent": "claude"}, effects
            )
            fresh_id = session.agent_conversation_id
            self.assertEqual(session.agent_conversation_provider, "claude")
            self.assertNotEqual(fresh_id, CLAUDE_ID)
            self.assertEqual(str(uuid.UUID(fresh_id)), fresh_id)
            self.assertFalse(session.agent_conversation_resume)
            self.assertEqual(
                _compose_agent_startup_command(session),
                f"claude --session-id {fresh_id}",
            )

            session_shell.apply_pane_shell_change(
                session.session_id, {"agent": "codex"}, effects
            )
            self.assertEqual(session.agent_conversation_provider, "")
            self.assertEqual(session.agent_conversation_id, "")
            self.assertFalse(session.agent_conversation_resume)

            session.agent_conversation_provider = "codex"
            session.agent_conversation_id = CODEX_ID
            session.agent_conversation_resume = True
            session_shell.apply_pane_shell_change(
                session.session_id, {"agent": "codex"}, effects
            )

        self.assertEqual(session.agent_conversation_id, "")
        self.assertEqual(_compose_agent_startup_command(session).split()[0:2], ["codex", "-c"])
        self.assertEqual(effects.start_connector.call_count, 3)


class OwnershipAndLifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.manager = SessionManager()
        self.registry = {}
        manager_patch = patch.object(terminal, "session_manager", self.manager)
        registry_patch = patch.object(terminal, "ssh_connections", self.registry)
        manager_patch.start()
        registry_patch.start()
        self.addCleanup(manager_patch.stop)
        self.addCleanup(registry_patch.stop)

    def add_session(self, provider: str, **updates):
        fields = agent_config(provider, **updates)
        fields.pop("session_id", None)
        fields.pop("group_id", None)
        return self.manager.create_session("group", **fields)

    def test_a_retired_connection_cannot_publish_to_its_replacement(self):
        session = self.add_session("codex")
        old = {"agent_activity": blank_agent_activity()}
        replacement = {"agent_activity": blank_agent_activity()}
        self.registry[session.session_id] = old

        self.assertTrue(
            terminal._publish_runtime_conversation_identity(
                session.session_id, old, "codex", CODEX_ID
            )
        )
        self.registry[session.session_id] = replacement
        old["retired"] = True

        self.assertFalse(
            terminal._publish_runtime_conversation_identity(
                session.session_id, old, "codex", CLAUDE_ID
            )
        )
        self.assertEqual(session.agent_conversation_id, CODEX_ID)

    def test_the_first_prompt_makes_an_id_resumable_but_a_retired_pump_cannot(self):
        session = self.add_session(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=False,
        )
        current = {}
        self.registry[session.session_id] = current

        with patch.object(terminal, "_arm_agent_runtime_on_input"):
            # A slash command saves no turn, so nothing exists to resume yet.
            terminal._track_current_terminal_agent_input(
                session.session_id, current, "/model\r"
            )
            self.assertFalse(session.agent_conversation_resume)
            self.assertEqual(session.to_dict(include_conversation=True)["agent_conversation_id"], "")

            terminal._track_current_terminal_agent_input(
                session.session_id, current, "explain this repo\r"
            )
        self.assertTrue(session.agent_conversation_resume)
        self.assertEqual(
            session.to_dict(include_conversation=True)["agent_conversation_id"], CLAUDE_ID
        )

        session.agent_conversation_resume = False
        replacement = {}
        self.registry[session.session_id] = replacement
        current["retired"] = True
        self.assertFalse(
            terminal._mark_agent_conversation_saved(session.session_id, current)
        )
        self.assertFalse(session.agent_conversation_resume)

    def test_a_codex_thread_announced_at_launch_is_not_restorable_until_a_prompt(self):
        session = self.add_session("codex")
        connection = {"agent_activity": blank_agent_activity()}
        self.registry[session.session_id] = connection

        with patch.object(terminal, "_start_conversation_resolver", return_value=True):
            terminal._note_agent_conversation(
                session.session_id, connection, {"title": CODEX_ID, "title_at": 1.0}
            )

        # Codex announces the thread before it writes it: `codex resume` of
        # this id fails with "No saved session found", so it is not saved.
        self.assertEqual(session.agent_conversation_id, CODEX_ID)
        self.assertFalse(session.agent_conversation_resume)
        self.assertEqual(
            runtime_state._snapshot_session(session)["agent_conversation_id"], ""
        )

        self.assertTrue(
            terminal._mark_agent_conversation_saved(session.session_id, connection)
        )
        self.assertEqual(
            runtime_state._snapshot_session(session)["agent_conversation_id"], CODEX_ID
        )

    def test_a_codex_resume_pick_is_saved_and_a_new_thread_is_not(self):
        session = self.add_session(
            "codex",
            agent_conversation_provider="codex",
            agent_conversation_id=CODEX_ID,
            agent_conversation_resume=True,
        )
        connection = {"agent_activity": blank_agent_activity()}
        self.registry[session.session_id] = connection
        picked = "01a0ca0b-ed8f-7eb0-ab8c-d96a87945cf4"
        new = "01a0ca33-9b21-7b71-803c-d33fdb0b13b0"

        with patch.object(terminal, "_start_conversation_resolver", return_value=True):
            terminal._note_agent_conversation_switch(
                session.session_id, connection, session, "/resume"
            )
            terminal._note_agent_conversation(
                session.session_id, connection, {"title": picked, "title_at": 2.0}
            )
            self.assertEqual(
                (session.agent_conversation_id, session.agent_conversation_resume),
                (picked, True),
            )

            terminal._note_agent_conversation_switch(
                session.session_id, connection, session, "/new"
            )
            terminal._note_agent_conversation(
                session.session_id, connection, {"title": new, "title_at": 3.0}
            )

        self.assertEqual(
            (session.agent_conversation_id, session.agent_conversation_resume),
            (new, False),
        )

    def test_a_restored_codex_resume_stays_resumable_when_it_announces_itself(self):
        session = self.add_session(
            "codex",
            agent_conversation_provider="codex",
            agent_conversation_id=CODEX_ID,
            agent_conversation_resume=True,
        )
        connection = {"agent_activity": blank_agent_activity()}
        self.registry[session.session_id] = connection

        with patch.object(terminal, "_start_conversation_resolver", return_value=True):
            terminal._note_agent_conversation_command(
                session.session_id, connection, f"codex resume {CODEX_ID}"
            )
            terminal._note_agent_conversation(
                session.session_id, connection, {"title": CODEX_ID, "title_at": 1.0}
            )

        self.assertTrue(session.agent_conversation_resume)

    def test_failed_startup_command_delivery_keeps_create_intent(self):
        session = self.add_session(
            "claude",
            directory="",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=False,
        )
        connection = {
            "kind": "local",
            "shell_kind": "posix",
            "launch_cwd_applied": True,
        }
        self.registry[session.session_id] = connection

        with patch.object(
            terminal, "_send_connection_input", side_effect=OSError("closed")
        ):
            with self.assertRaises(OSError):
                terminal._run_startup_sequence(connection, session)

        self.assertFalse(session.agent_conversation_resume)

    def test_provider_switch_commands_clear_identity_conservatively(self):
        session = self.add_session(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=True,
        )
        connection = {"kind": "local"}
        self.registry[session.session_id] = connection

        terminal._note_agent_conversation_switch(
            session.session_id, connection, session, "/clear"
        )

        self.assertEqual(session.agent_conversation_provider, "")
        self.assertEqual(session.agent_conversation_id, "")
        self.assertFalse(session.agent_conversation_resume)

    def test_agent_exit_clears_identity_with_the_rest_of_agent_ownership(self):
        session = self.add_session(
            "codex",
            agent_conversation_provider="codex",
            agent_conversation_id=CODEX_ID,
            agent_conversation_resume=True,
        )
        connection = {}
        self.registry[session.session_id] = connection

        with patch.object(terminal, "_broadcast_session_status"):
            self.assertTrue(terminal._mark_runtime_agent_exited(session.session_id, "test"))

        self.assertEqual(session.startup_mode, "terminal")
        self.assertEqual(session.agent_conversation_provider, "")
        self.assertEqual(session.agent_conversation_id, "")
        self.assertFalse(session.agent_conversation_resume)

    def test_a_human_title_after_a_codex_uuid_does_not_erase_identity(self):
        session = self.add_session("codex")
        connection = {"agent_activity": blank_agent_activity()}
        self.registry[session.session_id] = connection

        with patch.object(terminal, "_start_conversation_resolver", return_value=True):
            terminal._note_agent_conversation(
                session.session_id,
                connection,
                {"title": CODEX_ID, "title_at": 1.0},
            )
            terminal._note_agent_conversation(
                session.session_id,
                connection,
                {"title": "Review parser", "title_at": 2.0},
            )

        self.assertEqual(session.agent_conversation_provider, "codex")
        self.assertEqual(session.agent_conversation_id, CODEX_ID)

    def test_a_relaunch_plan_never_carries_the_previous_identity(self):
        claude = agent_config(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
        )
        codex = agent_config(
            "codex",
            agent_conversation_provider="codex",
            agent_conversation_id=CODEX_ID,
        )

        same = conversations.fresh_conversation_fields(claude, AGENT_REGISTRY)
        other = conversations.fresh_conversation_fields(codex, AGENT_REGISTRY)

        self.assertEqual(same["agent_conversation_provider"], "claude")
        self.assertNotEqual(same["agent_conversation_id"], CLAUDE_ID)
        self.assertFalse(same["agent_conversation_resume"])
        self.assertEqual(other, conversations.EMPTY_CONVERSATION_FIELDS)



class RestoreSwitchOffTestCase(unittest.TestCase):
    """With the App Settings switch off, the whole feature is absent."""

    def setUp(self):
        patcher = patch.object(
            conversations.runtime_config, "workspace_agent_conversation_restore", False
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.manager = SessionManager()
        self.registry = {}
        for name, value in (
            ("session_manager", self.manager),
            ("ssh_connections", self.registry),
        ):
            patcher = patch.object(terminal, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_switch_defaults_off(self):
        from web.config import _build_runtime_state

        self.assertFalse(
            conversations.conversation_restore_enabled(_build_runtime_state({}))
        )
        self.assertTrue(
            conversations.conversation_restore_enabled(
                _build_runtime_state(
                    {"workspace": {"agent_conversation_restore": True}}
                )
            )
        )

    def test_no_launch_plans_or_composes_an_identity(self):
        self.assertEqual(
            conversations.prepare_conversation_launch_fields(
                agent_config("claude"), AGENT_REGISTRY
            ),
            conversations.EMPTY_CONVERSATION_FIELDS,
        )
        restored = agent_config(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
        )
        self.assertEqual(
            conversations.prepare_conversation_launch_fields(
                restored, AGENT_REGISTRY, restore=True
            ),
            conversations.EMPTY_CONVERSATION_FIELDS,
        )
        # A pane planned while the switch was on launches bare once it is off.
        session = launch_session(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=True,
        )
        self.assertEqual(_compose_agent_startup_command(session, "claude"), "claude")

    def test_no_identity_is_captured_and_a_carried_one_restores_fresh(self):
        session = launch_session(
            "claude",
            agent_conversation_provider="claude",
            agent_conversation_id=CLAUDE_ID,
            agent_conversation_resume=True,
        )
        snapshot = runtime_state._snapshot_session(session)
        self.assertEqual(snapshot["agent_conversation_provider"], "")
        self.assertEqual(snapshot["agent_conversation_id"], "")

        # A snapshot written while the switch was on -- even a pair that would
        # be refused -- keeps its pane, only without the conversation.
        for fields in (
            {"agent_conversation_provider": "claude", "agent_conversation_id": CLAUDE_ID},
            {"agent_conversation_provider": "claude", "agent_conversation_id": "bad"},
        ):
            with self.subTest(fields=fields):
                validated = runtime_state._validate_session(
                    agent_config("claude", **fields)
                )
                self.assertIsNotNone(validated)
                self.assertEqual(validated["agent_conversation_provider"], "")
                self.assertEqual(validated["agent_conversation_id"], "")

    def test_nothing_is_observed_but_ownership_is_still_answered(self):
        session = self.manager.create_session(
            "group",
            **{
                key: value
                for key, value in agent_config("codex").items()
                if key not in {"session_id", "group_id"}
            },
        )
        connection = {"agent_activity": blank_agent_activity()}
        self.registry[session.session_id] = connection

        # The Codex name resolver still reads the ownership answer.
        self.assertTrue(
            terminal._publish_runtime_conversation_identity(
                session.session_id, connection, "codex", CODEX_ID, saved=True
            )
        )
        self.assertEqual(session.agent_conversation_id, "")
        session.agent_conversation_provider = "codex"
        session.agent_conversation_id = CODEX_ID
        self.assertFalse(
            terminal._mark_agent_conversation_saved(session.session_id, connection)
        )
        self.assertFalse(session.agent_conversation_resume)


if __name__ == "__main__":
    unittest.main()
