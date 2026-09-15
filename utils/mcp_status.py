"""Report whether a GridVibe pane's agent could actually get GridVibe tools.

The MCP sidecar fails in a place nobody watches. The agent CLI starts it as an
ordinary stdio child; when it exits immediately -- because the SDK is not
installed in the interpreter the generated config names, or the config points at
an install that has moved -- the CLI reports one line (``Failed to reconnect to
gridvibe: CONNECTION_CLOSED``) with no cause, and the pane otherwise looks fine.
Everything below is checked from the *outside*, exactly as the CLI would reach
it, so the answer is the one the CLI would get.

The sidecar's interpreter is the one named in ``.gridvibe_mcp.json``, which is
not necessarily the one running this script: GridVibe writes ``sys.executable``
as of the moment it started. So the SDK check spawns that interpreter rather
than importing here, and the handshake spawns the whole configured command.

Usage::

    python utils/mcp_status.py             # report, exit 1 if anything is broken
    python utils/mcp_status.py --json      # the same findings as one JSON object

Exit codes: ``0`` an agent pane would get its tools, ``1`` something in the
chain is broken (each broken link is named).
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.mcp_launch import (  # noqa: E402
    MCP_SERVER_NAME,
    SIDECAR_ENTRY,
    mcp_config_path,
)

#: Seconds any one bounded child gets. The handshake is a local process
#: answering a single line, so a slow one is a hung one.
SPAWN_TIMEOUT = 20.0

#: Seconds the loopback reachability probe gets. GridVibe being down is not a
#: failure here -- the sidecar starts fine without it and fails per call.
PROBE_TIMEOUT = 3.0

OK = "ok"
BROKEN = "broken"
NOTE = "note"


def _run(command, stdin_text=None, timeout=None):
    """Run one bounded child and return ``(returncode, stdout, stderr)``.

    A child that overruns is killed rather than left behind, and the timeout
    comes back as an ordinary failure string: a hung sidecar is one of the
    answers this script exists to give, not an exception for the caller.

    The bound is read here rather than defaulted in the signature, so a caller
    that needs a shorter one -- the suite proving the kill path, above all --
    can state it without waiting out the real one.
    """
    timeout = SPAWN_TIMEOUT if timeout is None else timeout
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        return None, "", str(exc)
    try:
        out, err = process.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return None, "", f"no answer within {timeout:g}s"
    return process.returncode, out, err


def read_config(path):
    """Return the generated config's server block, or an error string."""
    if not os.path.exists(path):
        return None, "not written yet -- start GridVibe once, or run `make mcp-config`"
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as exc:
        return None, f"unreadable: {exc}"
    servers = document.get("mcpServers")
    if not isinstance(servers, dict):
        return None, "has no mcpServers block"
    server = servers.get(MCP_SERVER_NAME)
    if not isinstance(server, dict):
        return None, f"names no {MCP_SERVER_NAME!r} server"
    return server, ""


def configured_url(server):
    """Return the ``--url`` value baked into the config's args."""
    args = server.get("args")
    if not isinstance(args, list):
        return ""
    for index, value in enumerate(args):
        if value == "--url" and index + 1 < len(args):
            return str(args[index + 1])
    return ""


def check_sdk(interpreter):
    """Ask the configured interpreter itself whether it has a usable SDK."""
    probe = (
        "import importlib.metadata as m;"
        "import mcp.server.lowlevel as s;"
        "print(m.version('mcp'), hasattr(s.Server('x'), 'list_tools'))"
    )
    code, out, err = _run([interpreter, "-c", probe])
    if code is None:
        return BROKEN, f"could not run {interpreter}: {err}"
    if code != 0:
        if "No module named" in err and "mcp" in err:
            return BROKEN, "the MCP SDK is not installed there -- run `make mcp-deps`"
        return BROKEN, (err.strip().splitlines() or ["failed"])[-1]
    version, _, decorators = out.strip().partition(" ")
    if decorators.strip() != "True":
        return BROKEN, (
            f"mcp {version} has no low-level decorators -- the sidecar is written"
            " against 1.x; run `make mcp-deps`"
        )
    return OK, f"mcp {version}"


def check_handshake(server):
    """Spawn the configured command exactly as the agent CLI would."""
    command = [str(server.get("command") or "")]
    command.extend(str(value) for value in server.get("args") or [])
    if not command[0]:
        return BROKEN, "the config names no command"
    request = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "gridvibe-mcp-status", "version": "1"},
            },
        }
    )
    notification = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    code, out, err = _run(command, stdin_text=f"{request}\n{notification}\n")
    if code is None:
        return BROKEN, err
    for line in out.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        info = (message.get("result") or {}).get("serverInfo") or {}
        if info:
            return OK, f"answered as {info.get('name')} (SDK {info.get('version')})"
    detail = (err.strip().splitlines() or ["it wrote nothing to stdout"])[-1]
    return BROKEN, f"no initialize answer -- {detail}"


def check_reachable(url):
    """Probe the URL the sidecar was told to call. A note, never a failure."""
    if not url:
        return NOTE, "the config bakes in no --url"
    try:
        with urllib.request.urlopen(f"{url}/api/agents", timeout=PROBE_TIMEOUT) as reply:
            if reply.status == 200:
                return OK, f"GridVibe is answering at {url}"
            return NOTE, f"{url} answered {reply.status}"
    except (urllib.error.URLError, OSError):
        return NOTE, f"nothing is listening at {url} -- tools fail until GridVibe runs"


def collect():
    """Return the ordered findings, each a ``(label, state, detail)`` triple.

    Ordered as the chain fails: a missing config makes every later question
    meaningless, so the walk stops at the first link that cannot be followed
    rather than reporting five consequences of one cause.
    """
    path = mcp_config_path()
    findings = []
    server, problem = read_config(path)
    if problem:
        findings.append(("generated config", BROKEN, f"{path}: {problem}"))
        return findings
    findings.append(("generated config", OK, path))

    interpreter = str(server.get("command") or "")
    if not interpreter or not os.path.exists(interpreter):
        findings.append(
            ("sidecar interpreter", BROKEN, f"{interpreter or '(none)'} does not exist")
        )
        return findings
    findings.append(("sidecar interpreter", OK, interpreter))

    args = server.get("args") or []
    entry = str(args[0]) if args else ""
    findings.append(
        (
            "sidecar entry",
            OK if entry and os.path.exists(entry) else BROKEN,
            entry or f"missing -- expected {SIDECAR_ENTRY}",
        )
    )

    findings.append(("mcp SDK", *check_sdk(interpreter)))
    findings.append(("sidecar handshake", *check_handshake(server)))
    findings.append(("GridVibe", *check_reachable(configured_url(server))))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mcp_status",
        description="Report whether an agent pane could get GridVibe tools.",
    )
    parser.add_argument("--json", action="store_true", help="Emit findings as JSON.")
    args = parser.parse_args(argv)

    findings = collect()
    broken = [name for name, state, _ in findings if state == BROKEN]

    if args.json:
        json.dump(
            {
                "ok": not broken,
                "findings": [
                    {"check": name, "state": state, "detail": detail}
                    for name, state, detail in findings
                ],
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
        return 1 if broken else 0

    marks = {OK: "OK  ", BROKEN: "FAIL", NOTE: "--  "}
    for name, state, detail in findings:
        print(f"{marks[state]} {name:<20} {detail}")
    print()
    if broken:
        print(f"MCP is not usable: {', '.join(broken)}.")
        return 1
    print("An agent pane launched with MCP would get GridVibe tools.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
