"""Validation for immutable, organization-neutral pattern template packages."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

TEMPLATE_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
OPERATION_ID = re.compile(r"[a-z][a-z0-9.-]{0,127}\Z")
ARTIFACT_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
CORE_ROUTE_CONTRACT = "core-route.v1"
PROFILE_MAPPING_ARTIFACT = re.compile(r"[a-z][a-z0-9-]{0,127}\.json\Z")
PROFILE_MAPPING_VERSION = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


class InvalidTemplatePackage(ValueError):
    """The package descriptor is missing fields or contains invalid values."""


class IncompatibleTemplatePackage(ValueError):
    """The package does not support the Core route contract used by this service."""


def _valid_input_schema_catalog(value: object) -> bool:
    """Accept a bounded schema catalog with local-only definition references."""
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("$defs"), dict)
        or len(str(value).encode("utf-8")) > 65536
    ):
        return False
    definitions = value["$defs"]
    for definition in definitions.values():
        if isinstance(definition, dict) and definition.get("type") == "object":
            if not isinstance(definition.get("properties"), dict):
                return False
            required = definition.get("required", [])
            if not isinstance(required, list) or any(
                not isinstance(item, str) for item in required
            ):
                return False

    def has_only_local_refs(item: object) -> bool:
        if isinstance(item, dict):
            if "$dynamicRef" in item or "$recursiveRef" in item:
                return False
            reference = item.get("$ref")
            if reference is not None:
                if not isinstance(reference, str) or not reference.startswith(
                    "#/$defs/"
                ):
                    return False
                definition_name = reference.removeprefix("#/$defs/")
                if "/" in definition_name or definition_name not in definitions:
                    return False
            return all(has_only_local_refs(child) for child in item.values())
        if isinstance(item, list):
            return all(has_only_local_refs(child) for child in item)
        return True

    return has_only_local_refs(value)


def _string_list(value: object, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        raise InvalidTemplatePackage("Package references must be a non-empty list.")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise InvalidTemplatePackage("Package references must be non-empty strings.")
    if len(value) != len(set(value)):
        raise InvalidTemplatePackage("Package references must be unique.")
    return value


def validate_template_package(payload: object) -> dict[str, object]:
    """Validate and normalize an immutable template descriptor without domain rules."""
    required = {
        "template_id",
        "template_version",
        "artifact_ref",
        "artifact_sha256",
        "compatible_core_contracts",
        "operations",
        "input_schema_catalog",
        "schema_refs",
        "adapter_refs",
        "rule_refs",
        "catalog_namespaces",
        "result_contract_refs",
    }
    optional = {"profile_projection_mapping"}
    if (
        not isinstance(payload, dict)
        or not required <= set(payload)
        or set(payload) - required - optional
    ):
        raise InvalidTemplatePackage("Template package has an invalid structure.")

    template_id = payload["template_id"]
    template_version = payload["template_version"]
    artifact_ref = payload["artifact_ref"]
    artifact_sha256 = payload["artifact_sha256"]
    if not isinstance(template_id, str) or not TEMPLATE_ID.fullmatch(template_id):
        raise InvalidTemplatePackage("Template identity is invalid.")
    if not isinstance(template_version, str) or not SEMVER.fullmatch(template_version):
        raise InvalidTemplatePackage("Template version must use semantic versioning.")
    if (
        not isinstance(artifact_ref, str)
        or not artifact_ref.strip()
        or len(artifact_ref) > 512
        or any(character.isspace() for character in artifact_ref)
    ):
        raise InvalidTemplatePackage("Artifact reference is invalid.")
    if not isinstance(artifact_sha256, str) or not ARTIFACT_DIGEST.fullmatch(
        artifact_sha256
    ):
        raise InvalidTemplatePackage("Artifact SHA-256 digest is invalid.")

    compatible = _string_list(payload["compatible_core_contracts"], required=True)
    if CORE_ROUTE_CONTRACT not in compatible:
        raise IncompatibleTemplatePackage("Template is incompatible with Core routing.")

    operations = payload["operations"]
    if not isinstance(operations, list) or not operations:
        raise InvalidTemplatePackage("Template operations must be a non-empty list.")
    normalized_operations: list[dict[str, object]] = []
    operation_ids: set[str] = set()
    tool_names: set[str] = set()
    for operation in operations:
        fields = {
            "operation_id",
            "mcp_tool_name",
            "request_schema_ref",
            "result_contract_ref",
            "summary",
            "user_goal_examples",
            "input_schema_ref",
        }
        if not isinstance(operation, dict) or set(operation) != fields:
            raise InvalidTemplatePackage("Template operation has an invalid structure.")
        operation_id = operation["operation_id"]
        tool_name = operation["mcp_tool_name"]
        request_schema_ref = operation["request_schema_ref"]
        result_contract_ref = operation["result_contract_ref"]
        summary = operation["summary"]
        user_goal_examples = operation["user_goal_examples"]
        input_schema_ref = operation["input_schema_ref"]
        if not isinstance(operation_id, str) or not OPERATION_ID.fullmatch(
            operation_id
        ):
            raise InvalidTemplatePackage("Template operation identity is invalid.")
        if operation_id in operation_ids:
            raise InvalidTemplatePackage("Template operation IDs must be unique.")
        if not isinstance(tool_name, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]{0,63}\Z", tool_name
        ):
            raise InvalidTemplatePackage("Template MCP tool name is invalid.")
        if tool_name in tool_names:
            raise InvalidTemplatePackage("Template MCP tool names must be unique.")
        if not isinstance(request_schema_ref, str) or not request_schema_ref.strip():
            raise InvalidTemplatePackage(
                "Template request schema reference is invalid."
            )
        if not isinstance(result_contract_ref, str) or not result_contract_ref.strip():
            raise InvalidTemplatePackage(
                "Template result contract reference is invalid."
            )
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 500:
            raise InvalidTemplatePackage("Template operation summary is invalid.")
        if (
            not isinstance(user_goal_examples, list)
            or not user_goal_examples
            or len(user_goal_examples) > 8
            or any(
                not isinstance(item, str) or not item.strip() or len(item) > 240
                for item in user_goal_examples
            )
        ):
            raise InvalidTemplatePackage("Template user goal examples are invalid.")
        if not isinstance(input_schema_ref, str) or not re.fullmatch(
            r"#/\$defs/[A-Za-z][A-Za-z0-9_-]{0,127}", input_schema_ref
        ):
            raise InvalidTemplatePackage(
                "Template operation input schema reference is invalid."
            )
        operation_ids.add(operation_id)
        tool_names.add(tool_name)
        normalized_operations.append(
            {
                "operation_id": operation_id,
                "mcp_tool_name": tool_name,
                "request_schema_ref": request_schema_ref,
                "result_contract_ref": result_contract_ref,
                "summary": summary,
                "user_goal_examples": user_goal_examples,
                "input_schema_ref": input_schema_ref,
            }
        )

    normalized: dict[str, object] = {
        "template_id": template_id,
        "template_version": template_version,
        "artifact_ref": artifact_ref,
        "artifact_sha256": artifact_sha256,
        "compatible_core_contracts": compatible,
        "operations": normalized_operations,
        "input_schema_catalog": payload["input_schema_catalog"],
        "schema_refs": _string_list(payload["schema_refs"], required=True),
        "adapter_refs": _string_list(payload["adapter_refs"]),
        "rule_refs": _string_list(payload["rule_refs"]),
        "catalog_namespaces": _string_list(payload["catalog_namespaces"]),
        "result_contract_refs": _string_list(
            payload["result_contract_refs"], required=True
        ),
    }
    if "profile_projection_mapping" in payload:
        declaration = payload["profile_projection_mapping"]
        if (
            not isinstance(declaration, dict)
            or set(declaration) != {"artifact_ref", "mapping_version"}
            or not isinstance(declaration.get("artifact_ref"), str)
            or not PROFILE_MAPPING_ARTIFACT.fullmatch(declaration["artifact_ref"])
            or not isinstance(declaration.get("mapping_version"), str)
            or not PROFILE_MAPPING_VERSION.fullmatch(declaration["mapping_version"])
        ):
            raise InvalidTemplatePackage(
                "Template profile projection mapping declaration is invalid."
            )
        normalized["profile_projection_mapping"] = declaration.copy()
    schema_catalog = normalized["input_schema_catalog"]
    if not _valid_input_schema_catalog(schema_catalog):
        raise InvalidTemplatePackage("Template input schema catalog is invalid.")
    schema_definitions = schema_catalog["$defs"]
    for operation in normalized_operations:
        input_schema = schema_definitions.get(
            operation["input_schema_ref"].removeprefix("#/$defs/")
        )
        if (
            not isinstance(input_schema, dict)
            or input_schema.get("type") != "object"
            or not isinstance(input_schema.get("properties"), dict)
        ):
            raise InvalidTemplatePackage(
                "Template operation references an undeclared input schema."
            )
    if any(
        operation["request_schema_ref"] not in normalized["schema_refs"]
        for operation in normalized_operations
    ):
        raise InvalidTemplatePackage(
            "Operation request schema is not declared by the package."
        )
    if any(
        operation["result_contract_ref"] not in normalized["result_contract_refs"]
        for operation in normalized_operations
    ):
        raise InvalidTemplatePackage(
            "Operation result contract is not declared by the package."
        )
    return normalized


def valid_handler_endpoint(value: object) -> bool:
    """Accept only project-private HTTP MCP endpoints without embedded credentials."""
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    hostname = parsed.hostname or ""
    return (
        parsed.scheme == "http"
        and hostname.endswith(".svc.cluster.local")
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 80, 8080)
        and parsed.path == "/mcp"
        and not parsed.query
        and not parsed.fragment
    )
