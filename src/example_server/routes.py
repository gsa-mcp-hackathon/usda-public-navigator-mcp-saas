"""Custom HTTP routes for the MCP server.

These are only active when the server runs over HTTP (streamable-http). They
sit alongside the `/mcp` endpoint on the same app.

The `/health` route matters for cloud deploys: cloud.gov, IBM Code Engine, and
Databricks Apps all use an HTTP health check to decide the app started
correctly. Keep it fast and dependency-free.
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from example_server import __version__


def register_routes(
    mcp: FastMCP,
    *,
    role: str = "example",
    tenant_id: str = "",
    cluster_id: str = "",
    pattern_version: str = "",
) -> None:
    """Register custom HTTP routes with the server."""

    identity: dict[str, Any] = {"role": role}
    if tenant_id:
        identity["tenant_id"] = tenant_id
    if cluster_id:
        identity["cluster_id"] = cluster_id
    if pattern_version:
        identity["pattern_version"] = pattern_version

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy", "service": "mcp", **identity})

    @mcp.custom_route("/version", methods=["GET"])
    async def version(request: Request) -> JSONResponse:
        return JSONResponse({"version": __version__})
