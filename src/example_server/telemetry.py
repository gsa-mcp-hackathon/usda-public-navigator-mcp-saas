"""Privacy-safe structured telemetry for core routing."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping
from typing import TextIO

_event_stream: TextIO | None = None
_SAFE_ERROR_CLASSES = {
    "endpoint_unconfigured",
    "incompatible_operation",
    "registry_unavailable",
    "tenant_not_eligible",
    "tenant_route_incompatible",
    "tenant_unavailable",
    "unknown_use_case",
}


def configure_event_stream(stream: TextIO) -> None:
    """Route telemetry away from MCP stdio when the app selects a transport."""
    global _event_stream
    _event_stream = stream


def emit_route_event(
    *,
    started_at: float,
    correlation_id: str,
    use_case_id: str | None,
    use_case_version: str | None,
    response: Mapping[str, object],
    error_class: str | None = None,
) -> None:
    """Write an allowlisted JSON event without request or tenant payloads."""
    result_status = str(response.get("status", "unavailable"))
    if error_class not in _SAFE_ERROR_CLASSES:
        error_class = None
    if result_status != "ok" and error_class is None:
        error_class = "tenant_unavailable"

    operation_id = response.get("operation_id")
    results = response.get("results")
    source_ids = (
        sorted(
            {
                str(result["source"])
                for result in results
                if isinstance(result, Mapping)
                and isinstance(result.get("source"), str)
                and result["source"] != "registry"
            }
        )
        if isinstance(results, list)
        else []
    )

    event: dict[str, object] = {
        "event": "mcp.route_use_case",
        "correlation_id": correlation_id,
        "route": "route_use_case",
        "use_case_id": use_case_id,
        "use_case_version": use_case_version,
        "operation_id": operation_id if isinstance(operation_id, str) else None,
        "source_ids": source_ids,
        "latency_ms": round((time.perf_counter() - started_at) * 1000, 3),
        "result_status": result_status,
    }
    if error_class is not None:
        event["error_class"] = error_class

    stream = _event_stream or sys.stderr
    stream.write(json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n")
    stream.flush()
