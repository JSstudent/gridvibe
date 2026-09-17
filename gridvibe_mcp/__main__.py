"""Entry point: argv, then stdio MCP wiring.

Nobody starts this by hand, and GridVibe does not start it either. The agent
CLI does, as an ordinary stdio child, reading the generated
``.gridvibe_mcp.json``. When the pane closes the CLI exits and this exits with
it: no orphan, nothing to supervise.

The sidecar takes no identity arguments -- only ``--url``, baked into the
generated config by the process that owns the port. Identity arrives through
two levels of ordinary process inheritance (pane shell -> agent CLI -> here).

Run as a *script path* rather than ``-m`` on purpose: the agent CLI spawns this
with the pane's working directory, which is the user's project, not GridVibe's
install. The bootstrap below is what makes the package importable from there.
"""

import os
import sys

_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402

from gridvibe_mcp.client import DEFAULT_BASE_URL, GridVibeClient  # noqa: E402
from gridvibe_mcp.identity import (  # noqa: E402
    DEFAULT_MAX_AGENT_DEPTH,
    read_identity,
)
from gridvibe_mcp.server import dispatch, tool_specs  # noqa: E402

SERVER_NAME = "gridvibe"

#: The 2.x SDK replaced the low-level server's decorators with an
#: `add_request_handler` registry, so a sidecar written against 1.x would
#: register nothing at all there. Said plainly rather than left as an
#: AttributeError traceback inside an agent CLI's MCP startup log, which is
#: where a user would otherwise meet it.
_WRONG_SDK = (
    "The GridVibe MCP sidecar is written against the 1.x MCP Python SDK, "
    "and the installed one has a different low-level server API.\n"
    "Reinstall the pinned version:\n"
    "    python -m pip install -r requirements-mcp.txt\n"
)

_MISSING_SDK = (
    "The GridVibe MCP sidecar needs the MCP Python SDK.\n"
    "Install it into the interpreter GridVibe runs under:\n"
    "    python -m pip install -r requirements-mcp.txt\n"
)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gridvibe-mcp",
        description="MCP sidecar for a running GridVibe.",
    )
    parser.add_argument(
        "--url",
        default="",
        help=f"GridVibe base URL (default: $GRIDVIBE_URL, else {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Seconds any one loopback call may take.",
    )
    parser.add_argument(
        "--max-agent-depth",
        type=int,
        default=DEFAULT_MAX_AGENT_DEPTH,
        help="How many generations of agent-launched panes are allowed.",
    )
    parser.add_argument(
        "--print-tools",
        action="store_true",
        help="Print the registered tool surface as JSON and exit.",
    )
    return parser.parse_args(argv)


def build_runtime(args: argparse.Namespace):
    """Return the (identity, client) pair every tool call is served from."""
    identity = read_identity(default_url=args.url or DEFAULT_BASE_URL)
    base_url = identity.url or args.url or DEFAULT_BASE_URL
    return identity, GridVibeClient(base_url, timeout=args.timeout)


async def _serve(args: argparse.Namespace) -> int:
    try:
        import mcp.types as types
        from mcp.server.lowlevel import Server
        from mcp.server.stdio import stdio_server
    except ImportError:
        sys.stderr.write(_MISSING_SDK)
        return 2

    identity, client = build_runtime(args)
    server = Server(SERVER_NAME)
    if not hasattr(server, "list_tools") or not hasattr(server, "call_tool"):
        sys.stderr.write(_WRONG_SDK)
        return 2

    @server.list_tools()
    async def list_tools():
        return [
            types.Tool(
                name=spec["name"],
                description=spec["description"],
                inputSchema=spec["inputSchema"],
            )
            for spec in tool_specs()
        ]

    @server.call_tool()
    async def call_tool(name, arguments):
        # Every handler is blocking loopback HTTP, so it runs off the event
        # loop that owns the stdio transport.
        result = await asyncio.to_thread(
            dispatch,
            name,
            arguments or {},
            client=client,
            identity=identity,
            max_agent_depth=args.max_agent_depth,
        )
        return [types.TextContent(type="text", text=json.dumps(result, indent=2))]

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )
    return 0


def main(argv=None) -> int:
    args = parse_args(argv)
    # stdout is the protocol channel; anything chatty goes to stderr.
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    if args.print_tools:
        json.dump(tool_specs(), sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    try:
        return asyncio.run(_serve(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
