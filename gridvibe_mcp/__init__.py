"""The GridVibe MCP sidecar: a stdio MCP server that speaks to a running
GridVibe over its loopback HTTP API.

This package is a *sibling* of GridVibe, not a part of it. Nothing under
``web/`` or ``sessions/`` imports it, and it imports nothing from GridVibe --
it speaks only HTTP. That boundary is what keeps the asyncio-native MCP SDK
out of the threading-mode Flask-SocketIO process.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
