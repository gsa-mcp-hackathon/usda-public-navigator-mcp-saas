from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tenant_packages import InvalidTenantPackage, validate_tenant_package


def specification() -> dict[str, object]:
    return {
        "tenant_id": "sample-tenant",
        "template_id": "sample-directory",
        "template_version": "1.0.0",
        "endpoint": "http://directory.svc.cluster.local/mcp",
        "jurisdictions": [{"level": "state", "code": "MO"}],
        "catalog_bindings": [
            {
                "dataset_id": "directory-data",
                "binding_context": {
                    "catalog_namespace": "directory.entries",
                    "catalog_version": "1.0.0",
                    "source_id": "directory.source",
                    "attribute_mappings": {"income": "household-income"},
                },
            }
        ],
        "operation_ids": ["search.entries"],
    }


def test_validates_generic_tenant_spec_and_namespace_semantics() -> None:
    normalized = validate_tenant_package(specification())

    assert normalized["tenant_id"] == "sample-tenant"
    binding = normalized["catalog_bindings"][0]
    assert binding["binding_context"]["catalog_namespace"] == "directory.entries"
    assert binding["binding_context"]["attribute_mappings"] == {
        "income": "household-income"
    }


@pytest.mark.parametrize(
    "change, expected",
    [
        (
            lambda spec: spec.update(endpoint="https://public.example.test/mcp"),
            "private MCP",
        ),
        (
            lambda spec: spec["catalog_bindings"][0]["binding_context"].update(
                catalog_namespace="not..valid"
            ),
            "context is invalid",
        ),
        (
            lambda spec: spec["catalog_bindings"].append(spec["catalog_bindings"][0]),
            "unique",
        ),
    ],
)
def test_rejects_invalid_endpoint_namespace_and_duplicate_binding(
    change, expected: str
) -> None:
    payload = specification()
    change(payload)

    with pytest.raises(InvalidTenantPackage, match=expected):
        validate_tenant_package(payload)


def test_rejects_non_json_context_values() -> None:
    payload = specification()
    payload["catalog_bindings"][0]["binding_context"]["non_finite"] = float("nan")

    with pytest.raises(InvalidTenantPackage, match="JSON data"):
        validate_tenant_package(payload)
