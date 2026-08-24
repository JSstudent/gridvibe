"""L-1: the pane-mode transition transaction, and the boundary it moved behind.

`change_session_mode()` had regrown inside `web/api.py` to ~250 lines owning
validation, cwd/root resolution, presentation cleanup, connection teardown,
metadata mutation, restart, and HTTP mapping at once. The transaction now lives
in `web/session_modes.py`; `web/api.py` keeps request parsing and status
mapping.

The move is characterization-first, and the ~30 `/mode` cases already in
`tests/test_api.py` are most of that characterization: every successful
transition, both explorer-root families, the cwd probe, the widen guard and the
browser tab strip. What they never covered is the half the route only ever
*refuses* — so this file pins the refusal matrix the extraction had to preserve,
and then the two properties the boundary itself has to hold:

- **The refusal paths are atomic.** Each one is asserted on the response *and*
  on the pane: a refused transition leaves the session's metadata, status and
  connection exactly as it found them. That is what makes the extraction
  falsifiable — a mutation that leaked ahead of a validation would show up here
  and nowhere else.
- **The service is not a Flask handler.** It is called directly, with no request
  context, and answers with a plain dict or a `ModeTransitionError`. The route
  maps that error's own status code rather than a fixed 400.
- **The three side effects stay resolvable through `web.api`.** They are passed
  in rather than imported, and the route looks them up in its own body, which is
  why `patch.object(api, "_close_ssh_connection")` still reaches the transition.

`web/session_modes.py` must not import `web.api`: the cycle is the reason the
effects are parameters in the first place.
"""

import ast
import errno
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import api
from tests.test_api import FakeSftp
from web import config as web_config
from web import explorer as web_explorer
from web import runtime_state as web_runtime_state
from web import saved_sessions as web_saved_sessions
from web import session_modes as web_session_modes
from web import terminal_io as web_terminal_io

#: Every field the transition is allowed to touch, so a refusal can be asserted
#: on the whole pane rather than on whichever field the test happened to guess.
_PANE_FIELDS = (
    "host",
    "directory",
    "current_directory",
    "launch_directory",
    "explorer_root_directory",
    "explorer_root_configured",
    "explorer_git_pin_active",
    "explorer_git_pinned_path",
    "initial_command",
    "initial_command_mode",
    "startup_mode",
    "browser_tabs",
    "browser_active_tab",
    "status",
    "username",
    "port",
)


#: Repository root, so the two structural assertions below read the shipped
#: modules rather than whatever the working directory happens to be.
_WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
)


def _module_source(filename):
    """Read one `web/` module's source for a structural assertion."""
    with io.open(os.path.join(_WEB_DIR, filename), encoding="utf-8") as handle:
        return handle.read()


def _pane_state(session_id):
    """Snapshot the pane fields a mode transition can move."""
    session = api.session_manager.get_session(session_id)
    return {
        name: json.loads(json.dumps(getattr(session, name), default=str))
        for name in _PANE_FIELDS
    }


class ModeTransitionTestCase(unittest.TestCase):
    """Shared fixture: a real app client over temporary durable state."""

    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        for module, attribute, filename in (
            (web_config, "CONFIG_PATH", "config.json"),
            (web_saved_sessions, "SAVED_SESSIONS_PATH", "saved_sessions.json"),
            (web_runtime_state, "RUNTIME_STATE_PATH", "runtime_state.json"),
        ):
            patcher = patch.object(
                module, attribute, str(Path(self.temp_dir.name) / filename)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        api._refresh_runtime_config()
        self.addCleanup(api._refresh_runtime_config)
        api.app.config["TESTING"] = True
        self.client = api.app.test_client()
        api.session_manager.reset_sessions()
        self.addCleanup(api.session_manager.reset_sessions)
        with api.connection_lock:
            api.ssh_connections.clear()
            api.session_output_buffers.clear()

    def _local_pane(self, **overrides):
        """One Local Repo terminal pane rooted in a real temporary directory."""
        repo_dir = Path(self.temp_dir.name) / overrides.pop("repo_name", "repo")
        repo_dir.mkdir(exist_ok=True)
        group = api.session_manager.create_group(
            name="Local", connection_mode="wsl", layout="single", terminal_count=1
        )
        fields = {
            "group_id": group.group_id,
            "host": "cmd",
            "directory": str(repo_dir),
            "mode": "wsl",
            "startup_mode": "terminal",
        }
        fields.update(overrides)
        return api.session_manager.create_session(**fields), repo_dir

    def _ssh_pane(self, **overrides):
        """One SSH terminal pane."""
        group = api.session_manager.create_group(
            name="SSH", connection_mode="ssh", layout="single", terminal_count=1
        )
        fields = {
            "group_id": group.group_id,
            "host": "example.com",
            "directory": "/srv/app",
            "mode": "ssh",
            "username": "ubuntu",
            "startup_mode": "terminal",
        }
        fields.update(overrides)
        return api.session_manager.create_session(**fields)

    def _post_mode(self, session_id, body):
        """POST one mode change with every side effect observable."""
        with patch.object(api, "_close_ssh_connection") as close_connection, patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post(
                f"/api/sessions/{session_id}/mode", json=body
            )
        return response, close_connection, start_task


class ModeTransitionRefusalTestCase(ModeTransitionTestCase):
    """The half of the route that only ever refuses, and refuses atomically."""

    def test_an_unknown_session_is_a_404_and_touches_nothing(self):
        response, close_connection, start_task = self._post_mode(
            "no-such-session", {"startup_mode": "explorer"}
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json()["error"], "Session not found")
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_a_pane_of_an_unsupported_kind_is_refused_before_any_work(self):
        """Mode switching is an SSH/Local Repo verb, and says so.

        `mode` is not an updatable metadata field, so the pane is built with the
        unsupported kind rather than moved to it.
        """
        session, _repo = self._local_pane(mode="local")
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_mode(
            session.session_id, {"startup_mode": "explorer"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "only available for SSH and Local Repo sessions",
            response.get_json()["error"],
        )
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_an_unsupported_startup_mode_is_refused_and_names_the_three(self):
        """The body is validated before the pane is touched."""
        session, _repo = self._local_pane()
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_mode(
            session.session_id, {"startup_mode": "agent"}
        )

        self.assertEqual(response.status_code, 400)
        error = response.get_json()["error"]
        for named in ("terminal", "explorer", "browser"):
            self.assertIn(named, error)
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_a_body_that_is_not_json_falls_back_to_the_panes_own_mode(self):
        """An empty body is not an error: `startup_mode` normalizes to the default.

        `_normalize_startup_mode(None, "wsl")` answers "terminal", so a garbage
        body reaches the terminal branch — which, for a pane that is already a
        terminal, is the no-op return rather than a refusal or a restart.
        """
        session, _repo = self._local_pane()
        before = _pane_state(session.session_id)

        with patch.object(api, "_close_ssh_connection") as close_connection, patch.object(
            api.socketio, "start_background_task"
        ) as start_task:
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                data="not json",
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["session_id"], session.session_id)
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_browser_mode_asked_of_an_ssh_pane_normalizes_to_terminal(self):
        """The characterized answer is normalization, not the refusal below it.

        `_normalize_startup_mode(value, session.mode)` only returns "browser"
        for a `wsl` pane, so an SSH pane asking for one is already a "terminal"
        request by the time the branch is chosen — and the browser branch's own
        `session.mode != "wsl"` guard cannot be reached through this route. The
        guard is left standing (it is the service's own invariant, and this
        extraction changes no behaviour), but the pane-visible answer is here:
        an SSH terminal pane asked for browser mode stays exactly as it was.
        """
        session = self._ssh_pane()
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_mode(
            session.session_id, {"startup_mode": "browser"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["startup_mode"], "terminal")
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_a_rejected_browser_url_refuses_before_the_strip_moves(self):
        """`_normalize_browser_url` raising is a 400, not a half-written strip."""
        session, _repo = self._local_pane(
            host="Browser",
            startup_mode="browser",
            initial_command="http://127.0.0.1:3000",
            initial_command_mode="browser",
            browser_tabs=["http://127.0.0.1:3000"],
            browser_active_tab=0,
        )
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_mode(
            session.session_id,
            {"startup_mode": "browser", "url": "ftp://files.example.com/x"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("http", response.get_json()["error"])
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_a_local_explorer_root_that_does_not_exist_is_refused_atomically(self):
        """Validation runs before the metadata write, the close and the restart."""
        session, _repo = self._local_pane()
        before = _pane_state(session.session_id)
        missing = str(Path(self.temp_dir.name) / "gone")

        with patch.object(
            web_terminal_io, "_resolve_live_terminal_cwd", return_value=None
        ):
            response, close_connection, start_task = self._post_mode(
                session.session_id,
                {"startup_mode": "explorer", "directory": missing},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"], "Explorer root directory does not exist"
        )
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_a_remote_explorer_root_that_is_not_a_directory_is_refused_atomically(self):
        """The SFTP branch refuses through the same 400, having mutated nothing."""
        session = self._ssh_pane()
        before = _pane_state(session.session_id)
        fake_sftp = FakeSftp(
            {
                "/srv/app": {"type": "directory"},
                "/srv/app/notes.txt": {"type": "file", "content": b"x"},
            }
        )

        with patch.object(
            web_explorer, "_open_ssh_sftp", return_value=(MagicMock(), fake_sftp)
        ):
            response, close_connection, start_task = self._post_mode(
                session.session_id,
                {"startup_mode": "explorer", "directory": "/srv/app/notes.txt"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"], "Explorer root directory does not exist"
        )
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_an_sftp_transport_failure_is_a_500_and_still_mutates_nothing(self):
        """A broken channel is the route's other refusal status, not a 400."""
        session = self._ssh_pane()
        before = _pane_state(session.session_id)

        class _ExplodingSftp(FakeSftp):
            def normalize(self, path):
                raise OSError(errno.EIO, "channel closed")

        with patch.object(
            web_explorer,
            "_open_ssh_sftp",
            return_value=(MagicMock(), _ExplodingSftp({})),
        ):
            response, close_connection, start_task = self._post_mode(
                session.session_id, {"startup_mode": "explorer"}
            )

        self.assertEqual(response.status_code, 500)
        self.assertTrue(response.get_json()["error"])
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()

    def test_leaving_explorer_mode_refuses_an_unusable_directory_atomically(self):
        """The explorer -> terminal branch validates before it restarts a shell."""
        session, repo_dir = self._local_pane(
            host="File Explorer",
            startup_mode="explorer",
            explorer_root_directory=str(Path(self.temp_dir.name) / "repo"),
        )
        before = _pane_state(session.session_id)

        response, close_connection, start_task = self._post_mode(
            session.session_id,
            {"startup_mode": "terminal", "directory": str(repo_dir / "gone")},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json()["error"], "Selected explorer path is not a directory"
        )
        self.assertEqual(_pane_state(session.session_id), before)
        close_connection.assert_not_called()
        start_task.assert_not_called()


class ModeTransitionBoundaryTestCase(ModeTransitionTestCase):
    """What the extraction itself has to hold, beyond behaviour parity."""

    def test_the_service_answers_without_a_request_context(self):
        """No Flask globals cross the boundary: a plain dict comes back.

        Called directly — no test client, no request context — which is the
        property that would fail immediately if `request` or `jsonify` had
        travelled with the transaction.
        """
        session, repo_dir = self._local_pane()
        calls = []
        effects = web_session_modes.ModeTransitionEffects(
            close_connection=lambda *args, **kwargs: calls.append(("close", args, kwargs)),
            broadcast_status=lambda pane_id: calls.append(("broadcast", pane_id)),
            start_connector=lambda pane_id: calls.append(("start", pane_id)),
        )

        with patch.object(
            web_terminal_io, "_resolve_live_terminal_cwd", return_value=None
        ):
            payload = web_session_modes.apply_pane_mode_change(
                session.session_id,
                {"startup_mode": "explorer", "directory": str(repo_dir)},
                effects,
            )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["startup_mode"], "explorer")
        self.assertEqual(payload["explorer_open_path"], "")
        self.assertEqual(
            calls,
            [
                ("close", (session.session_id,), {"clear_buffer": True}),
                ("broadcast", session.session_id),
            ],
        )

    def test_the_service_refuses_with_its_own_status_rather_than_a_response(self):
        """A refusal is a `ModeTransitionError` carrying the status to answer.

        The 404 rather than a 400 on purpose: a service that always meant "bad
        request" would not need to carry a status at all.
        """
        effects = web_session_modes.ModeTransitionEffects(
            close_connection=MagicMock(),
            broadcast_status=MagicMock(),
            start_connector=MagicMock(),
        )

        with self.assertRaises(web_session_modes.ModeTransitionError) as raised:
            web_session_modes.apply_pane_mode_change(
                "no-such-session", {"startup_mode": "explorer"}, effects
            )

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(raised.exception.message, "Session not found")
        effects.close_connection.assert_not_called()
        effects.broadcast_status.assert_not_called()
        effects.start_connector.assert_not_called()

    def test_the_route_maps_the_services_status_and_not_a_fixed_400(self):
        """The 500 branch proves the route reads `status_code` off the error."""
        session, _repo = self._local_pane()

        def _refuse(*_args, **_kwargs):
            raise web_session_modes.ModeTransitionError("channel closed", 500)

        with patch.object(api, "apply_pane_mode_change", _refuse):
            response = self.client.post(
                f"/api/sessions/{session.session_id}/mode",
                json={"startup_mode": "explorer"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "channel closed"})

    def test_the_route_resolves_its_effects_through_web_api(self):
        """The patch point the existing suite relies on still reaches the service.

        The effects are parameters, so nothing stops them being bound at import
        time — which would silently detach every `patch.object(api, ...)` in
        `tests/test_api.py` from the transition. They are looked up in the route
        body instead, and this is the assertion that says so.
        """
        session, repo_dir = self._local_pane(
            host="File Explorer",
            startup_mode="explorer",
            explorer_root_directory=str(Path(self.temp_dir.name) / "repo"),
        )

        with patch.object(
            web_terminal_io, "_resolve_live_terminal_cwd", return_value=None
        ):
            response, close_connection, start_task = self._post_mode(
                session.session_id,
                {"startup_mode": "terminal", "directory": str(repo_dir)},
            )

        self.assertEqual(response.status_code, 200, response.get_json())
        close_connection.assert_not_called()
        start_task.assert_called_once_with(api._connect_session, session.session_id)
        updated = api.session_manager.get_session(session.session_id)
        self.assertEqual(updated.startup_mode, "terminal")
        self.assertEqual(updated.status, api.SessionStatus.PENDING)

    def test_the_service_never_imports_the_flask_module_it_was_cut_out_of(self):
        """`web.api` imports `web.session_modes`; the reverse would cycle."""
        tree = ast.parse(_module_source("session_modes.py"))

        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        self.assertNotIn("web.api", imported)
        self.assertNotIn("api", imported)
        # And the route half really does depend on the service, so the edge
        # runs one way rather than not existing at all.
        self.assertIs(api.apply_pane_mode_change, web_session_modes.apply_pane_mode_change)

    def test_the_route_is_http_adaptation_and_nothing_else(self):
        """~250 lines became a parse, one call and a status map.

        A line ceiling rather than a shape assertion: the point of the move is
        that the transaction cannot regrow here, and the number is what says
        when it has started to.
        """
        tree = ast.parse(_module_source("api.py"))
        handler = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "change_session_mode"
        )

        self.assertLess(handler.end_lineno - handler.lineno, 40)
        called = {
            node.func.id
            for node in ast.walk(handler)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertIn("apply_pane_mode_change", called)
        self.assertIn("jsonify", called)


class RefreshPaneCwdMoveTestCase(ModeTransitionTestCase):
    """`_refresh_pane_cwd()` moved with the transaction it only ever served."""

    def test_the_probe_helper_is_the_one_the_route_still_re_exports(self):
        self.assertIs(api._refresh_pane_cwd, web_session_modes._refresh_pane_cwd)

    def test_an_agent_pane_is_observed_but_never_probed(self):
        """The characterized rule, asserted where the helper now lives."""
        session, repo_dir = self._local_pane(startup_mode="agent")

        with patch.object(
            web_terminal_io, "_resolve_live_terminal_cwd", return_value=None
        ):
            outcome = web_session_modes._refresh_pane_cwd(
                session.session_id,
                api.session_manager.get_session(session.session_id),
                True,
            )

        self.assertEqual(
            outcome,
            {
                "requested": True,
                "resolved": False,
                "reason": "agent_pane",
                "directory": "",
                "source": web_terminal_io.CWD_SOURCE_LAUNCH,
            },
        )

    def test_an_observed_directory_resolves_without_a_probe(self):
        session, repo_dir = self._local_pane()
        nested = repo_dir / "src"
        nested.mkdir()
        api.session_manager.update_session_metadata(
            session.session_id, current_directory=str(nested)
        )

        outcome = web_session_modes._refresh_pane_cwd(
            session.session_id,
            api.session_manager.get_session(session.session_id),
            False,
        )

        self.assertTrue(outcome["resolved"])
        self.assertEqual(Path(outcome["directory"]), nested)
        self.assertNotEqual(outcome["source"], web_terminal_io.CWD_SOURCE_LAUNCH)


if __name__ == "__main__":  # pragma: no cover - convenience runner
    unittest.main()
