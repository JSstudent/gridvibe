"""The pane-mode transition transaction: terminal <-> explorer <-> browser.

Extracted from `web/api.py` after `change_session_mode()` had regrown to ~250
lines owning validation, working
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

import logging
import os
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from sessions.manager import SessionStatus
from web.agent_conversations import EMPTY_CONVERSATION_FIELDS
from web.agent_handoffs import handoffs as agent_handoffs
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
from web.pane_directory import StatedDirectoryError, resolve_stated_directory
from web.pane_gates import (
    MODE_GATE,
    GateWording,
    PaneGateRefusal,
    attach_confirmation,
    check_caller,
    check_lineage,
    read_agent_request,
    refuse,
    what_ends,
)
from web.saved_sessions import _normalize_startup_mode
from web.session_presentation import DEFAULT_BROWSER_URL, _normalize_browser_url
from web.terminal_io import (
    CWD_SOURCE_LAUNCH,
    _local_shell_display_name,
    effective_directory,
)

logger = logging.getLogger(__name__)


class ModeTransitionError(Exception):
    """One pane-mode transition refused, with the status the route answers.

    Narrow on purpose: the route maps `message` and `status_code` onto an
    error response and nothing else, so the service never has to know what a
    Flask response looks like to refuse a transition.
    """

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        #: A gate refusal's structure -- which gate, whether `override` could
        #: waive it, and the question to ask -- carried to the route's body.
        self.details = dict(details or {})


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


def _stated_root_kept(session: Any, next_directory: str) -> str:
    """The configured root, when a stated directory still lies inside it.

    A root somebody chose survives a re-root that stays under it; one the pane
    derived for itself never does, and neither survives a stated path above
    it -- the pane leaves with no root and ``explorer_root_configured: False``,
    the same state a derived root produces.
    """
    configured = _configured_explorer_root_directory(session)
    if not configured:
        return ""
    if getattr(session, "mode", "") == "ssh":
        return configured if _remote_path_inside(configured, next_directory) else ""
    root = os.path.realpath(os.path.abspath(os.path.expanduser(configured)))
    candidate = os.path.realpath(os.path.abspath(os.path.expanduser(next_directory)))
    return configured if _local_path_inside(root, candidate) else ""


def _same_directory(session: Any, left: str, right: str) -> bool:
    """Whether two directories name the same place for this pane's shell."""
    left = str(left or "").strip()
    right = str(right or "").strip()
    if not left or not right:
        return False
    if getattr(session, "mode", "") == "ssh" or left.startswith("/"):
        return left.replace("\\", "/").rstrip("/") == right.replace("\\", "/").rstrip("/")
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(
        os.path.normpath(right)
    )


def _relaunch_terminal_at(
    session_id: str,
    session: Any,
    directory: str,
    effects: "ModeTransitionEffects",
) -> Dict[str, Any]:
    """Start this terminal or agent pane's shell again, at ``directory``.

    The one transition a directory alone asks for. A plain terminal keeps
    everything but its place; an agent pane becomes the plain terminal the mode
    switch names, because the agent is what ends. Everything fallible was
    decided by the caller, so from here on nothing refuses.
    """
    updates: Dict[str, Any] = {
        "directory": directory,
        # The shell being replaced reported where *it* was; the new one starts
        # at `directory`, and its own report will follow.
        "current_directory": None,
    }
    if str(getattr(session, "startup_mode", "") or "") == "agent":
        updates.update(
            {
                "startup_mode": "terminal",
                "initial_command": "",
                "initial_command_mode": "command",
                "agent_selection": "",
                "custom_agent": "",
                "agent_auto_mode": False,
                "agent_mcp": False,
                **EMPTY_CONVERSATION_FIELDS,
            }
        )
    session_manager.update_session_metadata(session_id, **updates)
    logger.info(
        "Pane relaunched at a stated directory session_id=%s directory=%s",
        session_id,
        directory,
    )
    effects.close_connection(session_id, clear_buffer=True)
    # Whatever brief was still waiting was meant for the process that ends.
    agent_handoffs.drop_bound(session_id, "pane relaunched")
    session_manager.update_session_status(session_id, SessionStatus.PENDING)
    effects.broadcast_status(session_id)
    effects.start_connector(session_id)
    return session_manager.get_session(session_id).to_dict()


def apply_pane_mode_change(
    session_id: str,
    data: Dict[str, Any],
    effects: "ModeTransitionEffects",
    *,
    stated_directory: str = "",
    report_change: bool = False,
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

    ``stated_directory`` is a path a *caller* named, which is not the same as
    the ``directory`` the header's toggle sends (the folder a live explorer is
    showing, which stays inside that explorer's root). A stated path wins over
    an observed cwd, is resolved on the pane's own machine rather than through
    a root the pane derived for itself, and on a terminal or agent pane asked
    to be a terminal relaunches it there. ``report_change`` adds ``changed``
    to the payload, for a caller that must not read a no-op as a success.
    """
    stated_directory = str(stated_directory or "").strip()
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
        # No agent runs in an explorer or browser pane, so a brief still
        # waiting for one has nobody left to read it.
        agent_handoffs.drop_bound(session_id, "pane mode changed")
        effects.broadcast_status(session_id)
        if report_change:
            browser_snapshot = {**browser_snapshot, "changed": True}
        return browser_snapshot

    if target_mode == "explorer":
        requested_directory = stated_directory or data.get("directory")
        refresh_requested = bool(data.get("refresh_cwd"))
        cwd_probe = _refresh_pane_cwd(session_id, session, refresh_requested)
        # A path the caller named outranks where the shell was last seen; only
        # an explicit refresh asks the pane instead. With nothing stated this
        # is the header toggle's rule: root where the shell is standing.
        if cwd_probe["directory"] and (not stated_directory or refresh_requested):
            requested_directory = cwd_probe["directory"]
        if stated_directory and requested_directory == stated_directory:
            if session.mode == "ssh":
                try:
                    requested_directory = resolve_stated_directory(
                        session, stated_directory
                    )
                except ValueError as exc:
                    raise ModeTransitionError(
                        f"{exc} Nothing was changed.", 400
                    ) from exc
                except _sftp_request_error_types() as exc:
                    raise ModeTransitionError(str(exc), 500) from exc
            else:
                # A Files pane browses GridVibe's own filesystem, whatever
                # shell family the pane ran, so that is where this is checked.
                local = os.path.abspath(os.path.expanduser(stated_directory))
                if not os.path.isdir(local):
                    raise ModeTransitionError(
                        f"The directory {stated_directory} does not exist on "
                        "GridVibe's own machine, where a Files pane browses. "
                        "Nothing was changed.",
                        400,
                    )
        next_directory = session.directory
        root_directory = ""
        open_path = ""

        if session.mode == "ssh":
            if requested_directory:
                next_directory = _remote_path_clean(requested_directory)
            next_directory = _remote_path_clean(next_directory or "/")
            client = None
            sftp = None
            try:
                client, sftp = _acquire_ssh_sftp(session)
                next_directory = sftp.normalize(next_directory)
                if not _remote_is_directory(sftp, next_directory):
                    raise ValueError("Explorer root directory does not exist")
                # Asked for every transition now, not only when no configured
                # root held the cwd: the root is derived from where the pane is
                # standing, so the repository it is standing in is the question.
                repo_root = _explorer_cwd_repo_root(
                    _SftpExplorerBackend(session, client, sftp), next_directory
                )
                root_directory = _resolve_explorer_open_root(next_directory, repo_root)
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
                # Always derived, because this transition now always derives:
                # the root comes from the pane's own working directory, so no
                # root it reaches Files carrying can pin the next transition to
                # a directory the shell has since left.
                explorer_root_configured=False,
                initial_command="",
                startup_mode="explorer",
                **EMPTY_CONVERSATION_FIELDS,
            )
        else:
            if requested_directory:
                next_directory = os.path.abspath(os.path.expanduser(str(requested_directory)))
            if not next_directory or not os.path.isdir(next_directory):
                raise ModeTransitionError("Explorer root directory does not exist", 400)

            next_directory = os.path.realpath(os.path.abspath(os.path.expanduser(next_directory)))
            repo_root = _explorer_cwd_repo_root(_LocalExplorerBackend(session), next_directory)
            root_directory = _resolve_explorer_open_root(next_directory, repo_root)
            open_path = _relative_explorer_path(root_directory, next_directory)

            session_manager.update_session_metadata(
                session_id,
                host="File Explorer",
                directory=next_directory,
                current_directory=None,
                explorer_root_directory=root_directory,
                # Same rule as the SSH branch above.
                explorer_root_configured=False,
                username="",
                port=22,
                password=None,
                initial_command="",
                startup_mode="explorer",
                **EMPTY_CONVERSATION_FIELDS,
                browser_tabs=[],
                browser_active_tab=0,
            )
        session_manager.update_session_status(session_id, SessionStatus.CONNECTED)
        effects.close_connection(session_id, clear_buffer=True)
        # No agent runs in an explorer or browser pane, so a brief still
        # waiting for one has nobody left to read it.
        agent_handoffs.drop_bound(session_id, "pane mode changed")
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
        if report_change:
            payload["changed"] = True
        return payload

    if not (_is_explorer_session(session) or _is_browser_session(session)):
        # Already a shell. Nothing stated is nothing to do, and says so to a
        # caller that asked; a stated directory is a relaunch there, unless
        # the pane is a plain terminal already standing in it.
        unchanged = session.to_dict()
        if report_change:
            unchanged["changed"] = False
        if not stated_directory:
            return unchanged
        try:
            next_directory = resolve_stated_directory(session, stated_directory)
        except ValueError as exc:
            raise ModeTransitionError(f"{exc} Nothing was changed.", 400) from exc
        except _sftp_request_error_types() as exc:
            raise ModeTransitionError(str(exc), 500) from exc
        if str(getattr(session, "startup_mode", "") or "") != "agent":
            current, _source = effective_directory(session_id, session)
            if _same_directory(session, current or session.directory, next_directory):
                return unchanged
        payload = _relaunch_terminal_at(session_id, session, next_directory, effects)
        if report_change:
            payload["changed"] = True
        return payload

    try:
        if stated_directory:
            # Resolved on its own merits, where the new shell will run. The
            # explorer's root bounds what that pane browses; it is not a limit
            # on where the pane may be re-rooted, least of all a root it
            # derived for itself.
            next_directory = resolve_stated_directory(session, stated_directory)
            root_path = _stated_root_kept(session, next_directory)
        else:
            next_directory, root_path = _resolve_pane_terminal_directory(
                session,
                data.get("directory", ""),
            )
    except StatedDirectoryError as exc:
        raise ModeTransitionError(f"{exc} Nothing was changed.", 400) from exc
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
        # A pane leaving explorer mode drops the sidebar's whole scope
        # selection -- both controls, not only the one carrying a path.
        # A fixed Git pin is relative to the explorer root it was captured
        # under. Once this pane becomes a terminal it can move anywhere, so the
        # next explorer must start from its newly resolved root/current folder
        # rather than reinterpret a pin belonging to the previous root.
        # Follow carries no path, which is why a restore and the launcher's
        # re-rooting both leave it alone: each opens the pane at its root,
        # where following the browsed folder is indistinguishable from not
        # following it. This transition is the one that re-enters explorer mode
        # at wherever the shell walked to, so leaving Follow on brought the
        # pane back scoped to a deep subdirectory of a freshly derived root,
        # chain pressed and no pin to explain it.
        "explorer_git_pin_active": False,
        "explorer_git_pinned_path": "",
        "explorer_git_pin_kind": "dir",
        "explorer_git_follow_browsing": False,
        "initial_command": "",
        "initial_command_mode": "command",
        "startup_mode": "terminal",
        **EMPTY_CONVERSATION_FIELDS,
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
    payload = session_manager.get_session(session_id).to_dict()
    if report_change:
        payload["changed"] = True
    return payload


# ==================== The gated half: a mode switch asked for by an agent =====
#
# `apply_pane_mode_change` above is the pane header's own mode toggle: the
# person looking at the pane pressed it, so there is nobody to check. A tool
# asking for the same switch is a different question, because switching a pane
# out of terminal mode *ends the shell behind it* -- the same blast radius the
# gated relaunch in `web/session_shell.py` has, and the same three gates.
#
# Self and lineage are `web/pane_gates.py`'s, shared with the relaunch and the
# clear. The third is this transaction's own, and it is looser than the
# relaunch's for a reason: an explorer or browser pane is exactly what this
# transaction exists to move, so only a pane with an *agent running in it* is
# refused -- switching that pane's mode would end the agent, which is the thing
# a person has to ask for by name.
#
# `override` waives lineage and the agent refusal, never self. A pane the user
# made by hand, or one running an agent nobody is using any more, is exactly
# what "override the bottom pane and make it a file explorer" means; the
# calling agent states it only on a person's own words, in this conversation,
# about this pane.

#: How this transaction names itself inside a shared refusal.
MODE_SWITCH_WORDING = GateWording(
    request_noun="a mode switch",
    self_reason=(
        "Switching its mode would end this agent in the middle of the call."
    ),
    act="switch its mode",
    acts="switches the mode of",
)

#: What the caller may state. Everything else the ungated route accepts is the
#: page's own business: `active_tab` belongs to a live tab strip nothing outside
#: the browser can see, and a whole browser tab list is presentation state with
#: its own ordered, revisioned transaction.
_AGENT_MODE_FIELDS = ("startup_mode", "directory", "url", "refresh_cwd")

#: The three a tool may ask for, stated here rather than inferred from
#: `_normalize_startup_mode()`. That normalizer answers "terminal" for anything
#: it cannot honour, which is the right answer for the page -- the toggle only
#: offers what the pane can be -- and the wrong one for a tool, which would be
#: told a browser pane was opened and handed back a plain shell.
_AGENT_MODE_TARGETS = ("terminal", "explorer", "browser")


#: What a mode switch turns a pane into, in the words a question uses.
_MODE_TARGET_WORDS = {
    "terminal": "a plain terminal",
    "explorer": "a file explorer",
    "browser": "a browser preview",
}


def _mode_switch_question(facts: Any, requested_mode: str) -> str:
    """The question a calling agent puts to the person before an override."""
    target = _MODE_TARGET_WORDS.get(requested_mode, "another kind of pane")
    return (
        f"Switching {facts.name} to {target} ends {what_ends(facts)}. "
        f"Override {facts.name}?"
    )


def apply_agent_pane_mode_change(
    session_id: str,
    payload: Dict[str, Any],
    effects: "ModeTransitionEffects",
) -> Dict[str, Any]:
    """Switch one pane's mode on behalf of a *calling agent's* pane.

    Every gate is checked before anything is mutated, closed or restarted, so a
    refusal leaves the pane exactly as it was found. Past the gates this is the
    ordinary transition, with the ordinary refusals -- a pane with no shell to
    replace, a browser pane asked for on a remote host, a directory that does
    not exist.
    """
    # One translation point for the whole gate sequence: every refusal below
    # is a `PaneGateRefusal` carrying the status the route should answer, and
    # this is where it becomes the one exception `web/api.py` maps.
    session = None
    requested_mode = ""
    try:
        request = read_agent_request(payload, MODE_SWITCH_WORDING)

        requested_mode = str(payload.get("startup_mode") or "").strip().lower()
        if requested_mode not in _AGENT_MODE_TARGETS:
            raise PaneGateRefusal(
                "startup_mode must be "
                + ", ".join(f"'{mode}'" for mode in _AGENT_MODE_TARGETS),
                400,
            )

        session = session_manager.get_session(session_id)
        if not session:
            raise PaneGateRefusal("Session not found", 404)

        check_caller(session_id, request, MODE_SWITCH_WORDING)

        if (
            str(getattr(session, "startup_mode", "") or "") == "agent"
            and not request.override
        ):
            raise refuse(
                MODE_GATE,
                "This pane is running an agent, and switching its mode would "
                "end it. Split off a new pane instead, unless the user "
                "explicitly asked to override this pane.",
                waivable=True,
            )

        check_lineage(session, request, MODE_SWITCH_WORDING)

        if requested_mode == "browser" and getattr(session, "mode", "") != "wsl":
            # Refused rather than quietly normalized. The page's toggle does
            # not appear on a remote pane at all, so the ungated route never
            # has to say this; a tool can ask, and being handed a plain
            # terminal labelled a success is the one answer it must not get.
            raise PaneGateRefusal(
                "Browser mode is only available for Local Repo sessions. "
                "GridVibe draws the preview on its own machine, and this "
                "pane's shell does not run there, so nothing was changed.",
                400,
            )
    except PaneGateRefusal as exc:
        attach_confirmation(
            exc,
            session,
            lambda facts: _mode_switch_question(facts, requested_mode),
        )
        raise ModeTransitionError(
            exc.message, exc.status_code, exc.details()
        ) from exc

    change = {key: payload[key] for key in _AGENT_MODE_FIELDS if key in payload}
    # A tool's `directory` is always a path the caller named, never the folder
    # a live explorer is showing -- so it travels as the stated one.
    stated_directory = str(change.pop("directory", "") or "").strip()
    logger.info(
        "Agent-requested pane mode switch session_id=%s startup_mode=%s "
        "requested_by_session_id=%s override=%s directory_stated=%s",
        session_id,
        str(change.get("startup_mode") or "-"),
        request.caller_session_id,
        request.override,
        bool(stated_directory),
    )
    return apply_pane_mode_change(
        session_id,
        change,
        effects,
        stated_directory=stated_directory,
        report_change=True,
    )
