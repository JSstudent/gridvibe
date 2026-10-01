"""The two things GridVibe does so a pane's agent can have GridVibe tools.

**The generated config.** ``<BASE_DIR>/.gridvibe_mcp.json``, rewritten on every
app start and gitignored. Generated rather than committed because both values
in it are per-install: the interpreter is the venv GridVibe is actually running
under, and the port comes from the running config, not a constant. Rewriting it
every start means a moved install, a changed port or a rebuilt venv self-heals
with no user action.

It carries no ``env`` block. The URL is baked into ``args`` by the process that
owns the port, and identity arrives by inheritance -- so the file is identical
for every pane, and one file serves them all.

**The opencode config.** ``<BASE_DIR>/.gridvibe_opencode.json``, written beside
it from the same interpreter and URL. opencode reads a different schema (an
``mcp`` table, a ``type``, a ``command`` *array*), so it gets its own file
rather than a second key in the one Claude and Copilot read. Unlike the first
file it states the identity, as ``{env:NAME}`` references opencode resolves
from its own environment: what an MCP child inherits is opencode's choice, not
a guarantee. References rather than values, so one file still serves every
pane.

**Pane identity.** Five variables merged into a local pane's spawn environment.
They go in at the call site in ``_connect_local_session``, *not* through
``_local_shell_integration``: that function opens by returning unchanged when
``terminal.shell_integration`` is off, and that setting is a kill switch for
the prompt hook. A user who turns it off has said nothing about MCP, and would
otherwise silently get panes whose agents cannot tell what workspace they are
in, with no error anywhere.
"""

import json
import logging
import os
import sys
from typing import Any, Dict, Optional, Tuple

from web.paths import BASE_DIR
from web.terminal_cwd import WSLENV_VARIABLE, merge_wslenv

logger = logging.getLogger(__name__)

MCP_CONFIG_FILENAME = ".gridvibe_mcp.json"
MCP_SERVER_NAME = "gridvibe"

#: Run as a script path rather than ``-m``: the agent CLI spawns the sidecar
#: with the *pane's* working directory, which is the user's project, so the
#: install directory is never implicitly on ``sys.path``. ``__main__.py``
#: bootstraps the rest.
SIDECAR_ENTRY = os.path.join(BASE_DIR, "gridvibe_mcp", "__main__.py")

#: Bind hosts that name no reachable address for a loopback child.
_UNROUTABLE_BIND_HOSTS = {"", "0.0.0.0", "::", "*"}

#: Set once by ``run_server`` when the real bind address is known. Read by the
#: pane spawn, which happens long after and has no other way to learn the port.
_server_base_url = ""


#: The one file a production process owns. Overridable for the same reason
#: ``runtime_state.json`` is: this is written by ``run_server``, which a test
#: calls with a fabricated host and port -- and the write is a plain
#: ``open()``, so nothing in ``web/state_files.py`` stands between the suite
#: and the developer's real config. A test run that repointed it at an
#: unroutable address left every later pane's agent starting a sidecar that
#: could not reach GridVibe, until the next app start rewrote it.
PRODUCTION_MCP_CONFIG_PATH = os.path.join(BASE_DIR, MCP_CONFIG_FILENAME)

#: The opencode document, beside the first and overridable for the same reason.
OPENCODE_CONFIG_FILENAME = ".gridvibe_opencode.json"
PRODUCTION_OPENCODE_CONFIG_PATH = os.path.join(BASE_DIR, OPENCODE_CONFIG_FILENAME)

#: The registry ``mcp.style`` naming opencode's config *format*, local or
#: remote. A format rather than a CLI, so a fork that reads the same schema can
#: name it too once verified.
OPENCODE_CONFIG_STYLE = "opencode_config"

#: How long opencode waits for the sidecar to *list* its tools, in ms. Its own
#: default is 5 s, which a cold venv start on Windows can approach -- and past
#: it the tools are silently absent for the whole session.
OPENCODE_TOOL_LIST_TIMEOUT_MS = 15000


def mcp_config_path() -> str:
    """Where the generated config lives, refusing production state in tests.

    Read per call rather than resolved at import: ``run_server`` is reached
    long after import, and a suite that set the override afterwards would
    otherwise still hold the real path.
    """
    override = os.environ.get("GRIDVIBE_MCP_CONFIG_PATH")
    if override:
        return override
    if os.environ.get("GRIDVIBE_TEST_MODE"):
        raise RuntimeError(
            "Refusing the production .gridvibe_mcp.json in test mode; set "
            "GRIDVIBE_MCP_CONFIG_PATH (tests/__init__.py does this)."
        )
    return PRODUCTION_MCP_CONFIG_PATH


def opencode_config_path() -> str:
    """Where the opencode document lives, refusing production state in tests.

    The same rule as :func:`mcp_config_path`, for the same reason:
    ``run_server`` writes it with a plain ``open()``.
    """
    override = os.environ.get("GRIDVIBE_OPENCODE_CONFIG_PATH")
    if override:
        return override
    if os.environ.get("GRIDVIBE_TEST_MODE"):
        raise RuntimeError(
            "Refusing the production .gridvibe_opencode.json in test mode; set "
            "GRIDVIBE_OPENCODE_CONFIG_PATH (tests/__init__.py does this)."
        )
    return PRODUCTION_OPENCODE_CONFIG_PATH


#: The one ``TerminalSession.mode`` whose shell runs on the machine GridVibe
#: runs on. Every other mode is a remote host reached over SSH.
LOCAL_PANE_MODE = "wsl"


def pane_can_run_the_sidecar(session: Any) -> bool:
    """Return whether this pane's agent could start the sidecar at all.

    Only a pane whose shell is on *this* machine can. An SSH pane's agent runs
    on the remote host, where both halves of the flag are wrong: the config
    path names a directory that exists only here, and ``127.0.0.1:<port>``
    there is the remote host's own loopback, not GridVibe's.

    Held here rather than at the launcher checkbox because the checkbox is not
    the only way in -- a saved preset, a restored snapshot and the relaunch
    route all carry ``agent_mcp`` forward, and a pane moved to SSH must not
    keep a flag that was true when it was local.
    """
    return str(getattr(session, "mode", "") or "") == LOCAL_PANE_MODE


def url_host(host: Any) -> str:
    """One host as a URL authority: an IPv6 literal wears brackets.

    ``http://::1:5050`` is not a URL: everything that reads one separates the
    host from the port at a colon, and that address is all colons. An explicit
    ``::1`` bind wrote it into the generated config anyway, where ``urlsplit``
    reads neither a host nor a port out of it -- so every pane's sidecar fell
    back to the loopback default and could not reach a GridVibe that is bound
    to IPv6 only. Brackets are what separate the two, and a host that already
    carries them is left alone.
    """
    text = str(host or "").strip()
    if text.startswith("[") and text.endswith("]"):
        return text
    return f"[{text}]" if ":" in text else text


def loopback_base_url(host: Any, port: Any) -> str:
    """Return the URL a child process on this machine should call."""
    resolved_host = str(host or "").strip()
    if resolved_host.startswith("[") and resolved_host.endswith("]"):
        # A bracketed bind address is the same address; unwrap it so the
        # unroutable check below reads it, and `url_host` puts them back.
        resolved_host = resolved_host[1:-1].strip()
    if resolved_host in _UNROUTABLE_BIND_HOSTS:
        resolved_host = "127.0.0.1"
    try:
        resolved_port = int(port)
    except (TypeError, ValueError):
        resolved_port = 5050
    return f"http://{url_host(resolved_host)}:{resolved_port}"


def set_server_address(host: Any, port: Any) -> str:
    """Remember the resolved bind address for every later pane spawn."""
    global _server_base_url
    _server_base_url = loopback_base_url(host, port)
    return _server_base_url


def server_base_url() -> str:
    """The URL panes are told to call, falling back to the configured port."""
    if _server_base_url:
        return _server_base_url
    from web.config import runtime_config

    server = runtime_config.app_config.get("server", {})
    if not isinstance(server, dict):
        server = {}
    return loopback_base_url(server.get("host"), server.get("port", 5050))


def build_mcp_config(*, interpreter: str, url: str) -> Dict[str, Any]:
    """The whole generated document, as data."""
    return {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": interpreter,
                "args": [SIDECAR_ENTRY, "--url", url],
            }
        }
    }


def build_opencode_config(*, interpreter: str, url: str) -> Dict[str, Any]:
    """The opencode document, as data.

    ``command`` holds the same three values as :func:`build_mcp_config`'s
    ``command`` and ``args``, so the two files cannot name different things.
    The identity names are the ones the spawn writes, read from
    :func:`pane_identity_environment` rather than listed again here.
    """
    identity_names = pane_identity_environment(session_id="", base_url=url)
    return {
        "$schema": "https://opencode.ai/config.json",
        "mcp": {
            MCP_SERVER_NAME: {
                "type": "local",
                "command": [interpreter, SIDECAR_ENTRY, "--url", url],
                "environment": {name: f"{{env:{name}}}" for name in identity_names},
                "timeout": OPENCODE_TOOL_LIST_TIMEOUT_MS,
            }
        },
    }


def _launch_values(host: Any, port: Any, interpreter: Optional[str]) -> Tuple[str, str]:
    """The interpreter and URL every generated document names."""
    url = set_server_address(host, port) if host is not None or port is not None else server_base_url()
    return interpreter or sys.executable or "python", url


def _write_document(target: str, document: Dict[str, Any], url: str) -> str:
    """Write one generated document. Returns the path, or "" if it could not.

    Always through ``json.dump``: a Windows path holds backslashes, and a
    single one written by hand is invalid JSON the CLI rejects.
    """
    try:
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        logger.warning("Could not write %s: %s", target, exc)
        return ""
    logger.info("MCP sidecar config written to %s (url=%s)", target, url)
    return target


def write_mcp_config(
    host: Any = None,
    port: Any = None,
    *,
    interpreter: Optional[str] = None,
    path: Optional[str] = None,
) -> str:
    """Rewrite the generated config. Returns the path, or "" if it could not.

    A failure here costs the MCP checkbox and nothing else, so it is logged and
    reported rather than raised: GridVibe still starts.
    """
    resolved_interpreter, url = _launch_values(host, port, interpreter)
    target = path or mcp_config_path()
    document = build_mcp_config(interpreter=resolved_interpreter, url=url)
    return _write_document(target, document, url)


def write_opencode_config(
    host: Any = None,
    port: Any = None,
    *,
    interpreter: Optional[str] = None,
    path: Optional[str] = None,
) -> str:
    """Rewrite the opencode document. Returns the path, or "" if it could not.

    Called beside :func:`write_mcp_config` with the same arguments, so both
    files name one interpreter and one URL. A failure costs opencode its tools
    and leaves the other file alone.
    """
    resolved_interpreter, url = _launch_values(host, port, interpreter)
    target = path or opencode_config_path()
    document = build_opencode_config(interpreter=resolved_interpreter, url=url)
    return _write_document(target, document, url)


def read_mcp_server_block(path: Optional[str] = None) -> Dict[str, Any]:
    """Return the generated config's own server entry, or ``{}``.

    Not every agent CLI can be handed a config *file*. Codex takes inline
    ``-c key=value`` overrides instead, so its launch line needs the same
    command and args the file holds rather than a path to it. Read back from
    the generated file rather than rebuilt from ``build_mcp_config`` so both
    shapes state one thing: whatever the running install actually wrote.
    """
    target = path or mcp_config_path()
    try:
        with open(target, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s: %s", target, exc)
        return {}
    servers = document.get("mcpServers")
    if not isinstance(servers, dict):
        return {}
    block = servers.get(MCP_SERVER_NAME)
    return block if isinstance(block, dict) else {}


def pane_identity_environment(
    *,
    session_id: str,
    group_id: str = "",
    workspace_id: str = "",
    agent_depth: Any = 0,
    base_url: str = "",
) -> Dict[str, str]:
    """The five variables a GridVibe-started pane carries.

    Every child of the pane inherits them, which is how the sidecar -- a
    grandchild -- knows which pane it is without a handshake.
    """
    try:
        depth = max(0, int(agent_depth or 0))
    except (TypeError, ValueError):
        depth = 0
    return {
        "GRIDVIBE_URL": base_url or server_base_url(),
        "GRIDVIBE_SESSION_ID": str(session_id or ""),
        "GRIDVIBE_GROUP_ID": str(group_id or ""),
        "GRIDVIBE_WORKSPACE_ID": str(workspace_id or ""),
        "GRIDVIBE_AGENT_DEPTH": str(depth),
    }


def apply_pane_identity(
    environment: Dict[str, str],
    identity: Dict[str, str],
    *,
    shell_kind: str = "",
) -> Dict[str, str]:
    """Merge pane identity into a spawn environment, WSLENV included.

    ``wsl.exe`` forwards only what ``WSLENV`` names, and the prompt hook
    extends the same variable -- so both callers go through
    :func:`web.terminal_cwd.merge_wslenv` rather than overwriting each other's.
    """
    merged = dict(environment)
    merged.update(identity)
    if str(shell_kind or "").strip() == "wsl":
        merged[WSLENV_VARIABLE] = merge_wslenv(
            merged.get(WSLENV_VARIABLE),
            identity.keys(),
        )
    return merged
