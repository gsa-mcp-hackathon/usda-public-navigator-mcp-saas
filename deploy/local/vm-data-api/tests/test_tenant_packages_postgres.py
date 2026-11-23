from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

API_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIRECTORY))
API_SPEC = importlib.util.spec_from_file_location(
    "tenant_onboarding_api", API_DIRECTORY / "api.py"
)
assert API_SPEC is not None and API_SPEC.loader is not None
api = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(api)


def test_tenant_onboarding_is_namespaced_immutable_and_routable(
    database_url: str,
) -> None:
    suffix = uuid.uuid4().hex[:16]
    template_id = f"aitest-probe-{suffix}"
    tenant_id = f"aitest-probe-{suffix}"
    source_id = f"aitest.probe-{suffix}"
    dataset_id = f"aitest-probe-dataset-{suffix}"
    version = "1.0.0"
    definition = {
        "template_id": template_id,
        "template_version": version,
        "artifact_ref": "test.invalid/template:release-1",
        "artifact_sha256": "a" * 64,
        "compatible_core_contracts": ["core-route.v1"],
        "input_schema_catalog": {
            "$defs": {
                "SearchArguments": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                }
            },
        },
        "operations": [
            {
                "operation_id": "search.entries",
                "mcp_tool_name": "search_entries",
                "request_schema_ref": "aitest.search.request.v1",
                "result_contract_ref": "aitest.search.result.v1",
                "summary": "Search the tenant's entries.",
                "user_goal_examples": ["Find matching entries."],
                "input_schema_ref": "#/$defs/SearchArguments",
            }
        ],
        "schema_refs": ["aitest.search.request.v1"],
        "adapter_refs": [],
        "rule_refs": [],
        "catalog_namespaces": ["aitest.entries", "aitest.household-income"],
        "result_contract_refs": ["aitest.search.result.v1"],
    }
    specification = {
        "tenant_id": tenant_id,
        "template_id": template_id,
        "template_version": version,
        "endpoint": "http://tenant.svc.cluster.local/mcp",
        "jurisdictions": [{"level": "state", "code": "MO"}],
        "catalog_bindings": [
            {
                "dataset_id": dataset_id,
                "binding_context": {
                    "catalog_namespace": namespace,
                    "catalog_version": "1.0.0",
                    "source_id": source_id,
                    "semantic_mapping": {"income": meaning},
                    "tags": ["AITest"],
                    "test_run_id": suffix,
                },
            }
            for namespace, meaning in (
                ("aitest.entries", "directory-record"),
                ("aitest.household-income", "household-income"),
            )
        ],
        "operation_ids": ["search.entries"],
    }

    api.connect_db = lambda: psycopg.connect(database_url)
    try:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """INSERT INTO mcp_saas.public_data_sources
                   (source_id, source_name, source_url, access_status, source_context)
                   VALUES (%s, %s, %s, 'available', %s::jsonb)""",
                (
                    source_id,
                    "AITest synthetic integration source",
                    "https://example.test/source",
                    '{"tags":["AITest"],"test_run_id":"' + suffix + '"}',
                ),
            )
            connection.execute(
                """INSERT INTO mcp_saas.public_datasets
                   (dataset_id, source_id, source_dataset_id, title, schema_version,
                      metadata)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb)""",
                (
                    dataset_id,
                    source_id,
                    dataset_id,
                    "AITest integration dataset",
                    "v1",
                    '{"tags":["AITest"],"test_run_id":"' + suffix + '"}',
                ),
            )

        api.register_template_package(definition)
        api.set_template_package_state(template_id, version, "active")

        validation = api.validate_tenant_package_in_registry(specification)
        assert validation["status"] == "valid"
        digest = validation["tenant_spec_sha256"]

        registered = api.register_tenant_package(specification)
        replayed = api.register_tenant_package(specification)
        assert registered["tenant_spec_sha256"] == digest
        assert replayed["tenant_spec_sha256"] == digest
        assert registered["tenant"]["lifecycle_state"] == "pending"
        assert "endpoint" not in registered["tenant"]

        persisted = api.read_tenant_package(tenant_id, template_id, version)
        assert persisted is not None
        assert persisted["tenant_spec_sha256"] == digest
        assert len(persisted["catalog_bindings"]) == 2
        assert {
            item["catalog_namespace"] for item in persisted["catalog_bindings"]
        } == {
            "aitest.entries",
            "aitest.household-income",
        }
        assert api.read_core_route(tenant_id, "search.entries") is None

        activated = api.activate_tenant_package(tenant_id, template_id, version, digest)
        assert activated is not None
        assert activated["lifecycle_state"] == "active"
        assert api.read_core_route(tenant_id, "search.entries") is not None

        with psycopg.connect(database_url) as connection:
            connection.execute(
                """UPDATE mcp_saas.core_route_metadata
                   SET lifecycle_state = 'inactive'
                   WHERE route_id = %s AND tenant_id = %s
                     AND route_version = %s AND operation_id = 'search.entries'""",
                (tenant_id, tenant_id, version),
            )
            connection.execute(
                """INSERT INTO mcp_saas.core_route_metadata
                   (route_id, route_version, operation_id, endpoint, mcp_tool_name,
                    protocol, lifecycle_state, template_id, tenant_id)
                   SELECT tenant_id || '.' || template_id, route_version,
                          operation_id, endpoint, mcp_tool_name, protocol, 'active',
                          template_id, tenant_id
                   FROM mcp_saas.core_route_metadata
                   WHERE route_id = %s AND tenant_id = %s
                     AND route_version = %s AND operation_id = 'search.entries'""",
                (tenant_id, tenant_id, version),
            )
            migration = (
                API_DIRECTORY.parents[2]
                / "db/migrations/030_stable_active_route_pointer.sql"
            ).read_text(encoding="utf-8")
            connection.execute(migration)
            route_states = connection.execute(
                """SELECT route_id, lifecycle_state
                   FROM mcp_saas.core_route_metadata
                   WHERE tenant_id = %s AND route_version = %s
                     AND operation_id = 'search.entries'
                   ORDER BY route_id""",
                (tenant_id, version),
            ).fetchall()
        assert dict(route_states) == {
            f"{tenant_id}.{template_id}": "inactive",
            tenant_id: "active",
        }
        assert api.read_core_route(tenant_id, "search.entries") is not None

        expanded_spec = {
            **specification,
            "jurisdictions": [
                {"level": "state", "code": "MO"},
                {"level": "federal", "code": "US"},
            ],
        }
        revision_validation = api.validate_tenant_package_in_registry(expanded_spec)
        revision_digest = revision_validation["tenant_spec_sha256"]
        assert revision_validation["status"] == "valid"
        assert revision_digest != digest
        revised = api.register_tenant_package(expanded_spec)
        assert revised["status"] == "revised"
        assert revised["tenant_spec_sha256"] == revision_digest
        assert revised["tenant"]["lifecycle_state"] == "pending"
        assert revised["tenant"]["jurisdictions"] == expanded_spec["jurisdictions"]
        assert (
            api.activate_tenant_package(tenant_id, template_id, version, digest) is None
        )
        assert api.read_core_route(tenant_id, "search.entries") is None
        with psycopg.connect(database_url) as connection:
            history = connection.execute(
                """SELECT previous_spec_sha256, revised_spec_sha256,
                          previous_context, revised_context
                   FROM mcp_saas.tenant_registry_jurisdiction_revisions
                   WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s""",
                (tenant_id, template_id, version),
            ).fetchone()
        assert history is not None
        assert history[0] == digest
        assert history[1] == revision_digest
        assert history[2] == {"jurisdictions": specification["jurisdictions"]}
        assert history[3] == {"jurisdictions": expanded_spec["jurisdictions"]}

        changed_bindings = {
            **expanded_spec,
            "catalog_bindings": [
                {
                    **expanded_spec["catalog_bindings"][0],
                    "binding_context": {
                        **expanded_spec["catalog_bindings"][0]["binding_context"],
                        "semantic_mapping": {"income": "different-meaning"},
                    },
                },
                expanded_spec["catalog_bindings"][1],
            ],
        }
        with pytest.raises(api.TemplatePackageConflict, match="jurisdiction-only"):
            api.validate_tenant_package_in_registry(changed_bindings)

        activated_revision = api.activate_tenant_package(
            tenant_id, template_id, version, revision_digest
        )
        assert activated_revision is not None
        assert activated_revision["lifecycle_state"] == "active"
        assert activated_revision["tenant_spec_sha256"] == revision_digest
        assert api.read_core_route(tenant_id, "search.entries") is not None
        digest = revision_digest

        assert (
            api.deactivate_tenant_package(tenant_id, template_id, version, "0" * 64)
            is None
        )
        deactivated = api.deactivate_tenant_package(
            tenant_id, template_id, version, digest
        )
        assert deactivated is not None
        assert deactivated["lifecycle_state"] == "inactive"
        assert api.read_core_route(tenant_id, "search.entries") is None
        reactivated = api.activate_tenant_package(
            tenant_id, template_id, version, digest
        )
        assert reactivated is not None
        assert reactivated["lifecycle_state"] == "active"
        assert api.read_core_route(tenant_id, "search.entries") is not None

        next_version = "1.0.1"
        next_definition = {**definition, "template_version": next_version}
        next_specification = {**specification, "template_version": next_version}
        api.register_template_package(next_definition)
        api.set_template_package_state(template_id, next_version, "active")
        next_validation = api.validate_tenant_package_in_registry(next_specification)
        next_digest = next_validation["tenant_spec_sha256"]
        api.register_tenant_package(next_specification)
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """INSERT INTO mcp_saas.core_route_metadata
                   (route_id, route_version, operation_id, endpoint, mcp_tool_name,
                    protocol, lifecycle_state, template_id, tenant_id)
                   VALUES (%s, %s, 'search.entries', %s, 'search_entries',
                           'mcp', 'active', %s, %s)""",
                (
                    f"{tenant_id}.{template_id}",
                    next_version,
                    specification["endpoint"],
                    template_id,
                    tenant_id,
                ),
            )

        upgraded = api.activate_tenant_package(
            tenant_id, template_id, next_version, next_digest
        )
        assert upgraded is not None
        assert upgraded["template_version"] == next_version
        assert upgraded["route_id"] == tenant_id
        resolved_route = api.read_core_route(tenant_id, "search.entries")
        assert resolved_route is not None
        assert resolved_route["route_version"] == next_version
        with psycopg.connect(database_url) as connection:
            active_route_ids = {
                row[0]
                for row in connection.execute(
                    """SELECT DISTINCT route_id FROM mcp_saas.core_route_metadata
                       WHERE tenant_id = %s AND lifecycle_state = 'active'""",
                    (tenant_id,),
                ).fetchall()
            }
            active_route_versions = {
                row[0]
                for row in connection.execute(
                    """SELECT DISTINCT route_version
                       FROM mcp_saas.core_route_metadata
                       WHERE tenant_id = %s AND lifecycle_state = 'active'""",
                    (tenant_id,),
                ).fetchall()
            }
        assert active_route_ids == {tenant_id}
        assert active_route_versions == {next_version}
        assert (
            api.read_core_route(f"{tenant_id}.{template_id}", "search.entries") is None
        )
        previous = api.read_tenant_package(tenant_id, template_id, version)
        assert previous is not None
        assert previous["lifecycle_state"] == "inactive"

        governed_tenant = api.read_tenant_package(tenant_id, template_id, next_version)
        assert governed_tenant is not None
        active_state_digest = api._tenant_lifecycle_state_sha256(
            tenant_id,
            template_id,
            next_version,
            governed_tenant["lifecycle_state"],
            governed_tenant["tenant_spec_sha256"],
        )
        audit_context = {
            "mcp_session_sha256": "1" * 64,
            "mcp_request_sha256": "2" * 64,
            "mcp_client_identity_status": "unavailable",
            "operator_credential_label": "core_operator_token",
        }
        transaction_id = str(uuid.uuid4())
        deactivated = api.set_tenant_package_state_governed(
            tenant_id,
            template_id,
            next_version,
            action="deactivate",
            approved_tenant_spec_sha256=governed_tenant["tenant_spec_sha256"],
            expected_before_sha256=active_state_digest,
            transaction_id=transaction_id,
            approval_reference="integration-test approval",
            operator_audit_context=audit_context,
        )
        assert deactivated is not None
        assert deactivated["status"] == "recorded"
        assert deactivated["event"]["outcome"] == "applied"
        assert deactivated["event"]["before_state_sha256"] == active_state_digest
        assert deactivated["event"]["operator_audit_context"] == audit_context
        assert deactivated["readback"]["lifecycle_state"] == "inactive"
        assert api.read_core_route(tenant_id, "search.entries") is None

        replayed = api.set_tenant_package_state_governed(
            tenant_id,
            template_id,
            next_version,
            action="deactivate",
            approved_tenant_spec_sha256=governed_tenant["tenant_spec_sha256"],
            expected_before_sha256=active_state_digest,
            transaction_id=transaction_id,
            approval_reference="integration-test approval",
            operator_audit_context=audit_context,
        )
        assert replayed is not None
        assert replayed["status"] == "idempotent_replay"
        assert replayed["event"] == deactivated["event"]

        with pytest.raises(api.TenantLifecycleConflict) as stale:
            api.set_tenant_package_state_governed(
                tenant_id,
                template_id,
                next_version,
                action="deactivate",
                approved_tenant_spec_sha256=governed_tenant["tenant_spec_sha256"],
                expected_before_sha256=active_state_digest,
                transaction_id=str(uuid.uuid4()),
                approval_reference="stale integration state",
                operator_audit_context=audit_context,
            )
        assert stale.value.reason == "stale_before_state"

        inactive_digest = deactivated["readback"]["lifecycle_state_sha256"]
        reactivated = api.set_tenant_package_state_governed(
            tenant_id,
            template_id,
            next_version,
            action="activate",
            approved_tenant_spec_sha256=governed_tenant["tenant_spec_sha256"],
            expected_before_sha256=inactive_digest,
            transaction_id=str(uuid.uuid4()),
            approval_reference="integration-test reactivation",
            operator_audit_context=audit_context,
        )
        assert reactivated is not None
        assert reactivated["status"] == "recorded"
        assert reactivated["readback"]["lifecycle_state"] == "active"
        assert api.read_core_route(tenant_id, "search.entries") is not None
        with psycopg.connect(database_url) as connection:
            receipt_count = connection.execute(
                """SELECT count(*) FROM mcp_saas.tenant_lifecycle_events
                   WHERE tenant_id = %s""",
                (tenant_id,),
            ).fetchone()[0]
        assert receipt_count == 2

        invalid_namespace = {
            **specification,
            "catalog_bindings": [
                {
                    **specification["catalog_bindings"][0],
                    "binding_context": {
                        **specification["catalog_bindings"][0]["binding_context"],
                        "catalog_namespace": "undeclared.namespace",
                    },
                }
            ],
        }
        with pytest.raises(api.InvalidTenantPackage, match="not declared"):
            api.validate_tenant_package_in_registry(invalid_namespace)
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                "DELETE FROM mcp_saas.tenant_lifecycle_events WHERE tenant_id = %s",
                (tenant_id,),
            )
            connection.execute(
                """DELETE FROM mcp_saas.tenant_registry_jurisdiction_revisions
                   WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s""",
                (tenant_id, template_id, version),
            )
            connection.execute(
                "DELETE FROM mcp_saas.core_route_metadata WHERE tenant_id = %s",
                (tenant_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.pattern_catalog_bindings WHERE tenant_id = %s",
                (tenant_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.tenant_registry WHERE tenant_id = %s",
                (tenant_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.pattern_template_packages WHERE template_id = %s",
                (template_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.public_datasets WHERE dataset_id = %s",
                (dataset_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.public_data_sources WHERE source_id = %s",
                (source_id,),
            )
