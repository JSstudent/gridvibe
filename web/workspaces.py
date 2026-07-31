"""Shared workspace identity and public-payload helpers.

The module deliberately stays independent from Flask and SessionManager so the
session model, HTTP boundary, Socket.IO rooms, and native bridge all enforce the
same opaque-id contract without creating import cycles.
"""

import re
import uuid
from typing import Any, Dict

DEFAULT_WORKSPACE_ID = "default"
_WORKSPACE_ID_PATTERN = re.compile(r"^[a-z0-9]{12}$")


def normalize_workspace_id(value: Any = None) -> str:
    """Return a valid opaque workspace id, defaulting omitted values.

    Only the permanent ``default`` workspace and generated-style twelve
    character lowercase alphanumeric ids are accepted. Room names and storage
    keys must only ever be derived from this normalized value.
    """
    workspace_id = str(value or DEFAULT_WORKSPACE_ID).strip()
    if workspace_id == DEFAULT_WORKSPACE_ID or _WORKSPACE_ID_PATTERN.fullmatch(workspace_id):
        return workspace_id
    raise ValueError("Invalid workspace id")


def generate_workspace_id() -> str:
    """Generate an opaque workspace id matching :func:`normalize_workspace_id`."""
    return uuid.uuid4().hex[:12]


def workspace_room(workspace_id: Any = None) -> str:
    """Return the Socket.IO room for a normalized workspace id."""
    return f"workspace:{normalize_workspace_id(workspace_id)}"


def public_workspace_payload(workspace: Any, group_count: int = 0) -> Dict[str, Any]:
    """Return the credential-free public summary for one live workspace."""
    return {
        "workspace_id": normalize_workspace_id(getattr(workspace, "workspace_id", None)),
        "label": str(getattr(workspace, "label", "") or "").strip(),
        "created_at": float(getattr(workspace, "created_at", 0.0) or 0.0),
        "active_group_id": str(getattr(workspace, "active_group_id", "") or "").strip(),
        "group_count": max(0, int(group_count)),
    }
