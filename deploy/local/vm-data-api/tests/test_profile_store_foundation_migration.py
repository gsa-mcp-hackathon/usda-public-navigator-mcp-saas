from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb


class _RollbackMigrationProbe(Exception):
    """Roll back the disposable migration probe after its assertions."""


def test_profile_foundation_creates_synthetic_tables_without_migration_001(
    database_url: str,
) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    migration = (
        repository_root
        / "db"
        / "migrations"
        / "005a_synthetic_profile_store_foundation.sql"
    ).read_text(encoding="utf-8")

    with psycopg.connect(database_url) as connection:
        with pytest.raises(_RollbackMigrationProbe):
            with connection.transaction():
                connection.execute(
                    "DROP TABLE IF EXISTS mcp_saas.profile_attribute_history CASCADE"
                )
                connection.execute(
                    "DROP TABLE IF EXISTS mcp_saas.profile_relationships CASCADE"
                )
                connection.execute(
                    "DROP TABLE IF EXISTS mcp_saas.profile_attribute_values CASCADE"
                )
                connection.execute(
                    "DROP TABLE IF EXISTS mcp_saas.synthetic_profiles CASCADE"
                )
                connection.execute(migration)

                profile_table = connection.execute(
                    "SELECT to_regclass('mcp_saas.synthetic_profiles')"
                ).fetchone()[0]
                attribute_table = connection.execute(
                    "SELECT to_regclass('mcp_saas.profile_attribute_values')"
                ).fetchone()[0]
                assert profile_table is not None
                assert attribute_table is not None

                connection.execute(
                    """INSERT INTO mcp_saas.synthetic_profiles
                       (profile_id, profile_kind, schema_version, provenance)
                       VALUES (%s, %s, %s, %s)""",
                    (
                        "synthetic-migration-probe",
                        "household",
                        "core-v1",
                        Jsonb({"record_type": "synthetic"}),
                    ),
                )
                connection.execute(
                    """INSERT INTO mcp_saas.profile_attribute_values
                       (profile_id, attribute_id, dimension_id, value_type, value,
                        source_id, schema_version, provenance, observed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())""",
                    (
                        "synthetic-migration-probe",
                        "core.household.size",
                        "household",
                        "integer",
                        Jsonb(2),
                        "profile.confirmed",
                        "core-v1",
                        Jsonb({"record_type": "synthetic"}),
                    ),
                )
                raise _RollbackMigrationProbe


def test_profile_dimensions_accept_global_namespaced_ids(database_url: str) -> None:
    profile_id = "synthetic-namespaced-dimension-probe"

    with psycopg.connect(database_url) as connection:
        with pytest.raises(_RollbackMigrationProbe):
            with connection.transaction():
                connection.execute(
                    """INSERT INTO mcp_saas.synthetic_profiles
                       (profile_id, profile_kind, schema_version, schema_registry_id,
                        provenance, created_at)
                       VALUES (%s, %s, %s, %s, %s, now())""",
                    (
                        profile_id,
                        "farm_business",
                        "core-v1",
                        "core-schema-registry",
                        Jsonb({"record_type": "synthetic"}),
                    ),
                )
                connection.execute(
                    """INSERT INTO mcp_saas.profile_attribute_values
                       (profile_id, attribute_id, dimension_id, value_type, value,
                        source_id, schema_version, provenance, observed_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())""",
                    (
                        profile_id,
                        "core.farm_business.years_farming",
                        "core.farm_business",
                        "integer",
                        Jsonb(3),
                        "profile.user_confirmed",
                        "core-v1",
                        Jsonb({"record_type": "synthetic"}),
                    ),
                )
                stored_dimension = connection.execute(
                    """SELECT dimension_id FROM mcp_saas.profile_attribute_values
                       WHERE profile_id = %s""",
                    (profile_id,),
                ).fetchone()[0]
                assert stored_dimension == "core.farm_business"
                raise _RollbackMigrationProbe
