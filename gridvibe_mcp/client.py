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
    # The dashboard computes a true per-group index and this list used to drop
    # it, which was an oversight rather than a decision: "Terminal 3" is what
    # the reader sees, and a row that cannot say which one it is cannot be
    # pointed at.
    "index",
)

#: Where one pane sits in its group. Published only when a group was resolved:
#: across a whole workspace, array position means nothing and an index would be
#: a number the reader could act on and should not.
POSITION_FIELDS = (
    "index",
    "rect",
    "relative_area",
    "neighbours",
)

#: How the group as a whole is arranged. `layout_advisory` is load-bearing:
#: above three panes GridVibe forces `grid` whatever was asked, so a reader
#: acting on the name alone would be acting on a lie -- the geometry is what
#: holds. `implied` says the rectangles were derived from the preset table
#: rather than read from a record the page wrote.
LAYOUT_FIELDS = (
    "group_id",
    "workspace_id",
    "layout",
    "layout_advisory",
    "terminal_count",
    "geometry",
)

#: A saved preset's *shape*, and never its connection. The saved-session route
#: answers with a decrypted SSH password by design, so this list is what stops
#: it: shape in, connection out, and `scrub()` behind it as the second line.
SAVED_LAYOUT_FIELDS = (
    "id",
    "name",
    "layout",
    "terminal_count",
    "is_default",
)

#: Per-pane, out of a preset's stored terminal entries. No host, no username,
#: no port, no directory that names a machine -- a preset launched by an agent
#: runs on the agent's own pane's machine through `origin_session_id`.
SAVED_LAYOUT_PANE_FIELDS = (
    "startup_mode",
    "title",
    "agent_selection",
)

#: The geometry record itself, as `_normalize_workspace_layout` validates it.
GEOMETRY_FIELDS = (
    "class_name",
    "split_slot_rects",
    "split_column_weights",
    "split_row_weights",
    "original_split_slot_count",
)

#: What a clear answers. Two fields rather than one `cleared: true`, because
#: they are not the same kind of claim: the replay buffer is gone, and the
#: windows showing the pane have been *told* to reset their display. A pane
#: nobody has open resets nothing, and this list is what stops the result from
#: pretending otherwise.
CLEAR_FIELDS = (
    "session_id",
    "buffer_purged",
    "display_reset_requested",
)

#: A ceiling on the per-preset reads `list_saved_layouts` makes, so a store
#: holding hundreds of presets cannot turn one tool call into hundreds of
#: requests. The result says when it stopped.
MAX_SAVED_LAYOUTS = 25


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

    def panes(
        self,
        workspace_id: str = "",
        group_id: str = "",
        position_group_id: str = "",
    ) -> Dict[str, Any]:
        """Every pane in scope, and where the panes of one group sit.

        ``position_group_id`` is the group whose arrangement is resolved. It is
        usually ``group_id``, and is the caller's own group when the read is
        workspace-wide -- an agent asking "what is here" is asking about the
        panes around it, and those are in its own group. Every pane outside
        that group carries ``index: None`` and no rectangle, because array
        position across groups means nothing.
        """
        params = {}
        if workspace_id:
            params["workspace_id"] = workspace_id
        if group_id:
            params["group_id"] = group_id
        payload = self.request("GET", "/api/sessions", params=params)
        raw = payload.get("sessions") if isinstance(payload, Mapping) else None
        panes = project_all(raw, PANE_FIELDS)

        result: Dict[str, Any] = {"panes": panes, "count": len(panes)}
        resolved_group = str(position_group_id or group_id or "").strip()
        if not resolved_group:
            for pane in panes:
                pane["index"] = None
            return result

        layout = self.pane_layout(resolved_group)
        positions = {
            str(entry.get("session_id") or ""): entry
            for entry in (layout.get("panes") or [])
        }
        if not positions:
            # The arrangement could not be read -- the group has gone, or the
            # read failed. An empty layout block would read as "no layout",
            # which is a different answer from "not read", so none is published.
            for pane in panes:
                pane["index"] = None
            return result
        for pane in panes:
            position = positions.get(str(pane.get("session_id") or ""))
            if position is None:
                # In the workspace but not in the group whose geometry was
                # read. Stating `None` rather than omitting the key: "I do not
                # know where this is" is an answer, and a missing key reads as
                # an oversight.
                pane["index"] = None
                continue
            pane.update(project(position, POSITION_FIELDS))
        result["layout"] = project(layout, LAYOUT_FIELDS)
        return result

    def pane_layout(self, group_id: str) -> Dict[str, Any]:
        """How one group's panes are arranged, and which pane is next to which.

        A failed read is not a failed ``list_panes``: an agent asking what is
        open still gets the panes, without the positions. So the geometry read
        degrades to an empty answer rather than raising through its caller.
        """
        try:
            payload = self.request(
                "GET",
                "/api/panes/layout",
                params={"group_id": group_id},
            )
        except GridVibeError:
            return {}
        return payload if isinstance(payload, Mapping) else {}

    def pane(self, session_id: str) -> Dict[str, Any]:
        payload = self.request("GET", f"/api/sessions/{urllib.parse.quote(session_id)}")
        return project(payload, PANE_FIELDS)

    def saved_layouts(self) -> Dict[str, Any]:
        """Every saved launcher preset, as shape and never as a connection.

        Two reads per preset because the list route answers with no config and
        the single route answers with one -- including a **decrypted** SSH
        password, which is exactly why the projection below is a field list and
        not a pass-through. Bounded: a store holding more presets than
        ``MAX_SAVED_LAYOUTS`` stops there and the result says so.
        """
        payload = self.request("GET", "/api/saved-sessions")
        entries = payload.get("sessions") if isinstance(payload, Mapping) else None
        rows: List[Dict[str, Any]] = []
        truncated = False
        for entry in entries or []:
            if not isinstance(entry, Mapping):
                continue
            if len(rows) >= MAX_SAVED_LAYOUTS:
                truncated = True
                break
            row = project(entry, SAVED_LAYOUT_FIELDS)
            preset_id = str(entry.get("id") or "")
            if preset_id:
                row.update(self._saved_layout_shape(preset_id))
            rows.append(row)
        result: Dict[str, Any] = {"layouts": rows, "count": len(rows)}
        if truncated:
            result["truncated_at"] = MAX_SAVED_LAYOUTS
        return result

    def _saved_layout_shape(self, saved_session_id: str) -> Dict[str, Any]:
        """The geometry and the per-pane shape of one preset, and nothing else."""
        try:
            payload = self.request(
                "GET",
                f"/api/saved-sessions/{urllib.parse.quote(saved_session_id)}",
            )
        except GridVibeError:
            return {}
        config = payload.get("config") if isinstance(payload, Mapping) else None
        if not isinstance(config, Mapping):
            return {}
        try:
            count = max(0, int(config.get("terminal_count") or 0))
        except (TypeError, ValueError):
            count = 0
        terminals = config.get("terminals")
        panes = project_all(
            list(terminals)[:count] if isinstance(terminals, list) else None,
            SAVED_LAYOUT_PANE_FIELDS,
        )
        shape: Dict[str, Any] = {"panes": panes}
        geometry = config.get("workspace_layout")
        shape["workspace_layout"] = (
            project(geometry, GEOMETRY_FIELDS) if isinstance(geometry, Mapping) else None
        )
        return shape

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

    def split_intent(self, session_id: str, body: Mapping[str, Any]) -> Dict[str, Any]:
        """Record "please split this pane" for an open page to perform.

        Refusals that can be decided without measuring a pane -- an unknown
        agent, a browser pane on a remote host, an axis that is not one of the
        two -- come back from this call, before any waiting starts.
        """
        payload = self.request(
            "POST",
            f"/api/sessions/{urllib.parse.quote(session_id)}/split-intent",
            body=dict(body),
        )
        return payload if isinstance(payload, dict) else {}

    def relaunch_as_agent(
        self,
        session_id: str,
        body: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Relaunch one pane into an agent, through the gated route.

        Deliberately not ``POST /api/sessions/<id>/shell``: that route is the
        pane header's own dropdown and checks nobody, because the person
        pressing it is looking at the pane. The gates live on the route this
        calls, in GridVibe's own process, so a tool cannot reach past them.
        """
        payload = self.request(
            "POST",
            f"/api/sessions/{urllib.parse.quote(session_id)}/agent-relaunch",
            body=dict(body),
        )
        return project(payload, PANE_FIELDS)

    def switch_pane_mode(
        self,
        session_id: str,
        body: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Switch one pane between terminal, explorer and browser modes.

        Deliberately not ``POST /api/sessions/<id>/mode``: that route is the
        pane header's own toggle and checks nobody, because the person pressing
        it is looking at the pane. The gates live on the route this calls, in
        GridVibe's own process, so a tool cannot reach past them.
        """
        payload = self.request(
            "POST",
            f"/api/sessions/{urllib.parse.quote(session_id)}/agent-mode-switch",
            body=dict(body),
        )
        return project(payload, PANE_FIELDS)

    def clear_pane(
        self,
        session_id: str,
        body: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Purge one pane's replay buffer and ask its windows to reset it.

        The header's Clear button is not a route -- it is an xterm reset plus a
        socket event -- so this is the gated equivalent, and the same three
        gates the relaunch passes stand in front of it.
        """
        payload = self.request(
            "POST",
            f"/api/sessions/{urllib.parse.quote(session_id)}/clear",
            body=dict(body),
        )
        return project(payload, CLEAR_FIELDS)

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
