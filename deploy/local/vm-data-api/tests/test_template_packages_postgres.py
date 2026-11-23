from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

API_DIRECTORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(API_DIRECTORY))
API_SPEC = importlib.util.spec_from_file_location(
    "template_registry_api", API_DIRECTORY / "api.py"
)
assert API_SPEC is not None and API_SPEC.loader is not None
api = importlib.util.module_from_spec(API_SPEC)
API_SPEC.loader.exec_module(api)


def test_template_registration_restart_readback_replay_and_core_route(
    database_url: str,
) -> None:
    template_id = f"probe-{uuid.uuid4().hex[:16]}"
    definition = {
        "template_id": template_id,
        "template_version": "1.0.0",
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
                "operation_id": "read.items",
                "mcp_tool_name": "read_items",
                "request_schema_ref": "sample.read.request.v1",
                "result_contract_ref": "sample.read.result.v1",
                "summary": "Read matching items.",
                "user_goal_examples": ["Find matching items."],
                "input_schema_ref": "#/$defs/SearchArguments",
            }
        ],
        "schema_refs": ["sample.read.request.v1"],
        "adapter_refs": [],
        "rule_refs": [],
        "catalog_namespaces": ["sample.items"],
        "result_contract_refs": ["sample.read.result.v1"],
    }

    api.connect_db = lambda: psycopg.connect(database_url)
    try:
        package, created = api.register_template_package(definition)
        assert created
        assert package["lifecycle_state"] == "pending"

        definition_sha256 = hashlib.sha256(
            json.dumps(definition, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        before_sha256 = api._template_lifecycle_state_sha256(
            template_id, "1.0.0", "pending", definition_sha256
        )
        transaction_id = str(uuid.uuid4())
        audit_context = {
            "mcp_session_sha256": "a" * 64,
            "mcp_request_sha256": "b" * 64,
            "mcp_client_identity_status": "unavailable",
            "operator_credential_label": "core_operator_token",
        }
        activation = api.activate_template_package_governed(
            template_id,
            "1.0.0",
            expected_before_sha256=before_sha256,
            transaction_id=transaction_id,
            approval_reference="test-approval-activation",
            operator_audit_context=audit_context,
        )
        assert activation is not None
        assert activation["status"] == "recorded"
        assert activation["event"]["before_state"] == "pending"
        assert activation["event"]["after_state"] == "active"
        assert activation["event"]["operator_audit_context"] == audit_context
        assert activation["readback"]["lifecycle_state"] == "active"

        replay = api.activate_template_package_governed(
            template_id,
            "1.0.0",
            expected_before_sha256=before_sha256,
            transaction_id=transaction_id,
            approval_reference="test-approval-activation",
            operator_audit_context={**audit_context, "mcp_request_sha256": "c" * 64},
        )
        assert replay is not None
        assert replay["status"] == "idempotent_replay"
        assert replay["event"] == activation["event"]

        with pytest.raises(api.TemplateLifecycleConflict, match="receipt"):
            api.activate_template_package_governed(
                template_id,
                "1.0.0",
                expected_before_sha256=before_sha256,
                transaction_id=transaction_id,
                approval_reference="different-approval-reference",
                operator_audit_context=audit_context,
            )

        with pytest.raises(api.TemplateLifecycleConflict, match="stale"):
            api.activate_template_package_governed(
                template_id,
                "1.0.0",
                expected_before_sha256="0" * 64,
                transaction_id=str(uuid.uuid4()),
                approval_reference="stale-approval-reference",
                operator_audit_context=audit_context,
            )

        route, route_created = api.bind_template_operation(
            template_id,
            "1.0.0",
            "read.items",
            "http://handler.svc.cluster.local/mcp",
        )
        assert route_created
        assert route["route_id"] == template_id

        replayed, created = api.register_template_package(definition)
        assert not created
        assert replayed["lifecycle_state"] == "active"

        readback = api.read_template_package(template_id, "1.0.0")
        assert readback is not None
        assert readback["definition"] == definition
        resolved = api.read_core_route(template_id, "read.items")
        assert resolved is not None
        assert resolved["route_version"] == "1.0.0"

        next_version = "1.0.1"
        next_definition = {**definition, "template_version": next_version}
        api.register_template_package(next_definition)
        api.set_template_package_state(template_id, next_version, "active")
        api.bind_template_operation(
            template_id,
            next_version,
            "read.items",
            "http://handler-v2.svc.cluster.local/mcp",
        )
        resolved = api.read_core_route(template_id, "read.items")
        assert resolved is not None
        assert resolved["route_version"] == next_version
        with psycopg.connect(database_url) as connection:
            active_versions = connection.execute(
                """SELECT route_version FROM mcp_saas.core_route_metadata
                   WHERE route_id = %s AND operation_id = 'read.items'
                     AND lifecycle_state = 'active'""",
                (template_id,),
            ).fetchall()
        assert active_versions == [(next_version,)]

        active_template = api.read_template_package(template_id, next_version)
        assert active_template is not None
        descriptor_sha256 = hashlib.sha256(
            json.dumps(
                active_template["definition"], sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        expected_before_sha256 = api._template_lifecycle_state_sha256(
            template_id, next_version, "active", descriptor_sha256
        )
        deactivated = api.deactivate_template_package_governed(
            template_id,
            next_version,
            expected_before_sha256=expected_before_sha256,
            transaction_id=str(uuid.uuid4()),
            approval_reference="postgres-template-deactivation-test",
            operator_audit_context={
                "mcp_session_sha256": None,
                "mcp_request_sha256": None,
                "mcp_client_identity_status": "unavailable",
                "operator_credential_label": "core_operator_token",
            },
        )
        assert deactivated["status"] == "recorded"
        assert deactivated["event"]["action"] == "deactivate"
        assert api.read_core_route(template_id, "read.items") is None

        changed = {**definition, "artifact_sha256": "b" * 64}
        with pytest.raises(api.TemplatePackageConflict):
            api.register_template_package(changed)
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(
                """DELETE FROM mcp_saas.pattern_template_lifecycle_events
                   WHERE template_id = %s""",
                (template_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.core_route_metadata WHERE route_id = %s",
                (template_id,),
            )
            connection.execute(
                "DELETE FROM mcp_saas.pattern_template_packages WHERE template_id = %s",
                (template_id,),
            )
