"""Bounded privacy-safe Core route telemetry over the Persistence Access API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from example_server.persistence_api_client import persistence_api_headers


class RouteEvent(BaseModel):
    """Allowlisted routing outcome; request and response content are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_id: UUID
    route_id: str | None = Field(default=None, min_length=1, max_length=129)
    route_version: str | None = Field(default=None, min_length=1, max_length=32)
    operation_id: str | None = Field(default=None, min_length=1, max_length=128)
    template_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    tenant_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    result_status: Literal[
        "ok",
        "unknown_route",
        "incompatible",
        "invalid_request",
        "downstream_error",
        "unavailable",
    ]
    latency_ms: float = Field(ge=0, le=600_000)
    error_class: (
        Literal[
            "registry_unavailable",
            "tenant_unavailable",
            "unknown_route",
            "incompatible_route",
            "tenant_argument_validation",
            "tenant_tool_error",
        ]
        | None
    ) = None
    package_id: UUID | None = None


class RouteTelemetryRepository:
    """Write allowlisted route events and read operator reports via the API."""

    def __init__(
        self,
        api_url: str,
        read_token_file: Path,
        write_token_file: Path,
        *,
        timeout_seconds: float = 1.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(api_url)
        if (
            not (
                parsed.scheme == "https"
                or (
                    parsed.scheme == "http"
                    and parsed.hostname == "mcp-saas-vm-data-api"
                    and parsed.port == 8080
                )
            )
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Persistence API URL must be an HTTPS origin.")
        self._base_url = api_url.rstrip("/")
        self._read_client = httpx.Client(
            headers=persistence_api_headers(self._read_token(read_token_file)),
            timeout=timeout_seconds,
            transport=transport,
        )
        self._write_client = httpx.Client(
            headers=persistence_api_headers(self._read_token(write_token_file)),
            timeout=timeout_seconds,
            transport=transport,
        )

    @staticmethod
    def _read_token(path: Path) -> str:
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Persistence API credential is unavailable.") from exc
        if not token:
            raise ValueError("Persistence API credential is empty.")
        return token

    def record_event(self, event: RouteEvent) -> None:
        """Persist one allowlisted event; callers may safely suppress API errors."""
        try:
            response = self._write_client.post(
                f"{self._base_url}/v1/ops/route-events",
                json=_event_payload(event),
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RouteTelemetryUnavailable("Route event write failed.") from exc

    def report(self, from_utc: str, to_utc: str) -> dict[str, object]:
        """Return bounded aggregates for an inclusive UTC time interval."""
        start = _parse_utc(from_utc)
        end = _parse_utc(to_utc)
        if end <= start or end - start > timedelta(days=31):
            raise ValueError("Report window must be positive and at most 31 days.")
        query = urlencode({"from_utc": start.isoformat(), "to_utc": end.isoformat()})
        try:
            response = self._read_client.get(
                f"{self._base_url}/v1/ops/route-events?{query}"
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("status") != "available":
                raise ValueError("Route telemetry report has an invalid structure.")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            raise RouteTelemetryUnavailable(
                "Route telemetry report is unavailable."
            ) from exc


def _event_payload(event: RouteEvent) -> dict[str, object]:
    """Serialize an event; omit the optional package id when there is none so an
    older Data API that does not know the field keeps accepting the event."""
    payload = event.model_dump(mode="json")
    if payload.get("package_id") is None:
        payload.pop("package_id", None)
    return payload


class RouteTelemetryUnavailable(RuntimeError):
    """The Persistence Access API could not record or read route telemetry."""


def _parse_utc(value: str) -> datetime:
    """Require an explicit UTC ISO-8601 timestamp."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("Report bounds must be ISO-8601 UTC timestamps.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError("Report bounds must be ISO-8601 UTC timestamps.")
    return parsed.astimezone(timezone.utc)
