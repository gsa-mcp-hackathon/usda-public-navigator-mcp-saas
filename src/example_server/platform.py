"""Build the core/router or a separately configured tenant MCP application."""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from fastmcp import Client, FastMCP

from clusters.eligibility_persistence import HttpCriteriaRulesetRepository
from clusters.eligibility_profile_projection import ProfileFixtureReader
from clusters.eligibility_public_search import PublicSearchProvider
from example_server.models import (
    PatternRouteResult,
    TenantProgram,
    UseCaseRouteResult,
)
from example_server.registry import (
    RegistryLookupUnavailable,
    RegistryRepository,
    ResolvedUseCase,
)
from example_server.storage import CatalogRepository
from example_server.telemetry import emit_route_event


def create_server(
    *,
    role: str,
    tenant_id: str = "",
    cluster_id: str = "",
    pattern_version: str = "",
    cluster_config: Mapping[str, object] | None = None,
    programs: list[TenantProgram] | None = None,
    cluster_mcp_endpoints: Mapping[str, str] | None = None,
    catalog_repository: CatalogRepository | None = None,
    criteria_ruleset_repository: HttpCriteriaRulesetRepository | None = None,
    profile_fixture_reader: ProfileFixtureReader | None = None,
    public_search_provider: PublicSearchProvider | None = None,
    registry_repository: RegistryRepository | None = None,
    version: str | None = None,
) -> FastMCP:
    """Build one runtime role; tenant deployments are separate applications."""
    if role not in {"example", "core", "cluster", "tenant"}:
        raise ValueError(f"Unsupported MCP_ROLE: {role}")
    if role == "tenant" and (not tenant_id or not cluster_id or not pattern_version):
        raise ValueError(
            "Tenant runtimes require TENANT_ID, CLUSTER_ID, and PATTERN_VERSION."
        )
    if role == "cluster" and (not cluster_id or not pattern_version):
        raise ValueError("Cluster runtimes require CLUSTER_ID and PATTERN_VERSION.")
    app = FastMCP(
        name="Example MCP Server" if role == "example" else f"MCP {role.title()}",
        version=version,
    )
    from example_server.routes import register_routes

    register_routes(
        app,
        role=role,
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        pattern_version=pattern_version,
    )
    if role == "tenant":
        from clusters.registry import register_cluster_tools

        register_cluster_tools(
            app,
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            pattern_version=pattern_version,
            programs=programs or [],
            catalog_repository=catalog_repository,
            cluster_mcp_endpoints=cluster_mcp_endpoints or {},
            criteria_ruleset_repository=criteria_ruleset_repository,
            cluster_config=cluster_config or {},
            profile_fixture_reader=profile_fixture_reader,
            public_search_provider=public_search_provider,
        )

    elif role == "cluster":
        from clusters.registry import register_cluster_inventory_tools

        register_cluster_inventory_tools(
            app,
            cluster_id=cluster_id,
            pattern_version=pattern_version,
            catalog_repository=catalog_repository,
            public_search_provider=public_search_provider,
            criteria_ruleset_repository=criteria_ruleset_repository,
        )

    elif role == "core":
        configured_cluster_endpoints = dict(cluster_mcp_endpoints or {})

        @app.tool(name="route_use_case")
        async def route_use_case(
            use_case_id: str,
            use_case_version: str,
            arguments: dict[str, Any] | None = None,
            tenant_ids: list[str] | None = None,
        ) -> dict[str, object]:
            """Route a versioned use case to compatible registered tenant operations."""
            started_at = time.perf_counter()
            correlation_id = str(uuid.uuid4())
            resolved: ResolvedUseCase | None = None
            base = {
                "use_case_id": use_case_id,
                "use_case_version": use_case_version,
                "correlation_id": correlation_id,
            }

            def finish(
                result: dict[str, object], error_class: str | None = None
            ) -> dict[str, object]:
                canonical_use_case = resolved.use_case if resolved is not None else None
                emit_route_event(
                    started_at=started_at,
                    correlation_id=correlation_id,
                    use_case_id=(
                        canonical_use_case.use_case_id
                        if canonical_use_case is not None
                        else None
                    ),
                    use_case_version=(
                        canonical_use_case.use_case_version
                        if canonical_use_case is not None
                        else None
                    ),
                    response=result,
                    error_class=error_class,
                )
                return result

            if registry_repository is None:
                return finish(
                    UseCaseRouteResult(**base, status="unavailable").model_dump(
                        mode="json", exclude_none=True
                    ),
                    "registry_unavailable",
                )
            try:
                resolved = await asyncio.to_thread(
                    registry_repository.resolve_use_case,
                    use_case_id,
                    use_case_version,
                )
            except RegistryLookupUnavailable:
                return finish(
                    UseCaseRouteResult(**base, status="unavailable").model_dump(
                        mode="json", exclude_none=True
                    ),
                    "registry_unavailable",
                )
            if resolved is None:
                return finish(
                    UseCaseRouteResult(**base, status="unknown_use_case").model_dump(
                        mode="json", exclude_none=True
                    ),
                    "unknown_use_case",
                )

            operation = next(
                (
                    item
                    for item in resolved.pattern.operations
                    if item.operation_id == resolved.use_case.operation_id
                ),
                None,
            )
            if operation is None or (
                operation.access_mode == "idempotent_write"
                and operation.route_scope != "cluster"
            ):
                return finish(
                    _resolved_result(resolved, correlation_id, "incompatible"),
                    "incompatible_operation",
                )
            if operation.route_scope == "cluster":
                endpoint = configured_cluster_endpoints.get(
                    resolved.use_case.pattern_id
                )
                parsed = urlparse(endpoint or "")
                if (
                    endpoint is None
                    or parsed.scheme not in {"http", "https"}
                    or not parsed.netloc
                ):
                    status = "unavailable" if endpoint is None else "incompatible"
                    route_result = PatternRouteResult(
                        status=status,
                        cluster_id=resolved.use_case.pattern_id,
                        pattern_id=resolved.use_case.pattern_id,
                        pattern_version=resolved.use_case.pattern_version,
                        operation_id=operation.operation_id,
                        source="cluster_route_config",
                        error=(
                            "The active cluster endpoint is not configured."
                            if endpoint is None
                            else "The active cluster endpoint is invalid."
                        ),
                    )
                    return finish(
                        _resolved_result(
                            resolved,
                            correlation_id,
                            status,
                            results=[route_result],
                        ),
                        "cluster_route_not_configured"
                        if endpoint is None
                        else "cluster_route_invalid",
                    )
                try:
                    async with Client(endpoint, timeout=8.0) as client:
                        response = await client.call_tool(
                            operation.mcp_tool_name, arguments or {}
                        )
                    if response.is_error:
                        raise RuntimeError("cluster operation returned an error")
                    route_result = PatternRouteResult(
                        status="ok",
                        cluster_id=resolved.use_case.pattern_id,
                        pattern_id=resolved.use_case.pattern_id,
                        pattern_version=resolved.use_case.pattern_version,
                        operation_id=operation.operation_id,
                        source="active_cluster_route",
                        result=response.data,
                    )
                    return finish(
                        _resolved_result(
                            resolved,
                            correlation_id,
                            "ok",
                            results=[route_result],
                        )
                    )
                except Exception:
                    route_result = PatternRouteResult(
                        status="unavailable",
                        cluster_id=resolved.use_case.pattern_id,
                        pattern_id=resolved.use_case.pattern_id,
                        pattern_version=resolved.use_case.pattern_version,
                        operation_id=operation.operation_id,
                        source="active_cluster_route",
                        error="The active cluster operation is unavailable.",
                    )
                    return finish(
                        _resolved_result(
                            resolved,
                            correlation_id,
                            "unavailable",
                            results=[route_result],
                        ),
                        "cluster_route_unavailable",
                    )
            registered = {tenant.tenant_id: tenant for tenant in resolved.tenants}
            requested = set(tenant_ids or registered)
            if not requested:
                return finish(
                    _resolved_result(resolved, correlation_id, "unavailable"),
                    "no_compatible_route_endpoint",
                )
            unknown = requested - registered.keys()
            if unknown:
                return finish(
                    UseCaseRouteResult(
                        **base,
                        status="incompatible",
                        pattern_id=resolved.use_case.pattern_id,
                        pattern_version=resolved.use_case.pattern_version,
                        operation_id=resolved.use_case.operation_id,
                        results=[
                            PatternRouteResult(
                                status="incompatible",
                                tenant_id=tenant_id,
                                pattern_id=resolved.use_case.pattern_id,
                                pattern_version=resolved.use_case.pattern_version,
                                operation_id=resolved.use_case.operation_id,
                                source="registry",
                                error="Tenant is not eligible for this use case.",
                            )
                            for tenant_id in sorted(unknown)
                        ],
                    ).model_dump(mode="json", exclude_none=True),
                    "tenant_not_eligible",
                )

            async def fetch(tenant_id: str) -> PatternRouteResult:
                tenant = registered[tenant_id]
                endpoint = str(tenant.endpoint)
                parsed = urlparse(endpoint)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    return PatternRouteResult(
                        status="incompatible",
                        tenant_id=tenant_id,
                        pattern_id=tenant.pattern_id,
                        pattern_version=tenant.pattern_version,
                        operation_id=operation.operation_id,
                        source="registry",
                        error="Tenant endpoint is not a valid HTTP URL.",
                    )
                try:
                    async with Client(endpoint, timeout=5.0) as client:
                        response = await client.call_tool(
                            operation.mcp_tool_name, arguments or {}
                        )
                    if response.is_error:
                        raise RuntimeError("tenant operation returned an error")
                    return PatternRouteResult(
                        status="ok",
                        tenant_id=tenant_id,
                        pattern_id=tenant.pattern_id,
                        pattern_version=tenant.pattern_version,
                        operation_id=operation.operation_id,
                        source=tenant.registration_id,
                        result=response.data,
                    )
                except Exception:
                    return PatternRouteResult(
                        status="unavailable",
                        tenant_id=tenant_id,
                        pattern_id=tenant.pattern_id,
                        pattern_version=tenant.pattern_version,
                        operation_id=operation.operation_id,
                        source=tenant.registration_id,
                        error="Tenant operation unavailable.",
                    )

            results = await asyncio.gather(*(fetch(item) for item in sorted(requested)))
            status = (
                "ok" if all(item.status == "ok" for item in results) else "unavailable"
            )
            error_class = None
            if status != "ok":
                error_class = (
                    "tenant_route_incompatible"
                    if any(item.status == "incompatible" for item in results)
                    else "tenant_unavailable"
                )
            return finish(
                _resolved_result(resolved, correlation_id, status, results=results),
                error_class,
            )

    else:
        # Default local role: one synthetic directory so a first run has a tool.
        from clusters.sample_directory import register_tools as register_directory_tools

        register_directory_tools(
            app,
            tenant_id=tenant_id or "local-demo",
            cluster_id=cluster_id or "sample-directory",
            pattern_version=pattern_version or "1.0.0",
            programs=[],
            catalog_repository=None,
            cluster_config={
                "entries": [{"id": "sample-a", "label": "Synthetic Directory Alpha"}]
            },
        )
    return app


def _resolved_result(
    resolved: ResolvedUseCase,
    correlation_id: str,
    status: str,
    *,
    results: list[PatternRouteResult] | None = None,
    missing: set[str] | None = None,
) -> dict[str, object]:
    """Build the shared routing envelope while leaving cluster data opaque."""
    operation_id = resolved.use_case.operation_id
    return UseCaseRouteResult(
        status=status,
        use_case_id=resolved.use_case.use_case_id,
        use_case_version=resolved.use_case.use_case_version,
        pattern_id=resolved.use_case.pattern_id,
        pattern_version=resolved.use_case.pattern_version,
        operation_id=operation_id,
        correlation_id=correlation_id,
        results=results
        or [
            PatternRouteResult(
                status="unavailable",
                tenant_id=tenant_id,
                pattern_id=resolved.use_case.pattern_id,
                pattern_version=resolved.use_case.pattern_version,
                operation_id=operation_id,
                source="registry",
                error="Tenant endpoint is not configured for this deployment.",
            )
            for tenant_id in sorted(missing or set())
        ],
    ).model_dump(mode="json", exclude_none=True)
