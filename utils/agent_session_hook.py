"""Claude Code ``SessionStart`` hook: tell GridVibe which session this pane is in.

Run by GridVibe's own interpreter in exec form, from the settings file
``web/agent_session_hooks.py`` generates. It reads the hook's JSON from stdin,
and posts the live ``session_id`` to the pane's report route with the pane's
per-connection token -- both of which the pane's shell put in this process's
environment.

Stdlib only and imports nothing from GridVibe: it runs with the user's project
as its working directory. It prints nothing (``SessionStart`` stdout becomes
model context) and always exits 0 (a hook failure must never cost the reader
their session). Everything it cannot do, it simply does not do.
"""

import json
import os
import sys
import urllib.parse
import urllib.request

#: Kept literal rather than imported; ``tests/test_agent_session_hooks.py``
#: pins both to ``web/agent_session_hooks.py`` so they cannot drift.
PANE_TOKEN_VARIABLE = "GRIDVIBE_PANE_TOKEN"
PANE_TOKEN_HEADER = "X-GridVibe-Pane-Token"
REPORT_PATH = "/api/sessions/{session_id}/agent-conversation"

MAX_INPUT_BYTES = 64 * 1024
REQUEST_TIMEOUT_SECONDS = 3.0


def build_report(hook_input, provider):
    """The body to send, or None when there is nothing to say."""
    if not isinstance(hook_input, dict):
        return None
    if hook_input.get("agent_id"):
        return None
    session_id = hook_input.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    report = {"provider": provider, "conversation_id": session_id.strip()}
    source = hook_input.get("source")
    if isinstance(source, str) and source:
        report["source"] = source
    return report


def build_request(report, environ):
    """The request to send, or None when the pane gave this process no route."""
    base_url = str(environ.get("GRIDVIBE_URL") or "").strip().rstrip("/")
    pane_id = str(environ.get("GRIDVIBE_SESSION_ID") or "").strip()
    token = str(environ.get(PANE_TOKEN_VARIABLE) or "").strip()
    if not base_url or not pane_id or not token or report is None:
        return None
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or not parsed.netloc:
        return None
    url = base_url + REPORT_PATH.format(session_id=urllib.parse.quote(pane_id, safe=""))
    return urllib.request.Request(
        url,
        data=json.dumps(report).encode("utf-8"),
        headers={"Content-Type": "application/json", PANE_TOKEN_HEADER: token},
        method="POST",
    )


def _provider_from(argv):
    for index, value in enumerate(argv):
        if value == "--provider" and index + 1 < len(argv):
            return argv[index + 1].strip().lower()
    return ""


def main(argv=None, stdin=None, environ=None, opener=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    stdin = sys.stdin if stdin is None else stdin
    environ = os.environ if environ is None else environ
    try:
        provider = _provider_from(argv)
        if not provider:
            return 0
        hook_input = json.loads(stdin.read(MAX_INPUT_BYTES) or "null")
        request = build_request(build_report(hook_input, provider), environ)
        if request is None:
            return 0
        # Loopback only, and never through a proxy the user's environment names.
        opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            response.read(1024)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
