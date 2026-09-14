"""Bounded loopback HTTP to a running GridVibe, and the field allowlists.

Two rules live here rather than in a tool handler, so that a tool added later
cannot forget either of them:

* **Every result is built from an explicit field list.** Never a pass-through
  of ``session.to_dict()``. The precedent copied literally is ``PANE_FIELDS``
  in ``web/dashboard.py``: a pane payload names what it is, where it points and
  what it runs on, and nothing else.
* **Every failure is typed and carries GridVibe's own sentence verbatim.** A
  409 on a taken workspace name is the server's wording, unretried.
"""

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Mapping, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:5050"
DEFAULT_TIMEOUT_SECONDS = 15.0

#: Anything whose key contains one of these never reaches a tool result, at any
#: depth, whatever the allowlist below says. A session carries an *encrypted*
#: SSH password; encrypted is still not something an agent needs to see.
FORBIDDEN_KEY_MARKERS = ("password", "passphrase", "secret", "token", "encryption_key")

WORKSPACE_FIELDS = (
    "workspace_id",
    "label",
    "active_group_id",
    "group_count",
)

GROUP_FIELDS = (
    "group_id",
    "name",
    "workspace_id",
    "layout",
    "connection_mode",
    "terminal_count",
)

#: What a pane is, where it points, and what it runs on.
PANE_FIELDS = (
    "session_id",
    "group_id",
    "title",
    "host",
    "mode",
    "status",
    "startup_mode",
    "directory",
    "current_directory",
    "agent_selection",
    "custom_agent",
    "agent_auto_mode",
    "agent_mcp",
    "use_wsl",
    "use_powershell",
    "distribution",
)

#: The dashboard's agent rows are already a published field list; the sidecar
#: narrows them to what an agent asking "what is running" can act on.
AGENT_FIELDS = (
    "session_id",
    "group_id",
    "title",
    "agent_selection",
    "custom_agent",
    "agent_auto_mode",
    "agent_mcp",
    "activity",
    "status",
    "host",
    "mode",
)


class GridVibeError(Exception):
    """A typed failure from the loopback call, with GridVibe's own sentence.

    ``kind`` is one of ``unreachable``, ``timeout``, ``http`` or ``invalid``,
    so a tool can report *why* without parsing the message.
    """

    def __init__(self, message: str, *, kind: str = "http", status: int = 0) -> None:
        super().__init__(str(message))
        self.kind = kind
        self.status = int(status or 0)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"error": str(self), "kind": self.kind}
        if self.status:
            payload["status"] = self.status
        return payload


def scrub(value: Any) -> Any:
    """Drop every forbidden key from a payload, at any depth."""
    if isinstance(value, dict):
        return {
            key: scrub(item)
            for key, item in value.items()
            if not any(marker in str(key).lower() for marker in FORBIDDEN_KEY_MARKERS)
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def project(payload: Any, fields: Iterable[str]) -> Dict[str, Any]:
    """Keep only the named fields of one mapping, scrubbed."""
    if not isinstance(payload, Mapping):
        return {}
    allowed = tuple(fields)
    return scrub({name: payload.get(name) for name in allowed if name in payload})


def project_all(payloads: Any, fields: Iterable[str]) -> List[Dict[str, Any]]:
    """Project every mapping in a list."""
    if not isinstance(payloads, list):
        return []
    return [project(item, fields) for item in payloads]


def normalize_base_url(url: Any) -> str:
    """Return a bare ``scheme://host:port`` with no trailing slash."""
    text = str(url or "").strip()
    if not text:
        return DEFAULT_BASE_URL
    if "://" not in text:
        text = f"http://{text}"
    parsed = urllib.parse.urlsplit(text)
    if not parsed.hostname:
        return DEFAULT_BASE_URL
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme or 'http'}://{parsed.hostname}{port}"


class GridVibeClient:
    """Every call GridVibe's HTTP API is asked for, and nothing else.

    One place for the timeout, the typed error and the allowlist, so a tool
    handler holds no HTTP and no policy.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        opener: Optional[Any] = None,
    ) -> None:
        self.base_url = normalize_base_url(base_url)
        self.timeout = max(0.1, float(timeout))
        # Injected in tests; urllib's own opener otherwise.
        self._opener = opener or urllib.request.build_opener()

    # ---------------- transport ----------------

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Optional[Mapping[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """Issue one bounded request and return the decoded JSON body."""
        deadline = timeout or self.timeout
        url = f"{self.base_url}{path}"
        query = {
            str(key): str(value)
            for key, value in (params or {}).items()
            if str(value or "").strip()
        }
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            # The cross-origin write guard compares Origin against the bound
            # address; a loopback caller states the one it is calling.
            headers["Origin"] = self.base_url
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=str(method or "GET").upper(),
        )
        try:
            with self._opener.open(request, timeout=deadline) as response:
                return self._decode(response.read())
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, socket.timeout):
                raise self._timeout(deadline) from None
            raise GridVibeError(
                f"GridVibe is not reachable at {self.base_url} ({reason}).",
                kind="unreachable",
            ) from None
        except socket.timeout:
            raise self._timeout(deadline) from None
        except OSError as exc:
            raise GridVibeError(
                f"GridVibe is not reachable at {self.base_url} ({exc}).",
                kind="unreachable",
            ) from None

    @staticmethod
    def _timeout(deadline: float) -> GridVibeError:
        return GridVibeError(
            f"GridVibe did not answer within {deadline:g}s.",
            kind="timeout",
        )

    @staticmethod
    def _decode(raw: bytes) -> Any:
        try:
            return json.loads(raw.decode("utf-8") or "null")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GridVibeError(
                "GridVibe answered with something that is not JSON.",
                kind="invalid",
            ) from None

    @staticmethod
    def _http_error(exc: urllib.error.HTTPError) -> GridVibeError:
        """Carry GridVibe's own sentence through, never a paraphrase."""
        message = ""
        try:
            payload = json.loads(exc.read().decode("utf-8") or "null")
            if isinstance(payload, Mapping):
                message = str(payload.get("error") or "").strip()
        except Exception:  # noqa: BLE001 - an unparseable body is not the error
            message = ""
        return GridVibeError(
            message or f"GridVibe refused the request ({exc.code}).",
            kind="http",
            status=int(exc.code),
        )

    # ---------------- read ----------------

    def health(self) -> Dict[str, Any]:
        payload = self.request("GET", "/api/health")
        return project(
            payload,
            ("status", "service", "version", "window_mode"),
        )

    def workspaces(self) -> List[Dict[str, Any]]:
        payload = self.request("GET", "/api/workspaces")
        raw = payload.get("workspaces") if isinstance(payload, Mapping) else None
        return project_all(raw, WORKSPACE_FIELDS)

    def panes(self, workspace_id: str = "", group_id: str = "") -> Dict[str, Any]:
        params = {}
        if workspace_id:
            params["workspace_id"] = workspace_id
        if group_id:
            params["group_id"] = group_id
        payload = self.request("GET", "/api/sessions", params=params)
        raw = payload.get("sessions") if isinstance(payload, Mapping) else None
        panes = project_all(raw, PANE_FIELDS)
        return {"panes": panes, "count": len(panes)}

    def pane(self, session_id: str) -> Dict[str, Any]:
        payload = self.request("GET", f"/api/sessions/{urllib.parse.quote(session_id)}")
        return project(payload, PANE_FIELDS)

    def agents(self) -> Dict[str, Any]:
        """Every agent anywhere, flattened to rows an agent can act on."""
        payload = self.request("GET", "/api/dashboard")
        rows: List[Dict[str, Any]] = []
        workspaces = payload.get("workspaces") if isinstance(payload, Mapping) else None
        for workspace in workspaces or []:
            if not isinstance(workspace, Mapping):
                continue
            for group in workspace.get("groups") or []:
                if not isinstance(group, Mapping):
                    continue
                for pane in group.get("panes") or []:
                    if not isinstance(pane, Mapping):
                        continue
                    row = project(pane, AGENT_FIELDS)
                    row["workspace_id"] = str(workspace.get("workspace_id") or "")
                    row["workspace_label"] = str(workspace.get("label") or "")
                    row["session_name"] = str(group.get("name") or "")
                    rows.append(row)
        return {"agents": rows, "count": len(rows)}

    # ---------------- create ----------------

    def create_workspace(self, label: str) -> Dict[str, Any]:
        payload = self.request("POST", "/api/workspaces", body={"label": label})
        return project(payload, WORKSPACE_FIELDS)

    def launch(self, body: Mapping[str, Any]) -> Dict[str, Any]:
        payload = self.request("POST", "/api/sessions", body=dict(body))
        return self._launch_result(payload)

    def split(self, session_id: str, body: Mapping[str, Any]) -> Dict[str, Any]:
        payload = self.request(
            "POST",
            f"/api/sessions/{urllib.parse.quote(session_id)}/split",
            body=dict(body),
        )
        return self._launch_result(payload)

    @staticmethod
    def _launch_result(payload: Any) -> Dict[str, Any]:
        if not isinstance(payload, Mapping):
            return {"panes": [], "count": 0}
        result: Dict[str, Any] = {
            "workspace_id": str(payload.get("workspace_id") or ""),
            "group_id": str(payload.get("group_id") or ""),
            "panes": project_all(payload.get("sessions"), PANE_FIELDS),
        }
        result["count"] = len(result["panes"])
        warnings = payload.get("agent_warnings") or payload.get("warnings")
        if isinstance(warnings, list) and warnings:
            result["warnings"] = [str(item) for item in warnings]
        return result

    # ---------------- window intents ----------------

    def open_window_intent(self, workspace_id: str, group_id: str = "") -> Dict[str, Any]:
        body: Dict[str, Any] = {"workspace_id": workspace_id}
        if group_id:
            body["group_id"] = group_id
        payload = self.request("POST", "/api/windows/open", body=body)
        return payload if isinstance(payload, dict) else {}

    def read_window_intent(self, intent_id: str) -> Dict[str, Any]:
        payload = self.request(
            "GET",
            f"/api/windows/intents/{urllib.parse.quote(intent_id)}",
        )
        return payload if isinstance(payload, dict) else {}
