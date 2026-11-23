from __future__ import annotations

import psycopg
import pytest
from psycopg.types.json import Jsonb


class _RollbackMigrationProbe(Exception):
    """Roll back catalog rows created by this migration probe."""


def test_dataset_record_limit_is_bounded_and_keeps_the_default(
    database_url: str,
) -> None:
    with psycopg.connect(database_url) as connection:
        sam_limit = connection.execute(
            """SELECT (metadata->>'maximum_records_total')::integer
               FROM mcp_saas.public_datasets
               WHERE dataset_id = 'sam-assistance-listings-current'"""
        ).fetchone()[0]
        assert sam_limit == 3000

        with pytest.raises(_RollbackMigrationProbe):
            with connection.transaction():
                connection.execute(
                    """INSERT INTO mcp_saas.public_data_sources
                       (source_id, source_name, source_url, access_status)
                       VALUES ('record-cap-probe', 'Record cap probe',
                               'https://example.gov/catalog', 'available')"""
                )
                for dataset_id, metadata in (
                    ("record-cap-explicit", {"maximum_records_total": 2}),
                    ("record-cap-default", {}),
                    ("record-cap-invalid", {"maximum_records_total": 5001}),
                ):
                    connection.execute(
                        """INSERT INTO mcp_saas.public_datasets
                           (dataset_id, source_id, source_dataset_id, title,
                            schema_version, metadata)
                           VALUES (%s, 'record-cap-probe', %s, %s, 'probe-v1', %s)""",
                        (
                            dataset_id,
                            dataset_id,
                            dataset_id,
                            Jsonb(metadata),
                        ),
                    )

                def insert_records(dataset_id: str, count: int, start: int = 0) -> None:
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            """INSERT INTO mcp_saas.public_dataset_records
                               (dataset_id, source_record_id, record_type, content_hash,
                                schema_version, observed_at)
                               VALUES (%s, %s, 'probe', %s, 'probe-v1', now())""",
                            [
                                (dataset_id, f"record-{index}", f"hash-{index}")
                                for index in range(start, start + count)
                            ],
                        )

                insert_records("record-cap-explicit", 2)
                with pytest.raises(psycopg.errors.RaiseException):
                    with connection.transaction():
                        insert_records("record-cap-explicit", 1, start=2)

                # Replacing an existing source identity remains allowed at cap.
                connection.execute(
                    """INSERT INTO mcp_saas.public_dataset_records
                       (dataset_id, source_record_id, record_type, content_hash,
                        schema_version, observed_at)
                       VALUES ('record-cap-explicit', 'record-0', 'probe',
                               'updated-hash', 'probe-v1', now())
                       ON CONFLICT (dataset_id, source_record_id)
                       DO UPDATE SET content_hash = EXCLUDED.content_hash"""
                )

                with pytest.raises(psycopg.errors.RaiseException):
                    with connection.transaction():
                        insert_records("record-cap-invalid", 1)

                insert_records("record-cap-default", 1000)
                with pytest.raises(psycopg.errors.RaiseException):
                    with connection.transaction():
                        insert_records("record-cap-default", 1, start=1000)

                raise _RollbackMigrationProbe
