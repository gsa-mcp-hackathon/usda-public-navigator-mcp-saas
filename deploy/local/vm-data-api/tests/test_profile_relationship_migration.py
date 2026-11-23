from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb


class _RollbackMigrationProbe(Exception):
    """Roll back the disposable migration probe after its assertions."""


def test_profile_relationship_migration_adds_namespaced_context(
    database_url: str,
) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    migration = (
        repository_root
        / "db"
        / "migrations"
        / "005b_namespaced_profile_relationships.sql"
    ).read_text(encoding="utf-8")

    with psycopg.connect(database_url) as connection:
        with pytest.raises(_RollbackMigrationProbe):
            with connection.transaction():
                connection.execute(
                    "DROP TABLE IF EXISTS mcp_saas.profile_relationships CASCADE"
                )
                connection.execute(migration)
                connection.execute(
                    """INSERT INTO mcp_saas.synthetic_profiles
                       (profile_id, profile_kind, schema_version, provenance,
                        created_at)
                       VALUES (%s, %s, %s, %s, now()),
                              (%s, %s, %s, %s, now())""",
                    (
                        "synthetic-relationship-probe-person",
                        "person",
                        "core-v1",
                        Jsonb({"record_type": "synthetic"}),
                        "synthetic-relationship-probe-household",
                        "household",
                        "core-v1",
                        Jsonb({"record_type": "synthetic"}),
                    ),
                )
                connection.execute(
                    """INSERT INTO mcp_saas.profile_relationships
                       (subject_profile_id, relationship_id, object_profile_id,
                        context, provenance)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (
                        "synthetic-relationship-probe-person",
                        "core.household.member_of",
                        "synthetic-relationship-probe-household",
                        Jsonb({"namespace": "demo.household"}),
                        Jsonb({"record_type": "synthetic"}),
                    ),
                )
                context = connection.execute(
                    """SELECT context FROM mcp_saas.profile_relationships
                       WHERE relationship_id = %s""",
                    ("core.household.member_of",),
                ).fetchone()[0]
                assert context == {"namespace": "demo.household"}
                raise _RollbackMigrationProbe
