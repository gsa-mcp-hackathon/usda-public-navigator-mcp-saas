"""Validation for privacy-safe operator request correlation metadata."""

from __future__ import annotations

import re

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CREDENTIAL_LABEL = "core_operator_token"


def validate_operator_audit_context(value: object) -> dict[str, str | None] | None:
    """Validate Core-derived correlation hashes and the fixed credential label.

    Client IDs and arbitrary actor labels are deliberately not accepted. The
    hashes correlate a lifecycle event to transport session/request logs without
    persisting raw session identifiers.
    """
    if value is None:
        return None
    fields = {
        "mcp_session_sha256",
        "mcp_request_sha256",
        "mcp_client_identity_status",
        "operator_credential_label",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("Operator audit context fields are invalid.")
    session_hash = value["mcp_session_sha256"]
    request_hash = value["mcp_request_sha256"]
    label = value["operator_credential_label"]
    client_identity_status = value["mcp_client_identity_status"]
    for item in (session_hash, request_hash):
        if item is not None and (
            not isinstance(item, str) or _SHA256.fullmatch(item) is None
        ):
            raise ValueError("Operator audit correlation hash is invalid.")
    if label != _CREDENTIAL_LABEL:
        raise ValueError("Operator credential label is invalid.")
    if client_identity_status != "unavailable":
        raise ValueError("Trusted MCP client identity is unavailable.")
    return {
        "mcp_session_sha256": session_hash,
        "mcp_request_sha256": request_hash,
        "mcp_client_identity_status": client_identity_status,
        "operator_credential_label": label,
    }
