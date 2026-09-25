"""Agent CLI registry, binary detection, preflight, and host probing.

Extracted from ``web/api.py`` (deep-dive finding 6.2). Covers the static
agent registry, the per-target detection probes (Windows/POSIX/WSL/SSH) with
their result cache, the launcher preflight payload builder, SSH ping/TCP
probes, and the local WSL distribution inspection helpers. ``web.api``
re-exports every name for backwards compatibility.
"""

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from web.agent_conversations import (
    CONVERSATION_ID_FIELD,
    CONVERSATION_PROVIDER_FIELD,
    CONVERSATION_RESUME_FIELD,
    EMPTY_CONVERSATION_FIELDS,
    compose_conversation_command,
)
from web.agent_handoffs import HANDOFF_OPENING_PROMPT
from web.agent_session_hooks import claude_settings_fragment
from web.config import _load_json_file, runtime_config
from web.hostkeys import _apply_host_key_policy
from web.mcp_launch import pane_can_run_the_sidecar
from web.paths import BASE_DIR
from web.saved_sessions import _normalize_connection_mode

try:
    import paramiko
except ImportError:  # pragma: no cover - handled at runtime when dependency is missing
    paramiko = None

logger = logging.getLogger(__name__)

AGENT_REGISTRY_PATH = os.path.join(BASE_DIR, "agent_registry.json")


def _load_agent_registry() -> Dict[str, Any]:
    """Load the static agent CLI registry from disk."""
    try:
        registry = _load_json_file(AGENT_REGISTRY_PATH)
    except OSError as exc:
        logger.warning(f"Failed to load {AGENT_REGISTRY_PATH}: {exc}")
        return {}
    except json.JSONDecodeError as exc:
        logger.warning(f"Failed to parse {AGENT_REGISTRY_PATH}: {exc}")
        return {}

    normalized: Dict[str, Any] = {}
    for key, value in registry.items():
        if isinstance(value, dict):
            normalized[str(key).strip().lower()] = value
    return normalized


AGENT_REGISTRY = _load_agent_registry()
_AGENT_DETECTION_CACHE_TTL_SECONDS = 30
_agent_detection_cache: Dict[Tuple[str, str, str, str, str, int], Tuple[float, Dict[str, Any]]] = {}
_agent_detection_cache_lock = threading.Lock()


_AUTO_MODE_OPTION_TOKEN = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9_-]*$")
_AUTO_MODE_VALUE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_=-]*$")


def _agent_auto_mode_flag(agent_key: Any) -> str:
    """Return the registry-defined auto-mode flag for one agent, or ""."""
    spec = AGENT_REGISTRY.get(_normalize_agent_key(agent_key))
    if not isinstance(spec, dict):
        return ""
    auto_mode = spec.get("auto_mode")
    if not isinstance(auto_mode, dict):
        return ""
    flag = str(auto_mode.get("flag") or "").strip()
    # A registered flag is composed onto the launch command line and sent to a
    # shell, so it must look like one or more CLI options (optionally each
    # followed by a plain value) separated by single spaces — never anything
    # carrying shell metacharacters that could smuggle a second command. This
    # allows multi-token flags like "--sandbox workspace-write" while a registry
    # typo (";", "$(...)", "rm -rf /") still resolves to no flag.
    tokens = flag.split(" ")
    if not tokens or not tokens[0].startswith("-"):
        return ""
    for token in tokens:
        matcher = _AUTO_MODE_OPTION_TOKEN if token.startswith("-") else _AUTO_MODE_VALUE_TOKEN
        if not matcher.match(token):
            return ""
    return flag


def _agent_auto_mode_description(agent_key: Any) -> str:
    """Return the registry-defined auto-mode description for one agent, or ""."""
    spec = AGENT_REGISTRY.get(_normalize_agent_key(agent_key))
    if not isinstance(spec, dict):
        return ""
    auto_mode = spec.get("auto_mode")
    if not isinstance(auto_mode, dict):
        return ""
    return str(auto_mode.get("description") or "").strip()


#: One word of an update command: a subcommand, never an option value, a path
#: or anything a shell would read as a second command.
_UPDATE_COMMAND_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _agent_update_command(agent_key: Any) -> str:
    """Return the registry-defined self-update command for one agent, or "".

    The command is typed into the pane's shell ahead of the agent's own launch
    line, so it gets the auto-mode flag's guard and one more: it must start
    with the agent's own binary and continue with plain subcommand words. A
    registry entry that names another program, or carries a shell
    metacharacter, resolves to no command -- and so to no update button.
    """
    key = _normalize_agent_key(agent_key)
    spec = AGENT_REGISTRY.get(key)
    if not isinstance(spec, dict):
        return ""
    update = spec.get("update")
    if not isinstance(update, dict):
        return ""
    command = str(update.get("command") or "").strip()
    tokens = command.split(" ")
    binary = str(spec.get("binary") or key).strip()
    if len(tokens) < 2 or tokens[0] != binary:
        return ""
    if not all(_UPDATE_COMMAND_TOKEN.match(token) for token in tokens):
        return ""
    return command


#: The one placeholder an MCP flag template may carry: the absolute path of the
#: generated sidecar config. Substituted (and quoted) at compose time, because
#: the path is per-install and the registry is committed.
_MCP_CONFIG_PLACEHOLDER = "{config}"

#: The same placeholder wearing Copilot's "this is a path, not inline JSON"
#: marker. Matched as one token so the quote can be opened before the `@`.
_MCP_MARKER_PLACEHOLDER = "@{config}"

#: `--mcp-config <path>`, and nothing more adventurous. One option token, one
#: placeholder: a registry typo carrying a shell metacharacter resolves to no
#: flag rather than smuggling a second command into the launch line. The
#: optional `@` is Copilot's own marker for "this argument is a file path, not
#: an inline JSON document" -- one literal character, still no metacharacter.
_MCP_FLAG_TEMPLATE = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9_-]*\s@?\{config\}$")

#: Codex takes no config *file* at launch. It takes `-c key=value` overrides
#: whose value is parsed as TOML, so the sidecar is registered by stating the
#: same command and args the generated file holds. Composed in code rather
#: than templated in the registry: the template would have to carry quotes,
#: and quotes in a registry string are exactly what the guard above exists to
#: keep off the launch line.
_MCP_STYLE_INLINE_TOML = "inline_toml"

#: A server name is a TOML bare key here, so it may hold nothing that would
#: need quoting or open a second table.
_MCP_SERVER_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _agent_mcp_style(agent_key: Any) -> str:
    """Return the registry-declared composition style for one agent, or ""."""
    spec = AGENT_REGISTRY.get(_normalize_agent_key(agent_key))
    if not isinstance(spec, dict):
        return ""
    mcp = spec.get("mcp")
    if not isinstance(mcp, dict):
        return ""
    return str(mcp.get("style") or "").strip()


def _agent_mcp_flag(agent_key: Any) -> str:
    """Return the registry-defined MCP flag *template* for one agent, or "".

    An agent with no block published here has no checkbox -- the same thing
    ``opencode`` already does for Auto mode, and the reason a CLI whose MCP
    mechanism has not been verified needs no code.

    Empty is not the same as "no MCP": an agent composed by *style* rather
    than by template has no flag string to publish. Ask
    :func:`_agent_supports_mcp` whether the checkbox belongs.
    """
    spec = AGENT_REGISTRY.get(_normalize_agent_key(agent_key))
    if not isinstance(spec, dict):
        return ""
    mcp = spec.get("mcp")
    if not isinstance(mcp, dict):
        return ""
    flag = str(mcp.get("flag") or "").strip()
    if not _MCP_FLAG_TEMPLATE.match(flag):
        return ""
    return flag


def _agent_supports_mcp(agent_key: Any) -> bool:
    """Whether this CLI can be handed the sidecar at launch, by any shape.

    The one question the checkbox asks. Five of the eight registered CLIs can
    only register an MCP server by *mutating the user's own config* (an
    ``<agent> mcp add`` subcommand), which a checkbox on a pane has no business
    doing and which would outlive the pane that asked. Those publish nothing
    here and get no checkbox.
    """
    return bool(
        _agent_mcp_flag(agent_key)
        or _agent_mcp_style(agent_key) == _MCP_STYLE_INLINE_TOML
    )


def _agent_mcp_description(agent_key: Any) -> str:
    """Return the registry-defined MCP description for one agent, or ""."""
    spec = AGENT_REGISTRY.get(_normalize_agent_key(agent_key))
    if not isinstance(spec, dict):
        return ""
    mcp = spec.get("mcp")
    if not isinstance(mcp, dict):
        return ""
    return str(mcp.get("description") or "").strip()


#: The one placeholder an opening-prompt flag template may carry. It is only
#: ever substituted with :data:`HANDOFF_OPENING_PROMPT` -- a GridVibe constant,
#: never a byte a tool supplied -- and the template is checked for shape exactly
#: like the MCP one.
_OPENING_PROMPT_PLACEHOLDER = "{prompt}"
_OPENING_PROMPT_FLAG_TEMPLATE = re.compile(r"^--?[A-Za-z0-9][A-Za-z0-9_-]*\s\{prompt\}$")

#: A positional prompt goes directly after the binary, before any option.
_OPENING_PROMPT_STYLE_POSITIONAL = "positional"


def _agent_opening_prompt_template(agent_key: Any) -> str:
    """How this CLI takes an opening prompt: ``"{prompt}"``, a flag, or ``""``.

    Only a block the registry marks ``verified`` counts -- the same bar the MCP
    block holds -- and a flag that is not one plain option token followed by
    the placeholder resolves to nothing rather than to something typed.
    """
    spec = AGENT_REGISTRY.get(_normalize_agent_key(agent_key))
    if not isinstance(spec, dict):
        return ""
    block = spec.get("opening_prompt")
    if not isinstance(block, dict) or block.get("verified") is not True:
        return ""
    if str(block.get("style") or "").strip() == _OPENING_PROMPT_STYLE_POSITIONAL:
        return _OPENING_PROMPT_PLACEHOLDER
    flag = str(block.get("flag") or "").strip()
    return flag if _OPENING_PROMPT_FLAG_TEMPLATE.match(flag) else ""


def _agent_accepts_task(agent_key: Any) -> bool:
    """Whether a task can be handed to this CLI at launch.

    Both halves, because the task is *fetched*: the opening prompt only tells
    the agent to call ``read_handoff``, and an agent without GridVibe's tools
    would be told to call a tool it does not have.
    """
    return bool(_agent_opening_prompt_template(agent_key)) and _agent_supports_mcp(agent_key)


def task_capable_agents() -> List[str]:
    """Every registered agent a task can be handed to, for refusal sentences."""
    return sorted(key for key in AGENT_REGISTRY if _agent_accepts_task(key))


def task_refusal(kind: Any, agent_key: Any, mcp: Any = None) -> str:
    """Why a task cannot go to this new or relaunched pane, or ``""``.

    The one owner of the rule, shared by the split intent, the launch and the
    gated relaunch, so the three refuse in the same words. ``mcp`` is the
    caller's *stated* choice: ``None`` means unstated, which a task turns on,
    and an explicit ``False`` is refused rather than silently overridden.
    """
    if str(kind or "").strip().lower() != "agent":
        return (
            "A task is handed to an agent pane: set kind to 'agent' and name "
            "the agent that should carry it out."
        )
    key = _normalize_agent_key(agent_key)
    capable = ", ".join(task_capable_agents()) or "none"
    if not key:
        return (
            "A task is handed to an agent: name the agent that should carry "
            f"it out ({capable})."
        )
    if key not in AGENT_REGISTRY:
        return f"'{key}' is not a known agent CLI. A task can be handed to: {capable}."
    if not _agent_accepts_task(key):
        missing = (
            "publishes no verified way to take an opening prompt"
            if not _agent_opening_prompt_template(key)
            else "cannot be given GridVibe's tools, which is how a task is fetched"
        )
        return f"{key} cannot be handed a task: it {missing}. A task can be handed to: {capable}."
    if mcp is not None and not isinstance(mcp, bool):
        return "mcp must be true or false"
    if mcp is False:
        return (
            "A task is fetched through GridVibe's tools, so the pane needs "
            "them: leave 'mcp' out or set it to true."
        )
    return ""


def _opening_prompt_fragment(agent_key: Any) -> str:
    """The launch-line fragment carrying the constant sentence, or ``""``.

    Double quotes read identically in cmd, PowerShell and POSIX shells here
    because the sentence holds nothing any of them expands (pinned by test).
    """
    template = _agent_opening_prompt_template(agent_key)
    if not template:
        return ""
    return template.replace(_OPENING_PROMPT_PLACEHOLDER, f'"{HANDOFF_OPENING_PROMPT}"')


def launch_line_carries_opening_prompt(command: Any) -> bool:
    """Whether a composed launch line tells its agent to fetch a handoff.

    The startup sequence asks this of the line it is about to type, so a
    handoff is marked announced only when the pointer sentence is really on it.
    """
    return f'"{HANDOFF_OPENING_PROMPT}"' in str(command or "")


def opening_prompt_gap(session: Any, *, tunnelled: bool = False) -> str:
    """Why a pane's launch line carries no opening prompt, in the pane's words.

    Read only after composition left the prompt out, and written to the pane's
    *output* by the startup sequence, never its input.
    """
    if str(getattr(session, "initial_command_mode", "") or "") != "agent":
        return "this pane is no longer set to start an agent"
    agent_key = _normalize_agent_key(getattr(session, "agent_selection", ""))
    base = _normalize_agent_key(getattr(session, "initial_command", ""))
    if not agent_key or base != agent_key:
        return "this pane starts a custom command rather than a registered agent"
    if not _agent_accepts_task(agent_key):
        return f"{agent_key} cannot be handed a task"
    if not bool(getattr(session, "agent_mcp", False)):
        return f"{agent_key} was set to start without GridVibe's tools"
    if bool(getattr(session, CONVERSATION_RESUME_FIELD, False)):
        return f"{agent_key} resumed a saved conversation instead"
    if pane_can_run_the_sidecar(session):
        return "GridVibe's tool config for this machine was not found"
    if not tunnelled:
        return "the SSH tunnel that carries GridVibe's tools could not be opened"
    return f"{agent_key} could not be given GridVibe's tools on this host"


def _pane_shell_family(session: Any) -> str:
    """Which shell family is about to be handed this launch line.

    Mirrors ``terminal_io._local_shell_kind``'s precedence (WSL wins over
    PowerShell) without importing it: ``web.terminal_io`` imports *this*
    module, so the dependency only runs one way.
    """
    from web.mcp_launch import LOCAL_PANE_MODE

    if str(getattr(session, "mode", "") or "") != LOCAL_PANE_MODE:
        return "posix"
    if getattr(session, "use_wsl", False):
        return "posix"
    if os.name != "nt":
        return "posix"
    return "powershell" if getattr(session, "use_powershell", False) else "cmd"


#: What one ``-c`` override cannot be handed to cmd bare. The space is the one
#: that matters -- an interpreter under ``C:/Program Files`` or a checkout with
#: a space in its name -- because cmd passes its command line on and the child's
#: own argv parsing ends the argument there: Codex received two or three
#: unrelated tokens and silently applied none of them. The rest is cmd's own
#: syntax, which would break the line before Codex ever saw it.
_CMD_ARGUMENT_SPECIALS = frozenset(' \t&|<>^()"')


def _toml_override_flag(key: str, value: str, shell_family: str) -> str:
    """One ``-c key=value`` override, quoted for the shell that will read it.

    The value is a TOML *literal* string (single quotes), which processes no
    escapes -- that is what carries a Windows path through unchanged. What
    differs is the shell around it, and the two Windows shells want opposite
    things:

    * **cmd** must see the single quotes bare. Wrapping the pair in double
      quotes there reaches Codex as a value it silently declines to apply --
      no error, no server, which is the worst of the three outcomes.
    * **PowerShell** (and every POSIX shell, which strips double quotes the
      same way) must see the outer double quotes. Bare, PowerShell eats the
      brackets and single quotes itself and Codex exits with *failed to load
      bootstrap configuration* -- costing the pane its agent, not just its
      tools.

    Verified both ways against the installed CLI rather than reasoned about;
    the two shells genuinely disagree and no single string serves both.

    Bare is not *available* for every value, though: an override holding a
    space or any of cmd's own syntax cannot survive the trip as one argument
    at all, so those are quoted. That is not the failure above -- the child's
    argv parsing strips those outer double quotes before Codex parses
    anything, so it reads exactly the string the bare form would have given
    it, which is the one thing a torn-apart argument cannot do.
    """
    override = f"{key}={value}"
    if shell_family != "cmd":
        return f'-c "{override}"'
    if any(char in _CMD_ARGUMENT_SPECIALS for char in override):
        return f'-c "{override}"'
    return f"-c {override}"


def _inline_toml_env_fragment(
    identity: Optional[Mapping[str, str]], shell_family: str
) -> str:
    """State the pane's identity directly, instead of trusting inheritance.

    Every other CLI reaches the sidecar as an ordinary child process and
    inherits the pane's environment two levels down without anything stating
    it. Codex's spawn of an MCP server does not carry the identity variables
    GridVibe injects into the pane's own shell along with it, so a sidecar it
    starts sees an empty ``GRIDVIBE_SESSION_ID`` and ``whoami`` reports
    ``inside_gridvibe: false`` from inside a pane GridVibe plainly started.
    Stating the same five variables as an inline TOML table closes that gap
    without depending on what Codex's own process spawn does or does not
    forward.

    Silently empty exactly like the command/args fragment: a value that
    cannot be written as a TOML literal string costs the identity block, not
    the whole registration.

    No space anywhere in the rendered table -- TOML does not require one
    around ``=`` or after ``,`` in an inline table, and on ``cmd`` this is
    emitted bare whenever it can be. A space there is not a cosmetic choice: it
    would cost the table that bare form and put the whole override behind
    quotes for nothing, where the values GridVibe writes here (ids, a loopback
    URL, a depth) never need it.
    """
    if not identity:
        return ""
    pairs = [(str(key), str(value)) for key, value in identity.items() if value]
    if not pairs:
        return ""
    if any("'" in value or '"' in value for _, value in pairs):
        return ""
    from web.mcp_launch import MCP_SERVER_NAME

    table = ",".join(f"{key}='{value}'" for key, value in sorted(pairs))
    return _toml_override_flag(
        f"mcp_servers.{MCP_SERVER_NAME}.env", f"{{{table}}}", shell_family
    )


def _inline_toml_mcp_fragment(
    config_path: str,
    shell_family: str,
    identity: Optional[Mapping[str, str]] = None,
) -> str:
    """Register the sidecar through ``-c`` overrides instead of a file.

    Codex reads MCP servers from ``~/.codex/config.toml`` and takes no
    "load this file" flag, but every key in that file can be overridden on the
    launch line, and the value is parsed as TOML. So the same command and args
    the generated config holds are stated directly.

    ``identity`` adds a fourth override, the pane's own identity, for the
    reason ``_inline_toml_env_fragment`` states.

    A value containing a quote of either kind cannot be written as a TOML
    literal string, so it resolves to no fragment rather than to a broken
    launch line.
    """
    from web.mcp_launch import MCP_SERVER_NAME, read_mcp_server_block

    if not _MCP_SERVER_NAME.match(MCP_SERVER_NAME):
        return ""
    block = read_mcp_server_block(config_path)
    command = str(block.get("command") or "")
    args = [str(value) for value in block.get("args") or []]
    if not command:
        return ""
    if any("'" in value or '"' in value for value in [command, *args]):
        return ""
    rendered_args = ",".join(f"'{value}'" for value in args)
    fragments = [
        _toml_override_flag(
            f"mcp_servers.{MCP_SERVER_NAME}.command", f"'{command}'", shell_family
        ),
        _toml_override_flag(
            f"mcp_servers.{MCP_SERVER_NAME}.args", f"[{rendered_args}]", shell_family
        ),
    ]
    env_fragment = _inline_toml_env_fragment(identity, shell_family)
    if env_fragment:
        fragments.append(env_fragment)
    return " ".join(fragments)


def _remote_server_name() -> str:
    """The MCP server name, as written into a remote pane's launch line."""
    from web.mcp_launch import MCP_SERVER_NAME

    return MCP_SERVER_NAME


def _agent_mcp_command_fragment(
    agent_key: Any,
    config_path: Optional[str] = None,
    shell_family: str = "",
    *,
    remote_url: str = "",
    identity: Optional[Mapping[str, str]] = None,
) -> str:
    """Return the composed MCP launch fragment for one agent, or "".

    Two shapes, because the CLIs have two. Most take a config *file*
    (``--mcp-config "<path>"``; Copilot's ``@`` marks the argument as a path
    rather than inline JSON), and Codex takes the servers themselves as
    ``-c`` overrides.

    Empty whenever the result cannot be trusted to work: no registry block, no
    generated config on disk (the write failed, or this is a checkout that has
    never been started), or a value that cannot be quoted. Pointing a CLI at a
    config file that is not there costs the user their agent, which is worse
    than quietly having no tools.

    ``remote_url`` switches both to the pane's *own* host: ``config_path`` then
    names a file written there over SFTP, and is not checked for existence
    because the check would ask this filesystem about a file on another one.
    Codex takes the URL directly instead -- inlining a file it cannot read
    would be pointless, and inlining *this* machine's interpreter into a line
    the remote host runs would be wrong.

    ``identity`` is only ever used on the local, inline-TOML path: a remote
    pane's tools arrive over its own SSH reverse tunnel, whose config the
    remote-side sidecar already carries, and every file-based CLI reaches the
    sidecar by ordinary process inheritance with nothing extra to state.
    """
    from web.mcp_launch import mcp_config_path

    remote = bool(str(remote_url or "").strip())
    if remote:
        resolved = str(config_path or "")
        if not resolved or '"' in resolved or "'" in str(remote_url):
            return ""
    else:
        resolved = str(config_path if config_path is not None else mcp_config_path())
        if not resolved or '"' in resolved or not os.path.exists(resolved):
            return ""

    if _agent_mcp_style(agent_key) == _MCP_STYLE_INLINE_TOML:
        if remote:
            # One key, not three: a streamable-HTTP server is a URL, and the
            # sidecar it would otherwise name does not exist on that host.
            return _toml_override_flag(
                f"mcp_servers.{_remote_server_name()}.url",
                f"'{remote_url}'",
                shell_family,
            )
        return _inline_toml_mcp_fragment(resolved, shell_family, identity)

    template = _agent_mcp_flag(agent_key)
    if not template:
        return ""
    # The quote opens *before* any marker, never after it: `@"C:\..."` starts a
    # here-string in PowerShell and fails to parse, while `"@C:\..."` is an
    # ordinary quoted argument in cmd, PowerShell and POSIX shells alike, and
    # reaches the CLI as the `@path` it asked for.
    marked = f"{_MCP_MARKER_PLACEHOLDER}" in template
    placeholder = _MCP_MARKER_PLACEHOLDER if marked else _MCP_CONFIG_PLACEHOLDER
    value = f'"@{resolved}"' if marked else f'"{resolved}"'
    return template.replace(placeholder, value)


def _agent_options() -> List[Dict[str, str]]:
    """Return launcher agent choices sourced from the registry."""
    options = [
        {
            "value": key,
            "label": str(spec.get("label") or key),
            # The pane header's relaunch menu names an agent in prose rather
            # than by its binary, so the registry's display name travels with
            # the option instead of being looked up a second time client-side.
            "display_name": str(spec.get("display_name") or spec.get("label") or key),
            "auto_mode_flag": _agent_auto_mode_flag(key),
            "auto_mode_description": _agent_auto_mode_description(key),
            # Published for the same reason as the auto-mode pair: the launcher
            # hides the checkbox for an agent that publishes no flag.
            "mcp_flag": _agent_mcp_flag(key),
            "mcp_description": _agent_mcp_description(key),
            # The question the checkbox actually asks. Not `mcp_flag` truthiness:
            # Codex supports MCP and publishes no flag string, because its
            # servers ride in as `-c` overrides composed at launch.
            "mcp_supported": _agent_supports_mcp(key),
            # Whether a tool may hand this agent a task at launch: the same
            # fact the tool descriptions state and the routes refuse on.
            "opening_prompt_supported": _agent_accepts_task(key),
            # The pane menu's update button runs this ahead of the agent; an
            # agent that publishes none gets no button.
            "update_command": _agent_update_command(key),
        }
        for key, spec in AGENT_REGISTRY.items()
    ]
    options.sort(key=lambda item: item["label"])
    options.append(
        {
            "value": "other",
            "label": "other",
            "display_name": "other",
            "auto_mode_flag": "",
            "auto_mode_description": "",
            "mcp_flag": "",
            "mcp_description": "",
            "mcp_supported": False,
            "opening_prompt_supported": False,
            "update_command": "",
        }
    )
    return options


def _compose_agent_startup_command(
    session: Any,
    remote_config_path: str = "",
    remote_url: str = "",
    *,
    identity: Optional[Mapping[str, str]] = None,
    session_hook_settings: str = "",
    opening_prompt: bool = False,
) -> str:
    """Apply launch-only title settings and optional auto-mode flags.

    Keep the persisted base command intact for detection and saved sessions.
    Custom or explicitly configured commands remain verbatim.

    ``remote_config_path``/``remote_url`` are the SSH tunnel's, handed in by
    the startup sequence that opened it. They are passed rather than read
    here because they belong to one *connection*, not to the pane record: a
    relaunch gets a new port, a new token and a freshly written file.

    ``identity`` is this pane's own five ``GRIDVIBE_*`` values, only reached
    for a local pane -- see ``_inline_toml_env_fragment``.

    ``session_hook_settings`` is the generated Claude settings file, handed in
    only for a connection whose token lets its session hook report home -- see
    ``web/agent_session_hooks.py``. Like the tunnel's pair it belongs to the
    connection, so it is passed rather than read here.

    ``opening_prompt`` asks for :data:`HANDOFF_OPENING_PROMPT`, because this
    connection's pane holds a handed-over task. It belongs to the connection
    for the same reason -- a relaunch never replays it -- and it is placed only
    beside a non-empty MCP fragment (an agent told to call a tool it does not
    have is the outcome this prevents) and never beside a resume. It goes
    directly after the binary: Claude's ``--mcp-config`` takes a variable
    number of values, so a prompt appended at the end would be read as a
    second config path.
    """
    base = str(getattr(session, "initial_command", "") or "").strip()
    if not base:
        return base
    if str(getattr(session, "initial_command_mode", "") or "") != "agent":
        return base
    agent_key = _normalize_agent_key(getattr(session, "agent_selection", "")) or _normalize_agent_key(base)
    if _normalize_agent_key(base) != agent_key:
        # Custom or already-modified commands launch verbatim.
        return base
    # Which shell reads this line changes how a `-c` override has to be
    # quoted, and the two Windows shells want opposite things -- see
    # `_toml_override_flag`. Read once here, for every override below.
    shell_family = _pane_shell_family(session)
    command = compose_conversation_command(
        base,
        getattr(session, CONVERSATION_PROVIDER_FIELD, ""),
        getattr(session, CONVERSATION_ID_FIELD, ""),
        getattr(session, CONVERSATION_RESUME_FIELD, False),
        shell_family,
        AGENT_REGISTRY,
    )
    # Known from what was just composed rather than parsed back out of the
    # finished line: a resume is exactly a changed line with the resume flag
    # set (a fresh Claude `--session-id` is a create, and takes a prompt).
    resumed = command != base and bool(getattr(session, CONVERSATION_RESUME_FIELD, False))
    mcp_fragment_placed = False
    if agent_key == "codex":
        # Launch-only override: the CLI's default title contains the project,
        # while thread-title follows the active conversation and /rename.
        command += " " + _toml_override_flag(
            "tui.terminal_title", "['thread-title']", shell_family
        )
    if agent_key == "claude" and session_hook_settings:
        # Launch-only, additive: `--settings` merges with the user's own
        # settings, so their hooks still run beside this one.
        fragment = claude_settings_fragment(session_hook_settings)
        if fragment:
            command += f" {fragment}"
    if bool(getattr(session, "agent_auto_mode", False)):
        flag = _agent_auto_mode_flag(agent_key)
        if flag:
            command += f" {flag}"
    if bool(getattr(session, "agent_mcp", False)):
        # Additive by construction: `--mcp-config` loads *alongside* the user's
        # own MCP servers. The strict variant would silently cost them every
        # server they had registered, inside GridVibe panes only.
        #
        # A local pane names the generated config on this machine. A remote
        # pane names what its own tunnel wrote on *its* host -- same flag, a
        # path that resolves where the line is actually typed. A remote pane
        # with no tunnel gets nothing rather than a path it cannot read: that
        # costs the tools, never the agent.
        if pane_can_run_the_sidecar(session):
            fragment = _agent_mcp_command_fragment(
                agent_key, shell_family=shell_family, identity=identity
            )
        elif remote_config_path:
            fragment = _agent_mcp_command_fragment(
                agent_key,
                remote_config_path,
                shell_family="posix",
                remote_url=remote_url or "",
            )
        else:
            fragment = ""
        if fragment:
            command += f" {fragment}"
            mcp_fragment_placed = True
    if (
        opening_prompt
        and mcp_fragment_placed
        and not resumed
        and command.startswith(base)
    ):
        prompt = _opening_prompt_fragment(agent_key)
        if prompt:
            command = f"{base} {prompt}{command[len(base):]}"
    return command


def _normalize_agent_key(value: Any) -> str:
    """Normalize an agent registry key."""
    return str(value or "").strip().lower()


def _normalize_port_number(value: Any, default: int = 22) -> int:
    """Return a bounded integer port."""
    try:
        port = int(value)
    except (TypeError, ValueError):
        port = default
    return max(1, min(65535, port))


def _normalize_ping_target(value: Any) -> str:
    """Return a host/IP value safe to pass as one subprocess argument."""
    target = str(value or "").strip()
    if not target:
        raise ValueError("Enter an SSH host or IP address before pinging.")
    if len(target) > 253:
        raise ValueError("SSH host is too long.")
    if any(character.isspace() for character in target):
        raise ValueError("SSH host cannot contain spaces.")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", target):
        raise ValueError("SSH host contains unsupported characters.")
    return target


def _ping_command(target: str) -> List[str]:
    """Build the local OS ping command for a single quick probe."""
    if os.name == "nt":
        return ["ping", "-n", "1", "-w", "3000", target]
    return ["ping", "-c", "1", "-W", "3", target]


def _parse_ping_latency_ms(output: str) -> Optional[float]:
    """Extract a representative ping latency from common ping output."""
    match = re.search(r"time[=<]\s*([0-9]+(?:\.[0-9]+)?)\s*ms", output, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return round(float(match.group(1)), 2)
    except ValueError:
        return None


def _tcp_probe_target(target: str, port: int, timeout: float = 3.0) -> Dict[str, Any]:
    """Check whether the SSH TCP port accepts a connection."""
    start = time.monotonic()
    try:
        with socket.create_connection((target, port), timeout=timeout):
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            return {
                "reachable": True,
                "method": "tcp",
                "latency_ms": latency_ms,
                "message": f"Reached {target}:{port} over TCP in {latency_ms:.0f} ms.",
            }
    except OSError as exc:
        return {
            "reachable": False,
            "method": "tcp",
            "latency_ms": None,
            "message": f"Could not reach {target}:{port}: {exc}",
        }


def _ping_ssh_target(target: Any, port: Any = 22) -> Dict[str, Any]:
    """Ping a launcher SSH target, falling back to a TCP SSH-port probe."""
    normalized_target = _normalize_ping_target(target)
    normalized_port = _normalize_port_number(port, 22)
    ping_executable = shutil.which("ping")

    if ping_executable:
        command = [ping_executable, *_ping_command(normalized_target)[1:]]
        try:
            start = time.monotonic()
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
                check=False,
            )
            elapsed_ms = round((time.monotonic() - start) * 1000, 2)
            output = f"{result.stdout}\n{result.stderr}".strip()
            latency_ms = _parse_ping_latency_ms(output) or elapsed_ms
            if result.returncode == 0:
                return {
                    "reachable": True,
                    "method": "icmp",
                    "target": normalized_target,
                    "port": normalized_port,
                    "latency_ms": latency_ms,
                    "message": f"Ping reached {normalized_target} in {latency_ms:.0f} ms.",
                }
            tcp_result = _tcp_probe_target(normalized_target, normalized_port)
            return {
                **tcp_result,
                "target": normalized_target,
                "port": normalized_port,
                "ping_error": output,
            }
        except (OSError, subprocess.SubprocessError) as exc:
            tcp_result = _tcp_probe_target(normalized_target, normalized_port)
            return {
                **tcp_result,
                "target": normalized_target,
                "port": normalized_port,
                "ping_error": str(exc),
            }

    tcp_result = _tcp_probe_target(normalized_target, normalized_port)
    return {
        **tcp_result,
        "target": normalized_target,
        "port": normalized_port,
        "ping_error": "Local ping command is unavailable.",
    }


def _powershell_single_quote(value: Any) -> str:
    """Escape a string for use inside a PowerShell single-quoted literal."""
    return "'" + str(value or "").replace("'", "''") + "'"


def _shell_single_quote(value: Any) -> str:
    """Escape a string for use inside a POSIX single-quoted literal."""
    return "'" + str(value or "").replace("'", "'\"'\"'") + "'"


def _contains_html_payload(value: Any) -> bool:
    """Return True when captured command output looks like an HTML page."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        return False

    return any(
        marker in normalized
        for marker in (
            "<!doctype html",
            "<html",
            "<title>just a moment",
            "enable javascript and cookies to continue",
        )
    )


def _build_posix_detection_command(binary: str) -> str:
    """Build a shell probe that detects files, aliases, and shell functions."""
    binary_literal = _shell_single_quote(binary)
    fast_script = (
        f"TF_BINARY={binary_literal}; "
        'if command -v "$TF_BINARY" >/dev/null 2>&1; then '
        'TF_PATH=$(command -v "$TF_BINARY" 2>/dev/null || true); '
        'TF_HEAD=""; '
        'if [ -n "$TF_PATH" ] && [ -f "$TF_PATH" ]; then '
        "TF_HEAD=$(LC_ALL=C head -c 256 \"$TF_PATH\" 2>/dev/null | tr '\\n' ' ' || true); "
        'fi; '
        "printf '__TF_FOUND__\n'; "
        "printf '__TF_KIND__:%s\n' file; "
        "printf '__TF_PATH__:%s\n' \"$TF_PATH\"; "
        "printf '__TF_HEAD__:%s\n' \"$TF_HEAD\"; "
        "exit 0; "
        "fi"
    )
    bash_script = (
        f"TF_BINARY={binary_literal}; "
        'if ! type "$TF_BINARY" >/dev/null 2>&1; then exit 1; fi; '
        'TF_KIND=$(type -t "$TF_BINARY" 2>/dev/null || printf file); '
        'TF_PATH=$(type -P "$TF_BINARY" 2>/dev/null || command -v "$TF_BINARY" 2>/dev/null || true); '
        "TF_HEAD=''; "
        'if [ -n "$TF_PATH" ] && [ -f "$TF_PATH" ]; then '
        "TF_HEAD=$(LC_ALL=C head -c 256 \"$TF_PATH\" 2>/dev/null | tr '\\n' ' ' || true); "
        'fi; '
        "printf '__TF_FOUND__\n'; "
        "printf '__TF_KIND__:%s\n' \"$TF_KIND\"; "
        "printf '__TF_PATH__:%s\n' \"$TF_PATH\"; "
        "printf '__TF_HEAD__:%s\n' \"$TF_HEAD\""
    )
    sh_script = (
        f"TF_BINARY={binary_literal}; "
        'if ! command -v "$TF_BINARY" >/dev/null 2>&1; then exit 1; fi; '
        'TF_PATH=$(command -v "$TF_BINARY" 2>/dev/null || true); '
        "TF_HEAD=''; "
        'if [ -n "$TF_PATH" ] && [ -f "$TF_PATH" ]; then '
        "TF_HEAD=$(LC_ALL=C head -c 256 \"$TF_PATH\" 2>/dev/null | tr '\\n' ' ' || true); "
        'fi; '
        "printf '__TF_FOUND__\n'; "
        "printf '__TF_KIND__:%s\n' file; "
        "printf '__TF_PATH__:%s\n' \"$TF_PATH\"; "
        "printf '__TF_HEAD__:%s\n' \"$TF_HEAD\""
    )
    return (
        f"{fast_script}; "
        'if command -v bash >/dev/null 2>&1; then '
        f'bash -ilc {_shell_single_quote(bash_script)}; '
        'else '
        f'sh -lc {_shell_single_quote(sh_script)}; '
        'fi'
    )


def _build_login_shell_detection_command(binary: str) -> str:
    """Build a probe that runs inside the user's login shell when possible."""
    binary_literal = _shell_single_quote(binary)
    posix_probe = (
        'if ! command -v "$TF_BINARY" >/dev/null 2>&1; then exit 1; fi; '
        'TF_PATH=$(command -v "$TF_BINARY" 2>/dev/null || true); '
        'TF_KIND=""; '
        'case "$TF_PATH" in '
        'alias\\ *) TF_KIND=alias; TF_PATH="" ;; '
        'esac; '
        'if [ -z "$TF_KIND" ] && [ "$TF_PATH" = "$TF_BINARY" ]; then TF_KIND=builtin; TF_PATH=""; fi; '
        "TF_HEAD=''; "
        'if [ -n "$TF_PATH" ] && [ -f "$TF_PATH" ]; then '
        "TF_HEAD=$(LC_ALL=C head -c 256 \"$TF_PATH\" 2>/dev/null | tr '\\n' ' ' || true); "
        'fi; '
        "printf '__TF_FOUND__\n'; "
        "printf '__TF_KIND__:%s\n' \"$TF_KIND\"; "
        "printf '__TF_PATH__:%s\n' \"$TF_PATH\"; "
        "printf '__TF_HEAD__:%s\n' \"$TF_HEAD\""
    )
    posix_command = f"TF_BINARY={binary_literal}; {posix_probe}"
    fish_command = f"env TF_BINARY={binary_literal} /bin/sh -lc {_shell_single_quote(posix_probe)}"
    return (
        'TF_LOGIN_SHELL=$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f7 | head -n 1); '
        '[ -n "$TF_LOGIN_SHELL" ] || TF_LOGIN_SHELL="${SHELL:-/bin/sh}"; '
        '[ -x "$TF_LOGIN_SHELL" ] || TF_LOGIN_SHELL=/bin/sh; '
        'TF_LOGIN_NAME=$(basename "$TF_LOGIN_SHELL"); '
        'case "$TF_LOGIN_NAME" in '
        f'fish) exec "$TF_LOGIN_SHELL" -ilc {_shell_single_quote(fish_command)} ;; '
        f'bash|zsh|ksh) exec "$TF_LOGIN_SHELL" -ilc {_shell_single_quote(posix_command)} ;; '
        f'*) exec "$TF_LOGIN_SHELL" -lc {_shell_single_quote(posix_command)} ;; '
        'esac'
    )


def _parse_posix_detection_output(
    binary: str,
    stdout: str,
    command_label: str,
    stderr: str = "",
    returncode: int = 0,
) -> Dict[str, Any]:
    """Normalize shell detection output from POSIX, WSL, and SSH probes."""
    metadata: Dict[str, str] = {}
    found_marker = False

    for raw_line in str(stdout or "").splitlines():
        line = raw_line.strip()
        if line == "__TF_FOUND__":
            found_marker = True
            continue
        if line.startswith("__TF_KIND__:"):
            metadata["kind"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("__TF_PATH__:"):
            metadata["path"] = line.split(":", 1)[1].strip()
            continue
        if line.startswith("__TF_HEAD__:"):
            metadata["head"] = line.split(":", 1)[1].strip()

    resolved = metadata.get("path", "")
    kind = metadata.get("kind", "")
    lowered_resolved = resolved.lower()
    if not kind and lowered_resolved.startswith("alias "):
        kind = "alias"
        resolved = ""
    elif not kind and "function" in lowered_resolved:
        kind = "function"
        resolved = ""
    elif not kind and resolved == binary:
        kind = "builtin"
        resolved = ""
    display_path = resolved or (f"{kind} {binary}" if kind in {"alias", "function", "builtin"} else "")

    if found_marker:
        if _contains_html_payload(metadata.get("head", "")):
            return {
                "found": False,
                "path": display_path,
                "command": command_label,
                "error": f"{binary} resolves to an HTML page instead of a working CLI.",
                "failed": True,
                "kind": kind,
            }
        return {
            "found": True,
            "path": display_path,
            "command": command_label,
            "error": "",
            "kind": kind,
        }

    return {
        "found": False,
        "path": "",
        "command": command_label,
        "error": str(stderr or "").strip() if returncode != 0 else "",
    }


def _agent_target_label(target: Dict[str, Any]) -> str:
    """Return a short human-readable target label."""
    environment_key = target.get("environment_key")
    shell_kind = target.get("shell_kind")
    if environment_key == "ssh":
        host = str(target.get("host") or "").strip()
        return f"SSH {host}" if host else "SSH"
    if environment_key == "windows_native":
        if shell_kind == "powershell":
            return "PowerShell"
        if shell_kind == "cmd":
            return "cmd"
        return "Windows"
    if shell_kind == "wsl":
        distribution = str(target.get("distribution") or "").strip()
        return f"WSL {distribution}" if distribution else "WSL"
    return "local shell"


def _resolve_preflight_wsl_distribution(value: Any) -> str:
    """Return the user-configured WSL distro for agent preflight, unmodified."""
    return str(value or "").strip()


def _resolve_agent_target(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve the requested preflight environment from launcher form state."""
    connection_mode = _normalize_connection_mode(payload.get("connection_mode"))
    ssh_data = payload.get("ssh") if isinstance(payload.get("ssh"), dict) else {}
    wsl_data = payload.get("wsl") if isinstance(payload.get("wsl"), dict) else {}
    terminal = payload.get("terminal") if isinstance(payload.get("terminal"), dict) else {}
    use_powershell = bool(terminal.get("use_powershell"))
    use_wsl = bool(terminal.get("use_wsl")) and not use_powershell

    if connection_mode == "ssh":
        return {
            "connection_mode": "ssh",
            "environment_key": "ssh",
            "shell_kind": "ssh",
            "host": str(ssh_data.get("host") or "").strip(),
            "username": str(ssh_data.get("username") or "ubuntu").strip() or "ubuntu",
            "password": str(ssh_data.get("password") or ""),
            "port": _normalize_port_number(ssh_data.get("port"), 22),
        }

    configured_distribution = str(terminal.get("distribution") or wsl_data.get("distribution") or "").strip()
    distribution = _resolve_preflight_wsl_distribution(configured_distribution) if use_wsl else configured_distribution
    username = str(wsl_data.get("username") or "").strip()
    if os.name != "nt":
        return {
            "connection_mode": connection_mode,
            "environment_key": "wsl_linux",
            "shell_kind": "wsl" if use_wsl else "posix",
            "distribution": distribution,
            "username": username,
        }

    if use_wsl:
        return {
            "connection_mode": connection_mode,
            "environment_key": "wsl_linux",
            "shell_kind": "wsl",
            "distribution": distribution,
            "username": username,
        }

    return {
        "connection_mode": connection_mode,
        "environment_key": "windows_native",
        "shell_kind": "powershell" if use_powershell else "cmd",
        "distribution": distribution,
        "username": username,
    }


def _detect_windows_command(binary: str, shell_kind: str = "powershell") -> Dict[str, Any]:
    """Detect a command in native Windows shells.

    The probe is the actual pane's shell family, not always PowerShell. A CLI
    installed only through a cmd-specific mechanism -- an ``AutoRun`` registry
    hook, a batch-file PATH shim, anything that never touched a PowerShell
    ``$PROFILE`` -- is genuinely absent from a fresh ``-NoProfile`` PowerShell
    subprocess even though the cmd pane about to open can run it fine. Probing
    through PowerShell regardless of ``shell_kind`` reported that pane's own
    agent as not installed and silently opened it as a plain shell (Guardrail
    2.1's Windows counterpart: the check must answer for the shell that will
    actually run the command, not a stand-in for it).

    cmd's own bare-command resolution is what ``where`` implements, and
    ``cmd.exe /c`` still runs ``AutoRun`` by default (only ``/D`` disables it),
    so this sees exactly what typing the binary at that same cmd prompt would.
    """
    if os.name != "nt":
        resolved = shutil.which(binary)
        return {
            "found": bool(resolved),
            "path": resolved or "",
            "command": f"command -v {binary}",
            "error": "",
        }

    if str(shell_kind or "").strip().lower() == "cmd":
        command_label = f"where {binary}"
        try:
            result = subprocess.run(
                ["cmd.exe", "/c", "where", binary],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "found": False,
                "path": "",
                "command": command_label,
                "error": str(exc),
                "failed": True,
            }
        # `where` lists every match, one per line; the first is what a bare
        # invocation at the prompt would actually run.
        resolved = next(
            (line.strip() for line in (result.stdout or "").splitlines() if line.strip()),
            "",
        )
        return {
            "found": bool(resolved) and result.returncode == 0,
            "path": resolved,
            "command": command_label,
            "error": (result.stderr or "").strip() if result.returncode != 0 else "",
        }

    command_label = f"Get-Command {binary} -ErrorAction SilentlyContinue"
    script = (
        f"$cmd = Get-Command {_powershell_single_quote(binary)} -ErrorAction SilentlyContinue; "
        f"if ($cmd) {{ $cmd.Source }}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": str(exc),
            "failed": True,
        }

    resolved = (result.stdout or "").strip()
    return {
        "found": bool(resolved),
        "path": resolved,
        "command": command_label,
        "error": (result.stderr or "").strip() if result.returncode != 0 else "",
    }


def _detect_posix_command(binary: str) -> Dict[str, Any]:
    """Detect a command in the current POSIX shell."""
    command_label = f"type {binary}"
    probe_command = _build_posix_detection_command(binary)
    try:
        result = subprocess.run(
            ["sh", "-lc", probe_command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": str(exc),
            "failed": True,
        }

    return _parse_posix_detection_output(
        binary,
        result.stdout or "",
        command_label,
        stderr=result.stderr or "",
        returncode=result.returncode,
    )


def _detect_wsl_command(binary: str, distribution: str = "") -> Dict[str, Any]:
    """Detect a command inside a local WSL environment."""
    command_label = f"type {binary}"
    if os.name != "nt":
        return _detect_posix_command(binary)

    wsl_executable = _find_wsl_executable()
    if not wsl_executable:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": "WSL is not available on this system.",
            "failed": True,
        }

    command = [wsl_executable]
    if distribution:
        command.extend(["--distribution", distribution])
    # --exec bypasses wsl.exe's intermediate shell, preventing premature $VAR expansion
    command.extend(["--exec", "bash", "-lc", _build_login_shell_detection_command(binary)])

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=8,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": str(exc),
            "failed": True,
        }

    # bash --exec login shells emit "logout" to stderr on exit — filter it out
    stderr = result.stderr or ""
    if stderr.strip().lower() == "logout":
        stderr = ""

    return _parse_posix_detection_output(
        binary,
        result.stdout or "",
        command_label,
        stderr=stderr,
        returncode=result.returncode,
    )


def _detect_ssh_command(binary: str, target: Dict[str, Any]) -> Dict[str, Any]:
    """Detect a command on a remote SSH host."""
    host = str(target.get("host") or "").strip()
    username = str(target.get("username") or "").strip()
    port = _normalize_port_number(target.get("port"), 22)
    command_label = f"type {binary}"
    if not host or not username:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": "Enter an SSH host and username to inspect the remote CLI.",
            "incomplete": True,
        }

    if paramiko is None:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": "Remote CLI detection is unavailable because Paramiko is not installed.",
            "failed": True,
        }

    client = None
    try:
        client = paramiko.SSHClient()
        _apply_host_key_policy(client, paramiko)
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=target.get("password") or None,
            timeout=min(int(runtime_config.ssh_config.get("connection_timeout", 30)), 5),
            auth_timeout=5,
            banner_timeout=5,
            look_for_keys=not bool(target.get("password")),
            allow_agent=not bool(target.get("password")),
        )
        _, stdout, stderr = client.exec_command(
            _build_login_shell_detection_command(binary),
            timeout=8,
        )
        exit_status = stdout.channel.recv_exit_status()
        return _parse_posix_detection_output(
            binary,
            stdout.read().decode("utf-8", errors="ignore"),
            command_label,
            stderr=stderr.read().decode("utf-8", errors="ignore"),
            returncode=exit_status,
        )
    except Exception as exc:
        return {
            "found": False,
            "path": "",
            "command": command_label,
            "error": str(exc),
            "failed": True,
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def _detect_agent_binary(target: Dict[str, Any], binary: str) -> Dict[str, Any]:
    """Detect whether an agent binary exists in the resolved environment."""
    environment_key = target.get("environment_key")
    if environment_key == "ssh":
        return _detect_ssh_command(binary, target)
    if environment_key == "windows_native":
        return _detect_windows_command(binary, str(target.get("shell_kind") or "powershell"))
    return _detect_wsl_command(binary, str(target.get("distribution") or "").strip())


def _agent_detection_cache_key(
    target: Dict[str, Any],
    binary: str,
) -> Tuple[str, str, str, str, str, int]:
    """Build a stable cache key for one agent binary detection target."""
    return (
        str(binary or "").strip(),
        str(target.get("environment_key") or ""),
        str(target.get("shell_kind") or ""),
        str(target.get("distribution") or "").strip(),
        str(target.get("host") or "").strip(),
        _normalize_port_number(target.get("port"), 22),
    )


def _detect_agent_binary_cached(target: Dict[str, Any], binary: str) -> Dict[str, Any]:
    """Detect an agent binary while reusing recent identical probe results."""
    if target.get("environment_key") == "ssh":
        return _detect_agent_binary(target, binary)

    key = _agent_detection_cache_key(target, binary)
    now = time.monotonic()

    with _agent_detection_cache_lock:
        cached = _agent_detection_cache.get(key)
        if cached is not None:
            cached_at, cached_payload = cached
            if now - cached_at <= _AGENT_DETECTION_CACHE_TTL_SECONDS:
                return dict(cached_payload)
            _agent_detection_cache.pop(key, None)

    # Probe outside the lock: the subprocess can take up to ~8s (WSL) and must
    # not serialize concurrent preflights for other agents/rows. Duplicate
    # concurrent probes for the same key are idempotent and cached afterwards.
    detection = _detect_agent_binary(target, binary)
    with _agent_detection_cache_lock:
        _agent_detection_cache[key] = (time.monotonic(), dict(detection))
    return detection


def _check_install_requirement(requirement: Dict[str, Any], target: Dict[str, Any]) -> Tuple[bool, str]:
    """Return whether one install prerequisite is available."""
    kind = str(requirement.get("kind") or "").strip().lower()
    message = str(requirement.get("message") or "Missing prerequisite.").strip()
    if kind == "wsl":
        return bool(_find_wsl_executable()), message

    if kind == "command":
        binary = str(requirement.get("binary") or "").strip()
        if not binary:
            return True, ""
        if target.get("environment_key") in {"ssh", "wsl_linux"}:
            return True, ""
        detected = _detect_agent_binary_cached(
            {
                **target,
                "environment_key": target.get("environment_key"),
            },
            binary,
        )
        return bool(detected.get("found")), message

    return True, ""


def _select_install_option(environment_spec: Dict[str, Any], target: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Pick the first install option whose prerequisites are satisfied."""
    options = environment_spec.get("install_options")
    if not isinstance(options, list) or not options:
        return None, []

    missing_messages: List[str] = []
    for option in options:
        if not isinstance(option, dict):
            continue
        requirements = option.get("requires")
        unmet_for_option: List[str] = []
        for requirement in requirements if isinstance(requirements, list) else []:
            if not isinstance(requirement, dict):
                continue
            available, message = _check_install_requirement(requirement, target)
            if not available and message:
                unmet_for_option.append(message)
        if not unmet_for_option:
            return option, []
        if not missing_messages:
            missing_messages = unmet_for_option

    first_option = next((option for option in options if isinstance(option, dict)), None)
    return first_option, missing_messages


def _agent_status_label(status: str) -> str:
    """Map internal preflight states to short UI labels."""
    return {
        "installed": "Installed",
        "missing": "Missing",
        "unsupported_here": "Unsupported here",
        "missing_prerequisite": "Missing prerequisite",
        "needs_manual_install": "Manual install",
        "target_incomplete": "Awaiting target",
        "check_failed": "Check failed",
    }.get(status, "Unknown")


def _build_agent_preflight_request(agent_key: str, connection_mode: str, session_config: Dict[str, Any]) -> Dict[str, Any]:
    """Build one agent preflight request from a prepared session payload."""
    normalized_mode = _normalize_connection_mode(connection_mode)
    session_data = session_config if isinstance(session_config, dict) else {}
    return {
        "agent": agent_key,
        "connection_mode": normalized_mode,
        "ssh": {
            "host": str(session_data.get("host") or "").strip(),
            "username": str(session_data.get("username") or "ubuntu").strip() or "ubuntu",
            "password": str(session_data.get("password") or ""),
            "port": _normalize_port_number(session_data.get("port"), 22),
        },
        "wsl": {
            "distribution": str(session_data.get("distribution") or "").strip(),
            "username": str(session_data.get("username") or "").strip(),
            "default_dir": str(session_data.get("directory") or "").strip(),
        },
        "terminal": {
            "distribution": str(session_data.get("distribution") or "").strip(),
            "use_wsl": bool(session_data.get("use_wsl")),
            "use_powershell": bool(session_data.get("use_powershell")),
        },
    }


#: Preflight verdicts that mean the binary is not there to be started.
#:
#: Distinct from ``check_failed``, which means the *check* did not run and says
#: nothing about the binary. Both cost the pane its agent identity; only
#: ``check_failed`` costs it the command as well.
AGENT_PREFLIGHT_ABSENT_STATUSES = frozenset(
    {"missing", "needs_manual_install", "missing_prerequisite", "unsupported_here"}
)


def _clear_agent_launch_identity(session: Dict[str, Any]) -> None:
    """Make this a plain terminal pane. Its startup command is left alone."""
    session["initial_command_mode"] = "command"
    session["startup_mode"] = "terminal"
    session["agent_selection"] = ""
    session["custom_agent"] = ""
    session["agent_auto_mode"] = False
    session["agent_mcp"] = False
    session.update(EMPTY_CONVERSATION_FIELDS)


def _sanitize_agent_launch_commands(connection_mode: str, sessions: List[Dict[str, Any]]) -> List[str]:
    """Answer the preflight before the pane opens, on two separate questions.

    **Can the check run?** ``check_failed`` means it could not, so the command
    is cleared as it always has been.

    **Is the binary there?** A fresh probe saying it is not is the earliest and
    most reliable moment GridVibe ever knows the pane will not run an agent --
    earlier than any signal the pane itself can give, because the pane's own
    answer is a shell prompt that arrives before its output has even been read.
    So the pane stops being an agent here, in its header and on the dashboard,
    while the command is **kept**: the reader still gets the real error in the
    terminal, which is what tells them what to install, and a probe that turns
    out to have been wrong costs a label rather than a launch. The launcher has
    already shown this same verdict beside the row, so nothing here is news.

    Either way the identity goes as one unit. Leaving it behind produced a pane
    that opened a plain shell and was still *called* Codex -- the same defect as
    an agent pane that outlives its agent, arriving before the pane has started.
    """
    normalized_mode = _normalize_connection_mode(connection_mode)
    warnings: List[str] = []
    for index, session in enumerate(sessions):
        initial_command = str(session.get("initial_command") or "").strip()
        agent_key = _normalize_agent_key(initial_command)
        if agent_key not in AGENT_REGISTRY:
            continue

        preflight = _agent_preflight_payload(
            agent_key,
            _build_agent_preflight_request(agent_key, normalized_mode, session),
        )
        status = str(preflight.get("status") or "")
        if status not in AGENT_PREFLIGHT_ABSENT_STATUSES and status != "check_failed":
            continue

        title = str(session.get("title") or f"Terminal {index + 1}").strip() or f"Terminal {index + 1}"
        message = str(preflight.get("message") or "Agent preflight failed.")
        if status == "check_failed":
            warning = f"{title}: {message} Startup command cleared."
            logger.warning("Clearing startup command because agent preflight failed: %s", warning)
            session["initial_command"] = ""
        else:
            warning = f"{title}: {message} Opened as a terminal."
            logger.info("Clearing agent identity because the binary is absent: %s", warning)
        _clear_agent_launch_identity(session)
        warnings.append(warning)

    return warnings


def _agent_absent_reason(
    agent_key: Any,
    connection_mode: Any,
    session_config: Dict[str, Any],
) -> str:
    """Return why this agent cannot start in that environment, or ``""``.

    The launcher's own check, asked as the one true/false question a caller
    with a pane already open needs: *is the binary there?* It is the same
    registry-driven probe behind ``_sanitize_agent_launch_commands()``, reading
    the same ``AGENT_PREFLIGHT_ABSENT_STATUSES``, so the pane menu and the
    launcher row cannot answer it differently.

    ``check_failed`` is deliberately not an absence. The check did not run, so
    it says nothing about the binary, and a caller that refused on it would
    turn a broken probe into a pane that cannot be relaunched at all. Only a
    verdict that the binary is *not there* comes back as a reason.
    """
    normalized_key = _normalize_agent_key(agent_key)
    if normalized_key not in AGENT_REGISTRY:
        return ""

    preflight = _agent_preflight_payload(
        normalized_key,
        _build_agent_preflight_request(
            normalized_key,
            connection_mode,
            session_config if isinstance(session_config, dict) else {},
        ),
    )
    if str(preflight.get("status") or "") not in AGENT_PREFLIGHT_ABSENT_STATUSES:
        return ""
    return str(preflight.get("message") or f"{normalized_key} is not available here.")


def _agent_preflight_payload(agent_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Build a registry-driven agent preflight response."""
    spec = AGENT_REGISTRY.get(agent_key)
    if not isinstance(spec, dict):
        raise ValueError("Unknown agent selection")

    target = _resolve_agent_target(payload)
    environment_key = str(target.get("environment_key") or "")
    environments = spec.get("environments") if isinstance(spec.get("environments"), dict) else {}
    environment_spec = environments.get(environment_key) if isinstance(environments.get(environment_key), dict) else {}
    label = str(spec.get("display_name") or spec.get("label") or agent_key)
    binary = str(spec.get("binary") or agent_key)
    verify = spec.get("verify") if isinstance(spec.get("verify"), list) else []
    post_install = str(spec.get("post_install") or "").strip()

    response = {
        "agent": agent_key,
        "label": label,
        "binary": binary,
        "status": "unsupported_here",
        "status_label": _agent_status_label("unsupported_here"),
        "message": f"{label} is not supported in {_agent_target_label(target)}.",
        "warning": str(environment_spec.get("warning") or "").strip(),
        "target": {
            "environment_key": environment_key,
            "shell_kind": str(target.get("shell_kind") or ""),
            "label": _agent_target_label(target),
            "host": str(target.get("host") or "").strip(),
            "distribution": str(target.get("distribution") or "").strip(),
        },
        "detection": {
            "command": "",
            "path": "",
            "kind": "",
        },
        "install": {
            "label": "",
            "command": "",
            "manual_only": False,
        },
        "verify": verify,
        "post_install": post_install,
        "missing_prerequisites": [],
    }

    if not environment_spec or not bool(environment_spec.get("supported")):
        return response

    detection = _detect_agent_binary_cached(target, binary)
    install_option, missing_prerequisites = _select_install_option(environment_spec, target)
    response["warning"] = str(environment_spec.get("warning") or "").strip()
    response["detection"] = {
        "command": str(detection.get("command") or ""),
        "path": str(detection.get("path") or ""),
        "kind": str(detection.get("kind") or ""),
    }
    response["install"] = {
        "label": str((install_option or {}).get("label") or ""),
        "command": str((install_option or {}).get("command") or ""),
        "manual_only": bool((install_option or {}).get("manual_only")),
    }
    response["missing_prerequisites"] = missing_prerequisites

    if detection.get("incomplete"):
        response["status"] = "target_incomplete"
        response["message"] = str(detection.get("error") or "The target environment is incomplete.")
    elif detection.get("failed"):
        response["status"] = "check_failed"
        response["message"] = str(detection.get("error") or "The preflight check failed.")
    elif detection.get("found"):
        response["status"] = "installed"
        response["message"] = f"{label} is available in {_agent_target_label(target)}."
    elif missing_prerequisites and environment_key != "wsl_linux":
        response["status"] = "missing_prerequisite"
        response["message"] = missing_prerequisites[0]
    elif bool(environment_spec.get("detect_only")) or bool((install_option or {}).get("manual_only")):
        response["status"] = "needs_manual_install"
        response["message"] = f"{label} is missing in {_agent_target_label(target)}."
    else:
        response["status"] = "missing"
        response["message"] = f"{label} is missing in {_agent_target_label(target)}."

    response["status_label"] = _agent_status_label(response["status"])
    return response


def _find_wsl_executable() -> Optional[str]:
    """Locate wsl.exe when WSL mode targets a Windows distro."""
    for candidate in ("wsl.exe", "/mnt/c/Windows/System32/wsl.exe"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        if os.path.exists(candidate):
            return candidate
    return None


def _parse_wsl_list_output(output: str) -> List[Dict[str, Any]]:
    """Parse `wsl -l -v` output into distro metadata."""
    distros: List[Dict[str, Any]] = []

    for raw_line in str(output or "").splitlines():
        line = raw_line.replace("\x00", "").strip()
        normalized = line.lower()

        if not line:
            continue
        if "name" in normalized and "state" in normalized and "version" in normalized:
            continue
        if normalized.startswith("the following is a list"):
            continue

        is_default = line.startswith("*")
        if is_default:
            line = line[1:].strip()

        parts = re.split(r"\s{2,}", line)
        if len(parts) < 3:
            continue

        distros.append(
            {
                "name": parts[0].strip(),
                "state": parts[1].strip(),
                "version": parts[2].strip(),
                "default": is_default,
            }
        )

    return distros


def _inspect_wsl_distributions() -> Dict[str, Any]:
    """Inspect local WSL distributions and return both parsed and raw command output."""
    command_label = "wsl -l -v"
    wsl_executable = _find_wsl_executable()
    if not wsl_executable:
        return {
            "available": False,
            "command": command_label,
            "distros": [],
            "raw_output": "",
            "error": "WSL is not available on this system.",
        }

    try:
        result = subprocess.run(
            [wsl_executable, "-l", "-v"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"Failed to inspect WSL distributions: {exc}")
        return {
            "available": False,
            "command": command_label,
            "distros": [],
            "raw_output": "",
            "error": str(exc),
        }

    raw_output = "\n".join(
        part.strip("\n")
        for part in (result.stdout or "", result.stderr or "")
        if part
    ).strip()
    return {
        "available": True,
        "command": command_label,
        "distros": _parse_wsl_list_output(raw_output),
        "raw_output": raw_output,
        "error": "",
    }
