from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from template_packages import (
    IncompatibleTemplatePackage,
    InvalidTemplatePackage,
    valid_handler_endpoint,
    validate_template_package,
)


def package() -> dict[str, object]:
    return {
        "template_id": "sample-directory",
        "template_version": "1.0.0",
        "artifact_ref": "registry.example.test/directory:release-1",
        "artifact_sha256": "a" * 64,
        "compatible_core_contracts": ["core-route.v1"],
        "input_schema_catalog": {
            "$defs": {
                "DirectorySearchArguments": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                }
            }
        },
        "operations": [
            {
                "operation_id": "search.entries",
                "mcp_tool_name": "search_entries",
                "request_schema_ref": "directory.search.request.v1",
                "result_contract_ref": "directory.search.result.v1",
                "summary": "Find matching directory entries.",
                "user_goal_examples": ["Find a directory entry."],
                "input_schema_ref": "#/$defs/DirectorySearchArguments",
            }
        ],
        "schema_refs": ["directory.search.request.v1"],
        "adapter_refs": ["directory.adapter.v1"],
        "rule_refs": ["directory.rule.v1"],
        "catalog_namespaces": ["directory.entries"],
        "result_contract_refs": ["directory.search.result.v1"],
    }


def test_validates_an_organization_neutral_immutable_package() -> None:
    validated = validate_template_package(package())
    assert validated["template_id"] == "sample-directory"
    assert validated["artifact_sha256"] == "a" * 64
    assert "federal-agency" not in str(validated)


def test_template_declares_and_preserves_its_projection_mapping_version() -> None:
    value = package()
    value["profile_projection_mapping"] = {
        "artifact_ref": "directory-profile-map-v1.json",
        "mapping_version": "directory-profile-v1",
    }

    validated = validate_template_package(value)

    assert (
        validated["profile_projection_mapping"] == value["profile_projection_mapping"]
    )


@pytest.mark.parametrize(
    "declaration",
    [
        {"artifact_ref": "../outside.json", "mapping_version": "map-v1"},
        {"artifact_ref": "map.json"},
        {"artifact_ref": "map.json", "mapping_version": "map-v1", "extra": 1},
    ],
)
def test_rejects_invalid_projection_mapping_declaration(
    declaration: dict[str, object],
) -> None:
    value = package()
    value["profile_projection_mapping"] = declaration

    with pytest.raises(InvalidTemplatePackage, match="mapping declaration"):
        validate_template_package(value)


def test_rejects_missing_digest_and_undeclared_result_contract() -> None:
    mutable = package()
    mutable["artifact_sha256"] = "latest"
    with pytest.raises(InvalidTemplatePackage, match="SHA-256"):
        validate_template_package(mutable)

    unknown_result = package()
    unknown_result["result_contract_refs"] = []
    with pytest.raises(InvalidTemplatePackage, match="non-empty"):
        validate_template_package(unknown_result)


def test_rejects_incompatible_core_contract() -> None:
    incompatible = package()
    incompatible["compatible_core_contracts"] = ["core-route.v2"]
    with pytest.raises(IncompatibleTemplatePackage):
        validate_template_package(incompatible)


def test_rejects_unbounded_or_external_operation_schemas() -> None:
    invalid = package()
    invalid["input_schema_catalog"]["$defs"]["DirectorySearchArguments"]["properties"][
        "query"
    ] = {"$ref": "https://example.test/schema.json"}

    with pytest.raises(InvalidTemplatePackage, match="input schema"):
        validate_template_package(invalid)

    unresolved = package()
    unresolved["input_schema_catalog"]["$defs"]["DirectorySearchArguments"][
        "properties"
    ]["query"] = {"$ref": "#/$defs/Missing"}
    with pytest.raises(InvalidTemplatePackage, match="input schema"):
        validate_template_package(unresolved)


def test_handler_endpoint_must_be_project_private_mcp_service() -> None:
    assert valid_handler_endpoint("http://template.svc.cluster.local/mcp")
    assert not valid_handler_endpoint("https://public.example.test/mcp")
    assert not valid_handler_endpoint(
        "http://user:secret@template.svc.cluster.local/mcp"
    )
    assert not valid_handler_endpoint("http://template.svc.cluster.local/admin")
