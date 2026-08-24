"""The pane-mode transition transaction: terminal <-> explorer <-> browser.

Extracted from `web/api.py` (L-1 of the 2026-08-24 pre-merge plan), where
`change_session_mode()` had regrown to ~250 lines owning validation, working
directory and explorer-root resolution, presentation cleanup, connection
teardown, metadata mutation, shell restart, and HTTP response mapping all at
once. The move is behaviour-neutral by construction: the transaction below is
the route's former body, and `web/api.py` keeps request parsing and status
mapping.

Nothing here imports `web.api` -- that would cycle, and the service has no
business knowing about Flask. The three effects it cannot own (closing the
pane's connection, broadcasting its status, starting the replacement shell)
arrive as `ModeTransitionEffects`, resolved by the route from the module the
tests already patch.

The route's *other* half stays where it is: shell switching, the explorer
backends and workspace orchestration are separate transactions in their own
canonical modules, and this module calls into them exactly as the route did.
"""

import os
from dataclasses import dataclass
from typing import Any, Callable, Dict

from sessions.manager import SessionStatus
from web.app import session_manager
from web.explorer import (
    _acquire_ssh_sftp,
    _configured_explorer_root_directory,
    _explorer_cwd_repo_root,
    _is_browser_session,
    _is_explorer_session,
    _local_path_inside,
    _LocalExplorerBackend,
    _relative_explorer_path,
    _relative_remote_explorer_path,
    _release_ssh_sftp,
    _remote_is_directory,
    _remote_path_clean,
    _remote_path_inside,
    _resolve_explorer_open_root,
    _resolve_pane_terminal_directory,
    _sftp_request_error_types,
    _SftpExplorerBackend,
)
from web.saved_sessions import _normalize_startup_mode
from web.session_presentation import DEFAULT_BROWSER_URL, _normalize_browser_url
from web.terminal_io import (
    CWD_SOURCE_LAUNCH,
    _local_shell_display_name,
    effective_directory,
)


class ModeTransitionError(Exception):
    """One pane-mode transition refused, with the status the route answers.

    Narrow on purpose: the route maps `message` and `status_code` onto an
    error response and nothing else, so the service never has to know what a
    Flask response looks like to refuse a transition.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ModeTransitionEffects:
    """The three side effects the transition cannot own itself.

    Passed in rather than imported because they belong to the Socket.IO server
    and the connection registry that `web/api.py` holds, and importing that
    module here would cycle. Resolving them in the route body also keeps them
    exactly as patchable as they were before the extraction.
    """

    close_connection: Callable[..., Any]
    broadcast_status: Callable[[str], Any]
    start_connector: Callable[[str], Any]


def _refresh_pane_cwd(session_id: str, session: Any, requested: bool) -> Dict[str, Any]:
    """Ask a live terminal where it is, and report whether it answered.

    `effective_directory()` owns the order -- the shell-integration observation,
    then the OS's own read of the pane's shell process, then the marker probe as
    a last resort. Falling through to the launch directory is still an answer,
    but it is an *assumed* one, and saying so is what stops the same gesture
    opening two different roots on two different days.

    An agent pane is still never probed: the probe types a command into the
    pane's shell, and behind a running agent there is no prompt to type it at.
    It is observed like any other pane, though, so a pane that reported its
    directory before the agent started answers without a write.

    ``requested`` gates the *probe*, not the question. Reading an observation
    the pane already produced costs nothing and writes nothing, so a caller
    that did not ask for a refresh still gets one rather than falling back to
    an assumption it had no reason to prefer; only ``requested`` outcomes are
    reported back to the client.
    """
    outcome: Dict[str, Any] = {
        "requested": requested,
        "resolved": False,
        "reason": "",
        "directory": "",
        "source": "",
    }

    directory, source = effective_directory(session_id, session, allow_probe=requested)
    outcome["source"] = source
    if source == CWD_SOURCE_LAUNCH:
        outcome["reason"] = (
            "agent_pane"
            if str(getattr(session, "startup_mode", "") or "") == "agent"
            else "probe_failed"
        )
        return outcome

    outcome["resolved"] = True
    outcome["directory"] = directory
    return outcome


def apply_pane_mode_change(
    session_id: str,
    data: Dict[str, Any],
    effects: "ModeTransitionEffects",
) -> Dict[str, Any]:
    """Switch one pane between terminal, file explorer, and browser modes.

    The whole transition: validate, resolve the pane's directory and explorer
    root, calculate the metadata and presentation changes, and only then close
    the old connection and start the replacement. Returns the payload the route
    serializes, or raises `ModeTransitionError` carrying the status the route
    answers with -- no Flask globals and no `jsonify` cross this boundary.

    The three side effects arrive in `effects` rather than being imported here,
    because `web/api.py` is what owns the Socket.IO server and the connection
    registry the route already reaches through.
    """
    session = session_manager.get_session(session_id)
    if not session:
        raise ModeTransitionError("Session not found", 404)

    if session.mode not in {"ssh", "wsl"}:
        raise ModeTransitionError(
            "Pane mode switching is only available for SSH and Local Repo sessions", 400
        )

    target_mode = _normalize_startup_mode(data.get("startup_mode"), session.mode)
    if target_mode not in {"terminal", "explorer", "browser"}:
        raise ModeTransitionError(
            "startup_mode must be 'terminal', 'explorer', or 'browser'", 400
        )

    if target_mode == "browser":
        if session.mode != "wsl":
            raise ModeTransitionError(
                "Browser mode is only available for Local Repo sessions", 400
            )
        # Mode transitions only. A live pane's tab strip is presentation state
        # and belongs to the ordered, revisioned `/api/session-presentation`
        # transaction — this route used to accept a whole strip as well, which
        # made it a second, unordered writer for the same field.
        try:
            requested_browser_url = data.get("url") or data.get("initial_command")
            browser_url = (
                _normalize_browser_url(requested_browser_url)
                if requested_browser_url
                else None
            )
            browser_snapshot = session_manager.merge_browser_tabs(
                session_id,
                browser_url=browser_url,
                browser_active_tab=data.get("active_tab"),
                default_browser_url=DEFAULT_BROWSER_URL,
            )
            if browser_snapshot is None:
                raise ModeTransitionError("Session not found", 404)
        except ValueError as exc:
            raise ModeTransitionError(str(exc), 400) from exc

        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        effects.close_connection(session_id, clear_buffer=True)
        effects.broadcast_status(session_id)
        return browser_snapshot

    if target_mode == "explorer":
        requested_directory = data.get("directory")
        cwd_probe = _refresh_pane_cwd(session_id, session, bool(data.get("refresh_cwd")))
        if cwd_probe["directory"]:
            requested_directory = cwd_probe["directory"]
        # The widen-guard floor is where the pane was *built*, never
        # `session.directory` -- this switch rewrites that on its way out, so
        # one round trip through explorer mode would leave the floor sitting at
        # the subdirectory the pane last showed and the explorer could never
        # follow the shell back up again.
        launch_directory = session.launch_directory or session.directory
        next_directory = session.directory
        root_directory = ""
        open_path = ""

        if session.mode == "ssh":
            if requested_directory:
                next_directory = _remote_path_clean(requested_directory)
            next_directory = _remote_path_clean(next_directory or "/")
            configured_root = _remote_path_clean(_configured_explorer_root_directory(session))
            client = None
            sftp = None
            try:
                client, sftp = _acquire_ssh_sftp(session)
                next_directory = sftp.normalize(next_directory)
                if not _remote_is_directory(sftp, next_directory):
                    raise ValueError("Explorer root directory does not exist")
                if configured_root:
                    try:
                        configured_root = sftp.normalize(configured_root)
                        if not _remote_is_directory(sftp, configured_root):
                            configured_root = ""
                    except OSError:
                        configured_root = ""
                repo_root = None
                if not (configured_root and _remote_path_inside(configured_root, next_directory)):
                    repo_root = _explorer_cwd_repo_root(
                        _SftpExplorerBackend(session, client, sftp), next_directory
                    )
                root_directory = _resolve_explorer_open_root(
                    configured_root,
                    next_directory,
                    _remote_path_clean(launch_directory or ""),
                    repo_root,
                    contains=_remote_path_inside,
                )
                open_path = _relative_remote_explorer_path(root_directory, next_directory)
            except ValueError as exc:
                raise ModeTransitionError(str(exc), 400) from exc
            except _sftp_request_error_types() as exc:
                raise ModeTransitionError(str(exc), 500) from exc
            finally:
                _release_ssh_sftp(session, client, sftp)

            session_manager.update_session_metadata(
                session_id,
                directory=next_directory,
                # The shell this pane was reading is being closed, so its last
                # report is no longer an observation of anything live. The
                # directory it named is what `directory` now holds.
                current_directory=None,
                explorer_root_directory=root_directory,
                # The live explorer needs a confinement boundary either way, so
                # the resolved root is always stored. The flag is what keeps a
                # *derived* one from pinning the next switch to a directory
                # nobody chose -- so it describes the root actually stored, not
                # the candidate it was chosen among. Holding *a* configured root
                # is not the same as having opened on it:
                # `_resolve_explorer_open_root()` returns it only while it still
                # holds the observed cwd, and a shell that has walked outside it
                # gets a derived root that used to be stored wearing this flag.
                explorer_root_configured=bool(configured_root)
                and root_directory == configured_root,
                initial_command="",
                startup_mode="explorer",
            )
        else:
            if requested_directory:
                next_directory = os.path.abspath(os.path.expanduser(str(requested_directory)))
            if not next_directory or not os.path.isdir(next_directory):
                raise ModeTransitionError("Explorer root directory does not exist", 400)

            next_directory = os.path.realpath(os.path.abspath(os.path.expanduser(next_directory)))
            configured_root = _configured_explorer_root_directory(session)
            if configured_root:
                configured_root = os.path.realpath(
                    os.path.abspath(os.path.expanduser(configured_root))
                )
                if not os.path.isdir(configured_root):
                    configured_root = ""
            if launch_directory:
                launch_directory = os.path.realpath(
                    os.path.abspath(os.path.expanduser(str(launch_directory)))
                )
            repo_root = None
            if not (configured_root and _local_path_inside(configured_root, next_directory)):
                repo_root = _explorer_cwd_repo_root(_LocalExplorerBackend(session), next_directory)
            root_directory = _resolve_explorer_open_root(
                configured_root,
                next_directory,
                str(launch_directory or ""),
                repo_root,
                contains=_local_path_inside,
            )
            open_path = _relative_explorer_path(root_directory, next_directory)

            session_manager.update_session_metadata(
                session_id,
                host="File Explorer",
                directory=next_directory,
                current_directory=None,
                explorer_root_directory=root_directory,
                # Same rule as the SSH branch above: the flag qualifies the root
                # being stored, so a derived root never pins the pane.
                explorer_root_configured=bool(configured_root)
                and root_directory == configured_root,
                username="",
                port=22,
                password=None,
                initial_command="",
                startup_mode="explorer",
                browser_tabs=[],
                browser_active_tab=0,
            )
        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        effects.close_connection(session_id, clear_buffer=True)
        effects.broadcast_status(session_id)
        payload = session_manager.get_session(session_id).to_dict()
        # Presentation paths are relative to the root they were captured under.
        # A live terminal -> explorer switch may have just derived a different
        # root, so the saved Preview directory is not a valid opening target.
        # This transient field names the observed cwd under the freshly resolved
        # root; it is response-only and never joins the durable pane shape.
        payload["explorer_open_path"] = open_path
        if cwd_probe["requested"] and not cwd_probe["resolved"]:
            # The probe could not answer, so the pane opened on an assumed
            # directory. Say so, and say which one: the silent fallback to the
            # launch directory is the flakiness ISSUE-2026-044 reports.
            #
            # Only the three fields a reader has: `resolved` is what
            # terminals.js branches on, `reason` and `directory` are what the
            # notice says. `requested` was always `true` here -- the guard
            # above is what puts the object in the payload at all -- and
            # `source` names an internal provenance nothing on the client
            # distinguishes. Both stay inside `_refresh_pane_cwd()`, where they
            # drive the probe and the launch-fallback verdict; neither crosses
            # the HTTP boundary as a field nothing reads (guardrail 5).
            payload["cwd_probe"] = {
                "resolved": False,
                "reason": cwd_probe["reason"],
                "directory": next_directory,
            }
        return payload

    if not (_is_explorer_session(session) or _is_browser_session(session)):
        return session.to_dict()

    try:
        next_directory, root_path = _resolve_pane_terminal_directory(
            session,
            data.get("directory", ""),
        )
    except ValueError as exc:
        raise ModeTransitionError(str(exc), 400) from exc
    except _sftp_request_error_types() as exc:
        raise ModeTransitionError(str(exc), 500) from exc

    updates = {
        "directory": next_directory,
        # A fresh shell starts at `next_directory`; whatever the pane's last
        # shell reported is not an observation of this one.
        "current_directory": None,
        # `_resolve_pane_terminal_directory()` hands back the *configured* root
        # or nothing at all, so a pane that never had a chosen root leaves
        # explorer mode without one and the next switch re-derives from where
        # the pane actually is.
        "explorer_root_directory": root_path,
        "explorer_root_configured": bool(root_path),
        # A fixed Git pin is relative to the explorer root it was captured
        # under. Once this pane becomes a terminal it can move anywhere, so the
        # next explorer must start from its newly resolved root/current folder
        # rather than reinterpret a pin belonging to the previous root.
        "explorer_git_pin_active": False,
        "explorer_git_pinned_path": "",
        "initial_command": "",
        "initial_command_mode": "command",
        "startup_mode": "terminal",
        # A pane leaving browser mode drops its tab strip; a stale strip would
        # otherwise be re-persisted and reopen browser tabs on a shell pane.
        "browser_tabs": [],
        "browser_active_tab": 0,
    }
    if session.mode == "wsl":
        updates["host"] = _local_shell_display_name(
            use_wsl=session.use_wsl,
            use_powershell=session.use_powershell,
            distribution=session.distribution,
        )
    session_manager.update_session_metadata(session_id, **updates)
    session_manager.update_session_status(session_id, SessionStatus.PENDING)
    effects.broadcast_status(session_id)
    effects.start_connector(session_id)
    return session_manager.get_session(session_id).to_dict()
