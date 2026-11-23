"""Provider-neutral repository boundary backed by PostgreSQL for the demo."""

from __future__ import annotations

from typing import Protocol

import psycopg
from psycopg.rows import dict_row
from pydantic import SecretStr

from example_server.models import SyntheticProfile, TenantProgram


class CatalogRepository(Protocol):
    """Minimal read contract used by a separately deployed tenant server."""

    def list_programs(self, tenant_id: str, cluster_id: str) -> list[TenantProgram]:
        """Return that tenant's catalog records in deterministic ID order."""


class StorageUnavailable(RuntimeError):
    """Raised when the configured persistence service cannot answer a query."""


class PostgresRepository:
    """Read catalog and synthetic profile records from the runtime data store."""

    def __init__(self, database_url: SecretStr, *, connect_timeout: int = 3) -> None:
        self._database_url = database_url
        self._connect_timeout = connect_timeout

    def list_programs(self, tenant_id: str, cluster_id: str) -> list[TenantProgram]:
        """Read one tenant and cluster slice without loading unrelated records."""
        query = """
            SELECT program_id, title, source_id, jurisdiction, catalog_version,
                   schema_version,
                   source_url, attributes, provenance
            FROM mcp_saas.program_catalog
            WHERE tenant_id = %s AND cluster_id = %s
            ORDER BY program_id
        """
        try:
            with psycopg.connect(
                self._database_url.get_secret_value(),
                connect_timeout=self._connect_timeout,
                row_factory=dict_row,
            ) as connection:
                rows = connection.execute(query, (tenant_id, cluster_id)).fetchall()
        except psycopg.Error as exc:
            raise StorageUnavailable("Catalog store query failed.") from exc
        return [TenantProgram.model_validate(row) for row in rows]

    def get_synthetic_profile(self, profile_id: str) -> SyntheticProfile | None:
        """Read one synthetic profile with its reusable dimensions and links."""
        query = """
            SELECT profile.profile_id, profile.profile_kind, profile.schema_version,
                   profile.provenance,
                   COALESCE((
                       SELECT jsonb_object_agg(attribute.attribute_id, attribute.value)
                       FROM mcp_saas.profile_attribute_values AS attribute
                       WHERE attribute.profile_id = profile.profile_id
                   ), '{}'::jsonb) AS attributes,
                   COALESCE((
                       SELECT jsonb_agg(jsonb_build_object(
                           'relationship_id', link.relationship_id,
                           'profile_id', link.object_profile_id
                       ) ORDER BY link.relationship_id, link.object_profile_id)
                       FROM mcp_saas.profile_relationships AS link
                       WHERE link.subject_profile_id = profile.profile_id
                   ), '[]'::jsonb) AS relationships
            FROM mcp_saas.synthetic_profiles AS profile
            WHERE profile.profile_id = %s
        """
        try:
            with psycopg.connect(
                self._database_url.get_secret_value(),
                connect_timeout=self._connect_timeout,
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(query, (profile_id,)).fetchone()
        except psycopg.Error as exc:
            raise StorageUnavailable("Profile store query failed.") from exc
        return SyntheticProfile.model_validate(row) if row is not None else None
