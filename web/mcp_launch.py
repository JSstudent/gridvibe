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
from typing import Any, Dict, Optional

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


def mcp_config_path() -> str:
    """Where the generated config lives."""
    return os.path.join(BASE_DIR, MCP_CONFIG_FILENAME)


def loopback_base_url(host: Any, port: Any) -> str:
    """Return the URL a child process on this machine should call."""
    resolved_host = str(host or "").strip()
    if resolved_host in _UNROUTABLE_BIND_HOSTS:
        resolved_host = "127.0.0.1"
    try:
        resolved_port = int(port)
    except (TypeError, ValueError):
        resolved_port = 5050
    return f"http://{resolved_host}:{resolved_port}"


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
    url = set_server_address(host, port) if host is not None or port is not None else server_base_url()
    target = path or mcp_config_path()
    document = build_mcp_config(
        interpreter=interpreter or sys.executable or "python",
        url=url,
    )
    try:
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        logger.warning("Could not write %s: %s", target, exc)
        return ""
    logger.info("MCP sidecar config written to %s (url=%s)", target, url)
    return target


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
