from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb


def test_requirements_package_table_accepts_v1_and_profile_report_v2(
    database_url: str,
) -> None:
    package_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    packages = [
        {
            "contract_version": "eligibility-requirements-package-v1",
            "package_id": package_ids[0],
            "tenant_id": "migration-probe-tenant",
            "official_eligibility_determination": False,
            "official_application_submitted": False,
        },
        {
            "contract_version": "eligibility-requirements-package-v2",
            "package_id": package_ids[1],
            "tenant_id": "migration-probe-tenant",
            "program_title": "Synthetic migration probe",
            "profile_snapshot": {"state_code": "MO"},
            "evaluation": {"evaluation_performed": True},
            "official_eligibility_determination": False,
            "official_application_submitted": False,
        },
    ]

    try:
        with psycopg.connect(database_url) as connection:
            for package_id, package in zip(package_ids, packages, strict=True):
                connection.execute(
                    """INSERT INTO mcp_saas.eligibility_requirements_packages
                       (package_id, tenant_id, definition, content_sha256)
                       VALUES (%s, %s, %s, %s)""",
                    (
                        package_id,
                        package["tenant_id"],
                        Jsonb(package),
                        "0" * 64,
                    ),
                )
            stored = connection.execute(
                """SELECT definition->>'contract_version',
                          definition->'profile_snapshot'
                   FROM mcp_saas.eligibility_requirements_packages
                   WHERE package_id = %s""",
                (package_ids[1],),
            ).fetchone()
        assert stored == ("eligibility-requirements-package-v2", {"state_code": "MO"})
        invalid_package = {
            **packages[1],
            "package_id": str(uuid.uuid4()),
            "program_title": "",
        }
        with psycopg.connect(database_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation):
                with connection.transaction():
                    connection.execute(
                        """INSERT INTO mcp_saas.eligibility_requirements_packages
                           (package_id, tenant_id, definition, content_sha256)
                           VALUES (%s, %s, %s, %s)""",
                        (
                            invalid_package["package_id"],
                            invalid_package["tenant_id"],
                            Jsonb(invalid_package),
                            "0" * 64,
                        ),
                    )
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """DELETE FROM mcp_saas.eligibility_requirements_packages
                   WHERE package_id = ANY(%s::uuid[])""",
                (package_ids,),
            )
