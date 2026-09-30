"""Credential redaction for GridVibe's log handlers.

A remote pane's agent reaches its tools at ``POST /mcp/<token>``
(``web/api.py``), and that path segment *is* the credential: anyone holding it
can act on this machine through that pane while it is alive. Several writers
put request paths into log records -- werkzeug's access and error lines, Flask's
"Exception on <path>" record, the cross-origin write guard -- and the log is
exactly what gets copied into a bug report.

So the rule is enforced where every record passes, not at each call site:
``RedactMcpTokenFilter`` sits first on each handler ``setup_logging`` installs
(``main.py``), and on every handler a library already attached to its own
logger -- python-engineio and python-socketio each put a stderr handler on
their server loggers at import, and those emit before a record propagates to
the root. A handler filter sees records from every logger, including ones
propagated from children and ones that name the path only in their traceback.
A call site that knows it is logging a path still passes it through
``redact_mcp_path`` (the origin guard does), so its text is right even under a
handler this module never saw.

Only the ``/mcp/<token>`` route is rewritten. ``/api/mcp/...`` is the local
sidecar's API, keyed by pane id rather than by a secret, and stays readable.
"""

import logging
import re
import traceback
from typing import Iterable, List, Optional

#: What a redacted token segment reads as.
REDACTED_MCP_PATH = "/mcp/<redacted>"

# `/mcp` and its separator, then the token: everything up to the next path,
# query or fragment separator, whitespace, quote or angle bracket. The
# separator is matched every way a request line can carry one that still
# reaches, or redirects to, the route: `/`, a repeated `/` (Werkzeug answers
# `/mcp//<token>` with a redirect and logs the original), and `%2F` (routing
# decodes it, the access line keeps it encoded), double-encoded included. Case
# is ignored for the same reason. Anchored so that `/api/mcp/...` -- the
# sidecar's own routes -- is left alone, and so that the literal placeholder
# `/mcp/<token>` in prose (and an already-redacted `/mcp/<redacted>`) does not
# match. A full URL (`http://host:port/mcp/...`) matches too, which is how the
# remote config names it.
_MCP_TOKEN_RE = re.compile(
    r"(?<!/api)/mcp(?:/|%(?:25)*2f)+[^/?#\s\"'<>\\]+", re.IGNORECASE
)


def redact_mcp_path(text) -> str:
    """Return ``text`` with every ``/mcp/<token>`` segment redacted."""
    value = "" if text is None else str(text)
    if "mcp" not in value.lower():
        return value
    return _MCP_TOKEN_RE.sub(REDACTED_MCP_PATH, value)


class RedactMcpTokenFilter(logging.Filter):
    """Rewrite a record whose text carries an MCP token, and pass it on.

    The record is formatted once and its ``msg``/``args`` replaced by the
    redacted text -- the technique ``main._StripAnsiFilter`` uses -- so every
    handler after this one, and the formatter, see only the redacted form. A
    record with an exception or stack gets its traceback text rendered and
    redacted here too, because the formatter appends that after ``msg``.
    Records that carry no token are left exactly as they came.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A malformed record is the formatter's to report; the text it
            # would print is not ours to guess at here.
            return True
        redacted = redact_mcp_path(message)
        if redacted != message:
            record.msg = redacted
            record.args = None
        if record.exc_info and not record.exc_text:
            exc_text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
            redacted_exc = redact_mcp_path(exc_text)
            if redacted_exc != exc_text:
                record.exc_text = redacted_exc
        elif record.exc_text:
            record.exc_text = redact_mcp_path(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_mcp_path(record.stack_info)
        return True


def _every_attached_handler() -> List[logging.Handler]:
    """The root's handlers and every handler attached to a named logger."""
    handlers = list(logging.getLogger().handlers)
    for logger in list(logging.root.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            handlers.extend(logger.handlers)
    return handlers


def install_mcp_token_redaction(
    handlers: Optional[Iterable[logging.Handler]] = None,
) -> None:
    """Put the redaction filter first on each handler, once.

    With no argument, every handler attached anywhere right now: the root's
    and each named logger's. Call it after the handlers exist; a handler a
    library attaches later is not seen.
    """
    if handlers is None:
        handlers = _every_attached_handler()
    for handler in handlers:
        if any(isinstance(f, RedactMcpTokenFilter) for f in handler.filters):
            continue
        handler.filters.insert(0, RedactMcpTokenFilter())
