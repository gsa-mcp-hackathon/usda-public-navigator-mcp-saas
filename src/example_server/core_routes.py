"""Pattern-neutral Core route metadata contract and persistence boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from example_server.persistence_api_client import persistence_api_headers


class CoreRoutePersistenceUnavailable(RuntimeError):
    """Raised when the generic Core route-metadata store cannot be read or written."""

    def __init__(
        self,
        message: str,
        *,
        reason: str = "upstream_unavailable",
        http_status: int | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.http_status = http_status


class CoreRouteMetadata(BaseModel):
    """Versioned mapping from an opaque route and operation to a compatible endpoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str = Field(min_length=1)
    route_version: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    endpoint: HttpUrl
    mcp_tool_name: str = Field(min_length=1)
    protocol: Literal["mcp"] = "mcp"
    lifecycle_state: Literal["active", "inactive"] = "active"
    template_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    tenant_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$")


class CoreRouteSummary(BaseModel):
    """Stable client-facing route identity without target or version details."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    route_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)
    mcp_tool_name: str = Field(min_length=1)
    protocol: Literal["mcp"] = "mcp"
    lifecycle_state: Literal["active", "inactive"]
    template_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$")
    tenant_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{0,63}$")


class CoreRouteInventory(BaseModel):
    """Bounded read-only snapshot of currently active Core route metadata."""

    routes: list[CoreRouteSummary]
    truncated: bool = False
    next_cursor: str | None = None


class CoreRouteRepository(Protocol):
    """Read-only lookup of active, opaque Core route metadata."""

    def check_ready(self) -> None:
        """Raise when the configured route-metadata store is unavailable."""

    def resolve_route(
        self, route_id: str, operation_id: str
    ) -> CoreRouteMetadata | None:
        """Resolve the one current active version for a stable route operation."""

    def list_routes(
        self, page_size: int = 25, cursor: str | None = None
    ) -> CoreRouteInventory:
        """Return a bounded inventory of active routes only."""


class HttpCoreRouteRepository:
    """Read Core route metadata from the controlled Persistence Access API."""

    def __init__(
        self,
        api_url: str,
        token_file: Path,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(api_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Persistence API URL must be an HTTPS origin.")
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError("Persistence API credential is unavailable.") from exc
        if not token:
            raise ValueError("Persistence API credential is empty.")
        self._api_url = api_url.rstrip("/")
        self._client = httpx.Client(
            headers=persistence_api_headers(token),
            timeout=timeout_seconds,
            transport=transport,
        )

    def check_ready(self) -> None:
        """Verify that the route API and its backing store are available."""
        try:
            response = self._client.get(f"{self._api_url}/healthz")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("status") != "ok":
                raise ValueError("Persistence API is not ready.")
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise self._unavailable("readiness", exc) from None

    def resolve_route(
        self, route_id: str, operation_id: str
    ) -> CoreRouteMetadata | None:
        """Resolve a stable route operation through the backend version pointer."""
        try:
            response = self._client.get(
                f"{self._api_url}/v1/core/routes",
                params={
                    "route_id": route_id,
                    "operation_id": operation_id,
                },
            )
            if response.status_code == httpx.codes.NOT_FOUND:
                return None
            response.raise_for_status()
            return CoreRouteMetadata.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise self._unavailable("lookup", exc) from None

    def list_routes(
        self, page_size: int = 25, cursor: str | None = None
    ) -> CoreRouteInventory:
        """Read a bounded route inventory without exposing target endpoints."""
        try:
            response = self._client.get(
                f"{self._api_url}/v1/core/routes",
                params={"limit": page_size, "cursor": cursor}
                if cursor
                else {"limit": page_size},
            )
            response.raise_for_status()
            payload = response.json()
            return CoreRouteInventory.model_validate(payload)
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise self._unavailable("inventory", exc) from None

    @staticmethod
    def _unavailable(
        operation: str, error: Exception
    ) -> CoreRoutePersistenceUnavailable:
        """Classify an upstream failure without retaining response bodies or secrets."""
        reason = "invalid_response"
        status: int | None = None
        if isinstance(error, httpx.HTTPStatusError):
            status = error.response.status_code
            reason = {
                httpx.codes.UNAUTHORIZED: "authentication_failed",
                httpx.codes.FORBIDDEN: "authorization_failed",
            }.get(status, "upstream_http_error")
        elif isinstance(error, httpx.TimeoutException):
            reason = "timeout"
        elif isinstance(error, httpx.ConnectError):
            reason = "connection_failed"
        elif isinstance(error, httpx.HTTPError):
            reason = "transport_failed"
        elif isinstance(error, ValidationError):
            reason = "invalid_response_schema"
        elif isinstance(error, ValueError):
            reason = "invalid_response_json"
        return CoreRoutePersistenceUnavailable(
            f"Persistence API {operation} failed.",
            reason=reason,
            http_status=status,
        )
