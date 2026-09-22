"""The name a conversation has, when the pane is only announcing its id.

``web/agent_activity.py`` reads what a pane *says about itself*, and for Codex
that is the OSC title its ``tui.terminal_title=['thread-title']`` override asks
for. The override is honest and the reading is correct, and between them they
still lose the one thing the dashboard row is for: a thread the reader has
named "Review OCR delegation" can announce nothing but
``019d2e46-065b-7b22-aa9e-51bb915be2ff``, because the thread's *title* and the
thread's *name* are two fields and only one of them travels over OSC. The row
then says ``New session`` about a conversation Codex's own resume picker lists
by name -- and rightly so, because an id is not a name and
``isOpaqueIdentifierTitle`` refuses to paint one as though it were.

So a whole-title UUID is read here as neither: not a display title, and not
proof that the thread is unnamed. It is *identity to resolve*, and this module
is the resolving half.

A pane announces that UUID only while the ``thread-title`` override is in
force, and GridVibe applies that override to exactly one command: the built-in
``codex``. Somebody who types ``codex resume <uuid>`` at the prompt -- which is
how a reader actually returns to a conversation -- gets Codex's *default*
title instead, which names the project, so the pane says ``gridvibe_colab``
about every thread in that repo and never says which one. Rewriting what the
reader typed is not on the table, so the id is read from the second place it
is already sitting: **the command that started the agent**. GridVibe captures
that line whole, at launch and again when it promotes a typed command to a
runtime agent, and :func:`command_thread_id` is the reading of it.

The two sources differ in what the answer is *tied to*, and the difference is
load-bearing. A title-borne id belongs to an announcement, so its name is
dropped the moment the pane announces something else. A command-borne id
belongs to the agent run, so its name stands until that agent is retired --
and until the reader switches conversations inside the TUI, which no title
would show, so ``web/terminal_io.py`` watches for the submitted line instead.

Four rules hold it up, and each one is a way the feature could otherwise cost
more than it is worth:

- **Nothing is asked of the pane.** The lookup is a separate, bounded,
  one-shot ``codex app-server`` speaking ``thread/read`` -- the same supported
  metadata read Codex's own picker is built on -- and never a keystroke into a
  terminal somebody is working in. A failure costs the pane its conversation
  name and nothing else; the ``New session`` fallback is what stays.
- **It runs where the pane's shell runs.** A thread store belongs to one
  machine and one account, so a Codex pane on WSL is asked inside that distro
  and an SSH pane over its own transport. Asking the GridVibe host about a
  remote thread would answer confidently and wrongly.
- **It happens on an announcement, never on a poll.** The trigger is a *new*
  whole-title UUID from one pane's stream. A dashboard read still reads a
  dictionary. Answers are cached by provider, environment and thread, and a
  thread that had no name yet is retried on a short bounded schedule and then
  left alone -- Codex names a thread some turns in, so the first answer is
  routinely "not yet" and the fifth would still be an unbounded poll.
- **The answer is tied to the announcement it answers.** A resolved name is
  published beside the exact title text it was resolved from, and the reader
  drops it the moment the pane announces something else. That is also what
  makes the existing title floor cover it for free: a masked title matches
  nothing, so a retired agent's name cannot label its replacement.

Values in, values out apart from the two runners at the bottom, which are the
only things here that touch a process or a socket and are both injectable --
so ``tests/test_agent_conversations.py`` drives the real parser, the real
schedule and the real bounded runner against a stand-in app server. The
stream side stays in ``web/terminal_io.py``.
"""

import json
import logging
import re
import shlex
import socket
import subprocess
import threading
import time
import uuid
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from web.agent_activity import normalize_agent_title
from web.process_bounds import new_process_group, terminate_process_tree

logger = logging.getLogger(__name__)

#: The one provider this module resolves for. It is named rather than implied
#: because the cache key carries it: another CLI's thread id is not a Codex
#: thread id even when the two happen to be the same UUID.
CONVERSATION_PROVIDER_CODEX = "codex"
CONVERSATION_PROVIDER_CLAUDE = "claude"

#: Conversation identity is deliberately narrower than the registry.  The
#: JSON advertises a capability, while this allowlist remains the executable
#: policy: a hand-edited registry can never turn an arbitrary string into a
#: shell template.
_CONVERSATION_RESTORE_CAPABILITIES = {
    CONVERSATION_PROVIDER_CLAUDE: {
        "strategy": "assigned_uuid",
        "create_style": "flag",
        "resume_style": "flag",
    },
    CONVERSATION_PROVIDER_CODEX: {
        "strategy": "osc_uuid",
        "resume_style": "subcommand",
    },
}

CONVERSATION_PROVIDER_FIELD = "agent_conversation_provider"
CONVERSATION_ID_FIELD = "agent_conversation_id"
CONVERSATION_RESUME_FIELD = "agent_conversation_resume"
CONVERSATION_ID_MAX_LENGTH = 64

EMPTY_CONVERSATION_FIELDS = {
    CONVERSATION_PROVIDER_FIELD: "",
    CONVERSATION_ID_FIELD: "",
    CONVERSATION_RESUME_FIELD: False,
}

#: The subcommand and the method. Codex's app server is the supported way to
#: read a stored thread's metadata without resuming it; the alternative is
#: scraping ``session_index.jsonl``, which is provider-owned state with no
#: compatibility promise, and localized exit prose, which is not data at all.
APP_SERVER_SUBCOMMAND = "app-server"
APP_SERVER_METHOD_INITIALIZE = "initialize"
APP_SERVER_METHOD_INITIALIZED = "initialized"
APP_SERVER_METHOD_THREAD_READ = "thread/read"

#: Fixed request ids, so the reader can recognise its own answer among the
#: notifications the server volunteers on the same stream.
APP_SERVER_INITIALIZE_ID = 1
APP_SERVER_THREAD_READ_ID = 2

#: What GridVibe calls itself in the handshake. It lands in the server's own
#: user-agent string and nowhere else.
APP_SERVER_CLIENT_NAME = "gridvibe"
APP_SERVER_CLIENT_VERSION = "1"

#: How long one lookup may take, end to end, including starting the server.
#: A measured local answer arrives in well under a second; this is the bound
#: on the case where it never arrives at all.
CONVERSATION_PROBE_TIMEOUT_SECONDS = 10.0

#: How much of a lookup's output is read before it is abandoned. One
#: ``thread/read`` answer carries the thread's metadata and a preview of its
#: first turn -- measured at roughly 11 KB -- so this is ample headroom and
#: still a ceiling a runaway stream cannot grow past.
CONVERSATION_PROBE_MAX_OUTPUT_BYTES = 256 * 1024

#: The whole negative schedule for one announcement, in seconds between
#: attempts. Codex names a thread from its opening turns, so the first read of
#: a just-started thread is expected to find no name; these are the three later
#: chances that announcement gets before the pane keeps its fallback. Four
#: bounded lookups is the whole cost of a conversation that never gets named.
CONVERSATION_RETRY_DELAYS = (6.0, 20.0, 60.0)

#: How many found names are remembered. Panes are few and threads are
#: per-pane, so this is a ceiling rather than a working size: it exists so a
#: workspace churning through panes for a day cannot grow a map nobody ever
#: reads again.
CONVERSATION_CACHE_MAX_ENTRIES = 256

#: What one lookup concluded.
LOOKUP_NAMED = "named"
LOOKUP_UNNAMED = "unnamed"
LOOKUP_UNKNOWN = "unknown"
LOOKUP_UNAVAILABLE = "unavailable"

#: The two shapes a whole title may take to be an id: the canonical hyphenated
#: UUID, or the same thirty-two hex digits with the hyphens dropped. Stricter
#: than ``agent-identity.js``'s ``OPAQUE_ID_PATTERNS``, whose bare-hex floor is
#: 24 because its job is to refuse to *paint* an identifier: refusing to paint
#: something is cheap, and resolving it as a thread id is a request.
_THREAD_ID_PATTERNS = (
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE),
    re.compile(r"^[0-9a-f]{32}$", re.IGNORECASE),
)


#: The subcommand that names an existing conversation on a command line, and
#: the wrappers a reader may put in front of the binary. Both mirror
#: ``web/terminal_io.py``'s own ``_agent_from_terminal_command``, which is what
#: recognised the command as Codex's in the first place.
CODEX_RESUME_SUBCOMMAND = "resume"
_COMMAND_LEADERS = frozenset({"command", "exec", "sudo"})

#: Options that swallow the token after them, so it is not mistaken for the
#: positional the id would be. Codex's ``-c key=value`` is the one that shows
#: up in practice, because it is how GridVibe's own overrides are written.
_VALUE_TAKING_OPTIONS = frozenset({"-c", "--config"})

#: The launcher's own suffix on a Windows binary, stripped before the name is
#: compared -- ``codex.cmd`` is codex.
_BINARY_SUFFIX_PATTERN = re.compile(r"\.(?:bat|cmd|exe)$", re.IGNORECASE)


class ConversationIdentityError(ValueError):
    """A stored or requested conversation identity is not safely launchable."""


def normalize_conversation_id(value: Any) -> str:
    """Return one canonical UUID, or ``""`` for anything else."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate or len(candidate) > CONVERSATION_ID_MAX_LENGTH:
        return ""
    try:
        return str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):
        return ""


def conversation_restore_capability(
    registry: Mapping[str, Any], provider: Any
) -> Dict[str, str]:
    """Return a verified, allowlisted restore capability, or an empty dict."""
    key = str(provider or "").strip().lower()
    expected = _CONVERSATION_RESTORE_CAPABILITIES.get(key)
    spec = registry.get(key) if isinstance(registry, Mapping) else None
    capability = spec.get("conversation_restore") if isinstance(spec, dict) else None
    if not expected or not isinstance(capability, dict):
        return {}
    normalized = {
        field: str(capability.get(field) or "").strip().lower()
        for field in expected
    }
    return dict(expected) if normalized == expected else {}


def _built_in_agent_command(config: Mapping[str, Any], provider: str) -> bool:
    """True when the launch is the provider's untouched built-in command."""
    return (
        str(config.get("startup_mode") or "").strip().lower() == "agent"
        and str(config.get("initial_command_mode") or "").strip().lower() == "agent"
        and str(config.get("agent_selection") or "").strip().lower() == provider
        and not str(config.get("custom_agent") or "").strip()
        and str(config.get("initial_command") or "").strip().lower() == provider
    )


def _identity_values(config: Mapping[str, Any]) -> Tuple[Any, Any]:
    return config.get(CONVERSATION_PROVIDER_FIELD), config.get(CONVERSATION_ID_FIELD)


def validate_conversation_identity(
    config: Mapping[str, Any], registry: Mapping[str, Any]
) -> Tuple[str, str]:
    """Validate a durable provider/id pair and return its canonical values.

    ``None``/``""`` on both fields is the backward-compatible absent shape.
    Anything partially present, wrong-typed, unsupported, provider-mismatched,
    or attached to a command that cannot honestly replay it is rejected.
    """
    raw_provider, raw_id = _identity_values(config)
    provider_absent = raw_provider is None or raw_provider == ""
    id_absent = raw_id is None or raw_id == ""
    if provider_absent and id_absent:
        return "", ""
    if provider_absent != id_absent:
        raise ConversationIdentityError("conversation identity is incomplete")
    if not isinstance(raw_provider, str) or not isinstance(raw_id, str):
        raise ConversationIdentityError("conversation identity must be text")

    provider = raw_provider.strip().lower()
    conversation_id = normalize_conversation_id(raw_id)
    if not provider or not conversation_id:
        raise ConversationIdentityError("conversation identity is invalid")
    if not conversation_restore_capability(registry, provider):
        raise ConversationIdentityError("conversation provider is not restorable")
    if str(config.get("startup_mode") or "").strip().lower() != "agent":
        raise ConversationIdentityError("conversation identity requires an agent pane")
    if str(config.get("agent_selection") or "").strip().lower() != provider:
        raise ConversationIdentityError("conversation provider does not match the pane")

    if not _built_in_agent_command(config, provider):
        command_provider, command_id = command_conversation_identity(
            config.get("initial_command")
        )
        if (command_provider, command_id) != (provider, conversation_id):
            raise ConversationIdentityError(
                "conversation identity is not tied to the startup command"
            )
    return provider, conversation_id


def prepare_conversation_launch_fields(
    config: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    restore: bool = False,
) -> Dict[str, Any]:
    """Plan identity for one launcher or server-side restore pane."""
    provider, conversation_id = validate_conversation_identity(config, registry)
    if provider:
        if not restore:
            raise ConversationIdentityError(
                "conversation identity is accepted only from workspace restore"
            )
        return {
            CONVERSATION_PROVIDER_FIELD: provider,
            CONVERSATION_ID_FIELD: conversation_id,
            CONVERSATION_RESUME_FIELD: True,
        }

    selected = str(config.get("agent_selection") or "").strip().lower()
    capability = conversation_restore_capability(registry, selected)
    if _built_in_agent_command(config, selected) and capability.get("strategy") == "assigned_uuid":
        return {
            CONVERSATION_PROVIDER_FIELD: selected,
            CONVERSATION_ID_FIELD: str(uuid.uuid4()),
            CONVERSATION_RESUME_FIELD: False,
        }
    return dict(EMPTY_CONVERSATION_FIELDS)


def fresh_conversation_fields(
    config: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> Dict[str, Any]:
    """Plan a brand-new conversation for a pane about to be relaunched.

    A relaunch from the pane header -- same agent or another one, another
    shell, MCP toggled -- is a request for a *new* process, and a new process
    starts a new conversation. Whatever identity the pane carried is dropped
    rather than resumed; only a workspace restore resumes.
    """
    stripped = dict(config)
    stripped[CONVERSATION_PROVIDER_FIELD] = ""
    stripped[CONVERSATION_ID_FIELD] = ""
    return prepare_conversation_launch_fields(stripped, registry)


def _quote_conversation_id(value: str, shell_family: str) -> str:
    """Pass a validated UUID through the target shell's quoting boundary."""
    if shell_family == "powershell":
        return "'" + value.replace("'", "''") + "'"
    if shell_family == "cmd":
        # Canonical UUIDs contain no cmd metacharacters. Single quotes would be
        # literal there, unlike PowerShell and POSIX.
        return value
    return shlex.quote(value)


def compose_conversation_command(
    base_command: Any,
    provider: Any,
    conversation_id: Any,
    resume: Any,
    shell_family: str,
    registry: Mapping[str, Any],
) -> str:
    """Compose one allowlisted provider create/resume command."""
    base = str(base_command or "").strip()
    selected = str(provider or "").strip().lower()
    if not base or base.lower() != selected:
        return base
    canonical_id = normalize_conversation_id(conversation_id)
    capability = conversation_restore_capability(registry, selected)
    if not canonical_id or not capability:
        return base
    quoted = _quote_conversation_id(canonical_id, shell_family)
    if selected == CONVERSATION_PROVIDER_CLAUDE:
        flag = "--resume" if bool(resume) else "--session-id"
        return f"{base} {flag} {quoted}"
    if selected == CONVERSATION_PROVIDER_CODEX and bool(resume):
        return f"{base} resume {quoted}"
    return base


def observed_conversation_identity(
    provider: Any, title: Any, registry: Mapping[str, Any]
) -> Tuple[str, str]:
    """Return the identity authoritatively announced by a provider title."""
    selected = str(provider or "").strip().lower()
    capability = conversation_restore_capability(registry, selected)
    if capability.get("strategy") != "osc_uuid":
        return "", ""
    conversation_id = conversation_thread_id(title)
    return (selected, conversation_id) if conversation_id else ("", "")


#: Switch commands whose destination is a conversation the provider already
#: saved: a picked or forked conversation resumes; a new or cleared one does
#: not exist until its first prompt.
_SAVED_DESTINATION_SWITCH_COMMANDS = frozenset({"/resume", "/fork"})

_CONVERSATION_SWITCH_COMMANDS = {
    CONVERSATION_PROVIDER_CODEX: frozenset({"/new", "/resume", "/fork"}),
    CONVERSATION_PROVIDER_CLAUDE: frozenset({"/clear", "/resume"}),
}


def is_conversation_switch_command(provider: Any, submitted_line: Any) -> bool:
    """True when a complete submitted TUI line makes identity unknown."""
    return bool(conversation_switch_verb(provider, submitted_line))


def conversation_switch_verb(provider: Any, submitted_line: Any) -> str:
    """The switch command a submitted line is, or ``""``."""
    selected = str(provider or "").strip().lower()
    words = str(submitted_line or "").strip().split()
    verb = words[0].lower() if words else ""
    return verb if verb in _CONVERSATION_SWITCH_COMMANDS.get(selected, ()) else ""


def switch_lands_on_saved_conversation(verb: Any) -> bool:
    """True when the conversation a switch lands on already exists on disk."""
    return str(verb or "").strip().lower() in _SAVED_DESTINATION_SWITCH_COMMANDS


def _command_positionals(tokens: List[str]) -> List[str]:
    """The words of a command line that are not options or option values."""
    positionals: List[str] = []
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if token in _VALUE_TAKING_OPTIONS:
                skip_next = True
            continue
        positionals.append(token)
    return positionals


def command_thread_id(command: Any, binary: str = CONVERSATION_PROVIDER_CODEX) -> str:
    """The thread id a ``codex resume <uuid>`` line names, or "".

    The other half of "which conversation is this pane in", and the half that
    answers for a pane GridVibe did not compose the command for. Only an exact
    id counts: ``codex resume --last`` names the most recent thread without
    saying which one it is, ``codex resume`` opens a picker, and ``resume`` also
    accepts a session *name*, which is already prose and is not an identity to
    look up. Each of those is left alone rather than guessed at.

    ``codex fork <uuid>`` is deliberately not read: it names the thread being
    forked *from*, and the pane is in the new one.
    """
    try:
        tokens = shlex.split(str(command or "").strip(), posix=True)
    except ValueError:
        return ""
    while tokens and tokens[0].lower() in _COMMAND_LEADERS:
        tokens.pop(0)
    if not tokens:
        return ""
    executable = re.split(r"[/\\]", tokens[0])[-1].lower()
    executable = _BINARY_SUFFIX_PATTERN.sub("", executable)
    if executable != str(binary or "").strip().lower():
        return ""
    positionals = _command_positionals(tokens[1:])
    if len(positionals) < 2 or positionals[0].lower() != CODEX_RESUME_SUBCOMMAND:
        return ""
    return conversation_thread_id(positionals[1])


def command_conversation_identity(command: Any) -> Tuple[str, str]:
    """Return an exact provider/id pair stated by a launch command.

    This is the best-effort path for agents a reader starts at an ordinary
    terminal prompt. A plain ``claude``/``codex`` line says no identity and is
    never guessed; an explicit create/resume UUID is authoritative launch
    metadata.
    """
    codex_id = command_thread_id(command)
    if codex_id:
        return CONVERSATION_PROVIDER_CODEX, codex_id

    try:
        tokens = shlex.split(str(command or "").strip(), posix=True)
    except ValueError:
        return "", ""
    while tokens and tokens[0].lower() in _COMMAND_LEADERS:
        tokens.pop(0)
    if not tokens:
        return "", ""
    executable = re.split(r"[/\\]", tokens[0])[-1].lower()
    executable = _BINARY_SUFFIX_PATTERN.sub("", executable)
    if executable != CONVERSATION_PROVIDER_CLAUDE:
        return "", ""
    for index, token in enumerate(tokens[1:], start=1):
        lowered = token.lower()
        if lowered in {"--resume", "--session-id"} and index + 1 < len(tokens):
            conversation_id = normalize_conversation_id(tokens[index + 1])
            return (
                (CONVERSATION_PROVIDER_CLAUDE, conversation_id)
                if conversation_id
                else ("", "")
            )
        if lowered.startswith(("--resume=", "--session-id=")):
            conversation_id = normalize_conversation_id(token.split("=", 1)[1])
            return (
                (CONVERSATION_PROVIDER_CLAUDE, conversation_id)
                if conversation_id
                else ("", "")
            )
    return "", ""


def command_resumes_conversation(command: Any) -> bool:
    """True when a launch command resumes a conversation the provider saved.

    ``codex resume <uuid>`` and ``claude --resume <uuid>`` name a conversation
    that exists on disk; ``claude --session-id <uuid>`` names one that will
    exist only once a prompt is sent.
    """
    provider, conversation_id = command_conversation_identity(command)
    if not conversation_id:
        return False
    if provider == CONVERSATION_PROVIDER_CODEX:
        return True
    try:
        tokens = shlex.split(str(command or "").strip(), posix=True)
    except ValueError:
        return False
    return any(
        token.lower() == "--resume" or token.lower().startswith("--resume=")
        for token in tokens
    )


#: Submitted TUI lines that are commands to the agent rather than prompts: a
#: slash command asks the TUI for something and saves no turn.
_TUI_COMMAND_PREFIX = "/"


def is_conversation_prompt(submitted_line: Any) -> bool:
    """True when a submitted agent line is a turn the provider will save.

    Codex and Claude both write a conversation to disk on its first turn and
    not before, so an id is only resumable from the first prompt on.
    """
    line = str(submitted_line or "").strip()
    return bool(line) and not line.startswith(_TUI_COMMAND_PREFIX)


def conversation_thread_id(title: Any) -> str:
    """Return the canonical thread id a whole title is, or "" for anything else.

    *Whole* is the load-bearing word. ``Review OCR delegation (019d…)`` is a
    name with an id in it and is already published as the name it is, so
    reading an id out of the middle of a title would replace prose somebody
    chose with a lookup of the same conversation. Only a title that is nothing
    but an identifier says "this pane has no name to publish".
    """
    candidate = str(title or "").strip()
    if not any(pattern.match(candidate) for pattern in _THREAD_ID_PATTERNS):
        return ""
    try:
        return str(uuid.UUID(candidate))
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - patterns pre-filter
        return ""


def normalize_conversation_name(value: Any, thread_id: Any = "") -> str:
    """Return a publishable conversation name, or "" when there is none.

    Bounded and flattened exactly as an announced title is, because it goes to
    the same row -- and refused when it is only the id again, which is what a
    provider that fills the name field with its own identifier would hand back.
    Publishing that would round-trip the whole lookup into the very thing the
    reader already refuses to paint.
    """
    name = normalize_agent_title(value)
    if not name:
        return ""
    if conversation_thread_id(name):
        return ""
    known = str(thread_id or "").strip().lower()
    if known and name.strip().lower() == known:
        return ""
    return name


# --------------------------------------------------------------------------
# The app-server exchange: three lines out, one answer back.
# --------------------------------------------------------------------------


def thread_read_request_text(thread_id: str) -> str:
    """Build the whole stdin side of one lookup: handshake, ack, read.

    Newline-delimited JSON-RPC, written in one go and never read back except
    for the one answer below. ``includeTurns`` is deliberately absent: the
    metadata read is the supported shape and hydrating a thread's history to
    learn its name would be a different operation with a different cost.
    """
    lines = [
        {
            "jsonrpc": "2.0",
            "id": APP_SERVER_INITIALIZE_ID,
            "method": APP_SERVER_METHOD_INITIALIZE,
            "params": {
                "clientInfo": {
                    "name": APP_SERVER_CLIENT_NAME,
                    "version": APP_SERVER_CLIENT_VERSION,
                }
            },
        },
        {
            "jsonrpc": "2.0",
            "method": APP_SERVER_METHOD_INITIALIZED,
            "params": {},
        },
        {
            "jsonrpc": "2.0",
            "id": APP_SERVER_THREAD_READ_ID,
            "method": APP_SERVER_METHOD_THREAD_READ,
            "params": {"threadId": str(thread_id)},
        },
    ]
    return "".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines)


class ThreadReadReader:
    """Fold one app server's output lines into a single settled answer.

    The server volunteers notifications on the same stream as the answer, and
    a lookup has no reason to wait for a process that is designed to keep
    running -- so the reader is fed line by line and says when it is done,
    which is what lets both runners stop at the answer rather than at EOF.
    """

    def __init__(self, thread_id: str):
        self.thread_id = str(thread_id or "")
        self.outcome = LOOKUP_UNAVAILABLE
        self.name = ""

    @property
    def settled(self) -> bool:
        return self.outcome != LOOKUP_UNAVAILABLE

    def feed_line(self, line: str) -> bool:
        """Read one line; return whether the answer has now been decided."""
        if self.settled:
            return True
        text = str(line or "").strip()
        if not text:
            return False
        try:
            message = json.loads(text)
        except (TypeError, ValueError):
            # Anything a shell profile or a wrapper printed before the server
            # started talking. Skipped rather than fatal: the answer is
            # recognised by its own id, so noise around it costs nothing.
            return False
        if not isinstance(message, dict) or message.get("id") != APP_SERVER_THREAD_READ_ID:
            return False
        if message.get("error") is not None:
            # A thread this environment has never heard of, which is the
            # honest answer for an id announced on another machine.
            self.outcome = LOOKUP_UNKNOWN
            return True
        result = message.get("result")
        thread = result.get("thread") if isinstance(result, dict) else None
        if not isinstance(thread, dict):
            self.outcome = LOOKUP_UNKNOWN
            return True
        answered_id = conversation_thread_id(thread.get("id"))
        if answered_id and answered_id != self.thread_id:
            # A different thread's metadata answers nothing about this pane.
            self.outcome = LOOKUP_UNKNOWN
            return True
        # A user-set thread title lives in ``name`` and must win. Ordinary
        # threads may never acquire one, but app-server still gives history
        # clients their ``preview`` as the summary used to identify the
        # conversation. Resumed panes need that same fallback: unlike a fresh
        # built-in launch, their terminal title only names the project.
        name = normalize_conversation_name(thread.get("name"), self.thread_id)
        if not name:
            name = normalize_conversation_name(thread.get("preview"), self.thread_id)
        self.outcome = LOOKUP_NAMED if name else LOOKUP_UNNAMED
        self.name = name
        return True

    def answer(self) -> Tuple[str, str]:
        return self.outcome, self.name


def parse_thread_read_output(output: Any, thread_id: str) -> Tuple[str, str]:
    """Read a whole captured stream into ``(outcome, name)``."""
    reader = ThreadReadReader(thread_id)
    for line in str(output or "").splitlines():
        if reader.feed_line(line):
            break
    return reader.answer()


# --------------------------------------------------------------------------
# Where the lookup runs.
# --------------------------------------------------------------------------


def _login_shell_command(binary: str) -> str:
    """One POSIX login-shell line that starts the app server.

    ``-lc`` and not a bare exec: the CLI is routinely installed by a version
    manager whose ``PATH`` only exists inside a login shell, which is the same
    reason ``web/agents.py`` detects it that way.
    """
    return "exec " + shlex.quote(str(binary or "")) + " " + APP_SERVER_SUBCOMMAND


def local_probe_target(
    binary: str,
    *,
    shell_kind: str = "",
    distribution: str = "",
    wsl_executable: str = "",
    resolved_binary: str = "",
) -> Dict[str, Any]:
    """Describe the lookup for a pane whose shell is on this machine.

    A WSL pane is a different machine as far as a thread store is concerned,
    so it is asked inside its own distribution and carries that distribution in
    its environment key. Everything else is this host, whichever Windows shell
    the pane happens to be running.
    """
    kind = str(shell_kind or "").strip().lower()
    if kind == "wsl":
        if not wsl_executable:
            return {}
        argv = [str(wsl_executable)]
        if distribution:
            argv.extend(["--distribution", str(distribution)])
        argv.extend(["--exec", "bash", "-lc", _login_shell_command(binary)])
        return {
            "kind": "local",
            "environment": f"wsl:{str(distribution or '').strip().lower()}",
            "argv": argv,
        }
    executable = str(resolved_binary or binary or "").strip()
    if not executable:
        return {}
    return {
        "kind": "local",
        "environment": "local",
        "argv": [executable, APP_SERVER_SUBCOMMAND],
    }


def remote_probe_target(
    transport: Any,
    binary: str,
    *,
    username: str = "",
    host: str = "",
    port: Any = 22,
) -> Dict[str, Any]:
    """Describe the lookup for a pane whose shell is on another machine.

    The pane's *own* transport, on a second channel -- never the interactive
    one, which somebody is working in. The environment key names the account
    as well as the host: two accounts on one machine own two thread stores.
    """
    if transport is None or not str(binary or "").strip():
        return {}
    account = f"{str(username or '').strip()}@{str(host or '').strip()}:{port}"
    return {
        "kind": "ssh",
        "environment": f"ssh:{account.lower()}",
        "transport": transport,
        "command": "sh -lc " + shlex.quote(_login_shell_command(binary)),
    }


def cache_key(provider: str, target: Dict[str, Any], thread_id: str) -> Tuple[str, str, str]:
    """The three facts an answer belongs to, in the order they are asked."""
    environment = str((target or {}).get("environment") or "")
    return (str(provider or ""), environment, str(thread_id or ""))


# --------------------------------------------------------------------------
# What is already known, and when it is worth asking again.
# --------------------------------------------------------------------------


def retry_delay(
    attempt: int,
    delays: Tuple[float, ...] = CONVERSATION_RETRY_DELAYS,
) -> Optional[float]:
    """How long to wait after ``attempt`` before asking again, or ``None``.

    The schedule belongs to *one announcement*, not to the thread: a pane that
    has been told "this thread has no name" a few times stops asking, and a
    pane that announces the same thread tomorrow -- when Codex has long since
    named it -- starts its own. Caching the negative instead would make the
    first pane's silence permanent for every pane after it.
    """
    index = max(0, int(attempt))
    if index >= len(delays):
        return None
    return float(delays[index])


class ConversationNameCache:
    """The names already found, by provider, environment and thread.

    Positive answers only, and kept for the life of the process: a thread's
    name does not change under an id that is already published beside it, and
    two panes resumed on one thread should cost one lookup. What is *not* here
    is any memory of a thread that had no name yet -- that is
    :func:`retry_delay`'s bounded schedule, run by whichever pane is asking.

    Every method takes its own lock hold and nothing else, so the resolver
    threads and the observer thread reach it without an ordering rule.
    """

    def __init__(self, max_entries: int = CONVERSATION_CACHE_MAX_ENTRIES):
        self._lock = threading.Lock()
        self._entries: "OrderedDict[Tuple[str, str, str], str]" = OrderedDict()
        self._max_entries = max(1, int(max_entries))

    def resolved_name(self, key: Tuple[str, str, str]) -> str:
        """The name already known for this thread, or ""."""
        with self._lock:
            name = self._entries.get(key)
            if name is None:
                return ""
            self._entries.move_to_end(key)
            return name

    def remember(self, key: Tuple[str, str, str], name: str) -> None:
        """Keep one found name, dropping whatever was read longest ago."""
        if not name:
            return
        with self._lock:
            self._entries[key] = str(name)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def forget(self, key: Tuple[str, str, str]) -> None:
        with self._lock:
            self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# --------------------------------------------------------------------------
# The two runners. Everything above this line is values in, values out.
# --------------------------------------------------------------------------


def run_local_probe(
    argv: List[str],
    request_text: str,
    thread_id: str,
    *,
    timeout: float = CONVERSATION_PROBE_TIMEOUT_SECONDS,
    max_output_bytes: int = CONVERSATION_PROBE_MAX_OUTPUT_BYTES,
) -> Tuple[str, str]:
    """Ask a local (or WSL) app server, bounded in time, output and process tree.

    The server is a long-running process by design, so this never waits for it
    to exit: a reader thread stops at the answer, and the bound is what happens
    when the answer never comes. The kill is a *tree* kill because the child is
    routinely a shim -- a ``.cmd`` on Windows, ``wsl.exe`` around a distro --
    holding the pipe on behalf of something else (Guardrail 4, the same rule
    ``web/process_bounds.py`` was written for).
    """
    reader = ThreadReadReader(thread_id)
    settled = threading.Event()
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **new_process_group(),
        )
    except (OSError, ValueError) as exc:
        logger.debug("Conversation lookup could not start %r: %s", argv[:1], exc)
        return LOOKUP_UNAVAILABLE, ""

    def _drain() -> None:
        total = 0
        pending = bytearray()
        try:
            read = getattr(process.stdout, "read1", process.stdout.read)  # type: ignore[union-attr]
            while total < max_output_bytes:
                raw = read(min(8192, max_output_bytes - total))
                if not raw:
                    break
                total += len(raw)
                pending.extend(raw)
                while b"\n" in pending:
                    line, _, remainder = pending.partition(b"\n")
                    pending = bytearray(remainder)
                    if reader.feed_line(line.decode("utf-8", errors="replace")):
                        return
        except (OSError, ValueError):
            pass
        finally:
            settled.set()

    worker = threading.Thread(target=_drain, name="gridvibe-conversation-read", daemon=True)
    worker.start()
    try:
        if process.stdin is not None:
            process.stdin.write(request_text.encode("utf-8"))
            process.stdin.flush()
    except (OSError, ValueError):
        pass
    settled.wait(max(0.0, float(timeout)))
    # Kill first, so the reader's blocking readline sees EOF and ends by
    # itself; only then close the pipes it was reading.
    try:
        terminate_process_tree(process)
    except OSError:  # pragma: no cover - the tree kill swallows its own
        pass
    worker.join(timeout=1.0)
    for stream in (process.stdin, process.stdout):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:  # pragma: no cover - already killed
        pass
    return reader.answer()


def run_remote_probe(
    transport: Any,
    command: str,
    request_text: str,
    thread_id: str,
    *,
    timeout: float = CONVERSATION_PROBE_TIMEOUT_SECONDS,
    max_output_bytes: int = CONVERSATION_PROBE_MAX_OUTPUT_BYTES,
) -> Tuple[str, str]:
    """Ask a remote app server over a second channel on the pane's own transport.

    Same bounds as the local runner and the same reason for them, with the
    channel standing in for the process tree: the deadline is the whole
    exchange, not each read, so a host that answers one byte a second cannot
    hold the channel open past it. ``shutdown_write`` is deliberately not
    called -- the server would be within its rights to exit on stdin EOF, and
    the answer is what the exchange is for.
    """
    reader = ThreadReadReader(thread_id)
    if transport is None or not getattr(transport, "is_active", lambda: False)():
        return LOOKUP_UNAVAILABLE, ""
    channel = None
    deadline = time.monotonic() + max(0.0, float(timeout))
    try:
        channel = transport.open_session(timeout=timeout)
        channel.settimeout(timeout)
        channel.exec_command(command)
        channel.sendall(request_text.encode("utf-8"))
        pending = ""
        total = 0
        while time.monotonic() < deadline and total < max_output_bytes:
            try:
                data = channel.recv(min(8192, max_output_bytes - total))
            except socket.timeout:
                break
            if not data:
                break
            total += len(data)
            pending += data.decode("utf-8", errors="replace")
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                if reader.feed_line(line):
                    return reader.answer()
        reader.feed_line(pending)
    except Exception as exc:  # paramiko raises its own family here
        logger.debug("Remote conversation lookup failed: %s", exc)
    finally:
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass
    return reader.answer()


def probe_conversation_name(
    target: Dict[str, Any],
    thread_id: str,
    *,
    timeout: float = CONVERSATION_PROBE_TIMEOUT_SECONDS,
    max_output_bytes: int = CONVERSATION_PROBE_MAX_OUTPUT_BYTES,
    local_runner: Optional[Callable[..., Tuple[str, str]]] = None,
    remote_runner: Optional[Callable[..., Tuple[str, str]]] = None,
) -> Tuple[str, str]:
    """Run one lookup against whichever environment ``target`` names."""
    if not target or not thread_id:
        return LOOKUP_UNAVAILABLE, ""
    request_text = thread_read_request_text(thread_id)
    if target.get("kind") == "ssh":
        runner = remote_runner or run_remote_probe
        return runner(
            target.get("transport"),
            str(target.get("command") or ""),
            request_text,
            thread_id,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
        )
    argv = list(target.get("argv") or [])
    if not argv:
        return LOOKUP_UNAVAILABLE, ""
    runner = local_runner or run_local_probe
    return runner(
        argv,
        request_text,
        thread_id,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
    )
