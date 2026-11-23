"""MCP server entry point.

Keep this file THIN. It only:
  1. creates the FastMCP instance,
  2. registers tools / prompts / resources / routes from the submodules, and
  3. decides which transport to run (stdio locally vs. HTTP when deployed).

All real logic lives in the submodules:
  - tools/ one file per tool (each exposes `register(mcp)`)
  - prompts/ reusable conversation starters
  - resources/ static/semi-static data the client can read directly
  - routes.py HTTP-only infrastructure (health check, etc.)
  - config.py settings loaded from env vars /.env
  - models.py Pydantic models & enums for tool parameters
  - utils.py shared helpers (HTTP client, formatting, pagination)
"""

from __future__ import annotations

import logging
import os
import sys

from clusters.eligibility_persistence import HttpCriteriaRulesetRepository
from clusters.eligibility_profile_projection import (
    HttpProfileFixtureReader,
    load_declared_profile_projection_mapping,
    resolve_templates_directory,
)
from clusters.eligibility_versions import (
    CRITERIA_PERSISTENCE_VERSION,
    supports_eligibility_feature,
)
from example_server import __version__
from example_server.config import settings
from example_server.persistence_api import PersistenceApiRegistryRepository
from example_server.persistence_api_catalog import PersistenceApiCatalogRepository
from example_server.platform import create_server
from example_server.registry import PostgresRegistryRepository
from example_server.storage import PostgresRepository
from example_server.telemetry import configure_event_stream

catalog_repository = None
profile_fixture_reader = None
registry_repository = None
public_search_provider = settings.resolved_public_search_provider()
criteria_ruleset_repository = None
cluster_config = settings.cluster_config
if settings.mcp_role == "tenant":
    templates_directory = resolve_templates_directory()
    projection_mapping = load_declared_profile_projection_mapping(
        settings.cluster_id, settings.pattern_version, templates_directory
    )
    if projection_mapping is not None:
        cluster_config["profile_projection_mapping"] = projection_mapping
if settings.mcp_role == "tenant" and settings.catalog_backend == "postgres":
    catalog_repository = PostgresRepository(settings.resolved_database_url())
elif (
    settings.mcp_role in {"tenant", "cluster"}
    and settings.catalog_backend == "persistence_api"
):
    api_url, token_file = settings.resolved_persistence_api_catalog()
    catalog_repository = PersistenceApiCatalogRepository(
        api_url,
        token_file,
        tenant_id=settings.tenant_id if settings.mcp_role == "tenant" else "",
        cluster_id=settings.cluster_id,
        pattern_version=settings.pattern_version,
        requirements_package_token_file=(
            settings.persistence_api_requirements_package_token_file
        ),
    )
    if settings.mcp_role == "tenant":
        # Use a dedicated cluster-bound ruleset reader when configured; the
        # existing catalog reader is retained for the current eligibility scope.
        criteria_ruleset_repository = HttpCriteriaRulesetRepository(
            api_url,
            settings.persistence_api_criteria_ruleset_read_token_file or token_file,
            cluster_id=settings.cluster_id,
        )
    if (
        settings.mcp_role == "tenant"
        and settings.persistence_api_profile_fixture_token_file is not None
    ):
        profile_fixture_reader = HttpProfileFixtureReader(
            api_url, settings.persistence_api_profile_fixture_token_file
        )
    if settings.mcp_role == "cluster" and supports_eligibility_feature(
        settings.pattern_version, CRITERIA_PERSISTENCE_VERSION
    ):
        writer_token_file = settings.persistence_api_criteria_ruleset_token_file
        if writer_token_file is None:
            raise ValueError(
                "Cluster runtime requires its criteria-ruleset write capability."
            )
        criteria_ruleset_repository = HttpCriteriaRulesetRepository(
            api_url,
            settings.persistence_api_criteria_ruleset_read_token_file or token_file,
            writer_token_file,
            cluster_id=settings.cluster_id,
        )
if settings.mcp_role == "core":
    if settings.registry_backend == "persistence_api":
        api_url, token_file = settings.resolved_persistence_api()
        registry_repository = PersistenceApiRegistryRepository(api_url, token_file)
    else:
        registry_repository = PostgresRegistryRepository(
            settings.resolved_database_url()
        )

# The server name is what MCP clients display. Rename it to your service.
mcp = create_server(
    role=settings.mcp_role,
    tenant_id=settings.tenant_id,
    cluster_id=settings.cluster_id,
    pattern_version=settings.pattern_version,
    cluster_config=cluster_config,
    programs=settings.tenant_programs,
    cluster_mcp_endpoints=settings.cluster_mcp_endpoints,
    catalog_repository=catalog_repository,
    criteria_ruleset_repository=criteria_ruleset_repository,
    profile_fixture_reader=profile_fixture_reader,
    public_search_provider=public_search_provider,
    registry_repository=registry_repository,
    version=__version__,
)


def _configure_http_timing_logs() -> None:
    """Expose privacy-safe Core and Eligibility timing stages on container stderr."""
    for logger_name in (
        "example_server.core_app",
        "clusters.eligibility_inventory",
    ):
        timing_logger = logging.getLogger(logger_name)
        timing_logger.setLevel(logging.INFO)
        for handler in timing_logger.handlers:
            handler.setLevel(logging.INFO)
        if not timing_logger.handlers:
            timing_logger.addHandler(logging.StreamHandler(sys.stderr))
        timing_logger.propagate = False


def main() -> None:
    """Start the server, choosing a transport based on the environment.

    Precedence:
      1. A platform-injected port (Databricks `DATABRICKS_APP_PORT` or a generic
         `PORT`) → run HTTP on that port. Cloud hosts set this for you.
      2. `MCP_TRANSPORT=streamable-http` in the environment → run HTTP on
         `MCP_HOST`:`MCP_PORT` (see config.py /.env).
      3. Otherwise → stdio, which is how local clients (Claude Desktop, Claude
         Code, Zed) launch the server as a subprocess.
    """
    platform_port = os.getenv("DATABRICKS_APP_PORT") or os.getenv("PORT")
    use_http = bool(platform_port) or settings.mcp_transport == "streamable-http"
    configure_event_stream(sys.stdout if use_http else sys.stderr)
    if use_http:
        _configure_http_timing_logs()

    if platform_port:
        mcp.run(transport="http", host="0.0.0.0", port=int(platform_port))
    elif settings.mcp_transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=settings.mcp_host,
            port=settings.mcp_port,
        )
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
