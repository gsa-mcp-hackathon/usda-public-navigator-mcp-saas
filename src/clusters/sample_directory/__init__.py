"""Generic directory example cluster at pattern version 1.0.0."""

from __future__ import annotations

from collections.abc import Mapping

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from clusters.eligibility_profile_projection import ProfileFixtureReader
from example_server.models import TenantProgram
from example_server.storage import CatalogRepository


class DirectoryEntry(BaseModel):
    """One generic directory entry supplied by a tenant configuration."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)


class DirectoryConfiguration(BaseModel):
    """Validated, non-secret configuration for the sample directory cluster."""

    model_config = ConfigDict(extra="forbid")

    entries: list[DirectoryEntry]


def register_tools(
    app: FastMCP,
    *,
    tenant_id: str,
    cluster_id: str,
    pattern_version: str,
    programs: list[TenantProgram],
    catalog_repository: CatalogRepository | None,
    cluster_config: Mapping[str, object],
    profile_fixture_reader: ProfileFixtureReader | None = None,
) -> None:
    """Register a generic list operation backed by tenant configuration."""
    del programs, catalog_repository, profile_fixture_reader
    config = DirectoryConfiguration.model_validate(cluster_config)
    entries = config.entries

    @app.tool(name="list_entries")
    async def list_entries() -> dict[str, object]:
        """List this tenant's configured directory entries."""
        return {
            "tenant_id": tenant_id,
            "cluster_id": cluster_id,
            "pattern_version": pattern_version,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        }
