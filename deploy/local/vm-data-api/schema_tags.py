"""Validation helpers for the global, versioned Schema Registry tag model."""

from __future__ import annotations

import re

REGISTRY_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
SCHEMA_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
TAG_ID = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\Z")
ENTITY_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:@|/-]{0,191}\Z")
CONTEXT_ID = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\Z")
ENTITY_TYPES = frozenset(
    {"profile", "public_dataset", "pattern_template", "tenant", "source", "route"}
)


def _matches_type(value: object, value_type: object) -> bool:
    validators = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: (
            isinstance(item, (int, float)) and not isinstance(item, bool)
        ),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
    }
    validator = validators.get(value_type)
    return validator is not None and validator(value)


def validate_assignment(
    payload: object, schema_definition: dict[str, object] | None
) -> dict[str, object] | None:
    """Validate one immutable, contextual tag assignment against its active schema."""
    required = {
        "registry_id",
        "schema_version",
        "tag_id",
        "entity_type",
        "entity_id",
        "context_id",
        "value",
        "context",
        "provenance",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        return None
    if (
        not isinstance(payload["registry_id"], str)
        or REGISTRY_ID.fullmatch(payload["registry_id"]) is None
        or not isinstance(payload["schema_version"], str)
        or SCHEMA_VERSION.fullmatch(payload["schema_version"]) is None
        or not isinstance(payload["tag_id"], str)
        or TAG_ID.fullmatch(payload["tag_id"]) is None
        or payload["entity_type"] not in ENTITY_TYPES
        or not isinstance(payload["entity_id"], str)
        or ENTITY_ID.fullmatch(payload["entity_id"]) is None
        or not isinstance(payload["context_id"], str)
        or CONTEXT_ID.fullmatch(payload["context_id"]) is None
        or not isinstance(payload["context"], dict)
        or not isinstance(payload["provenance"], dict)
        or not payload["provenance"]
        or not isinstance(payload["provenance"].get("source_id"), str)
        or not payload["provenance"]["source_id"].strip()
    ):
        return None

    tags = schema_definition.get("tags") if schema_definition else None
    definition = tags.get(payload["tag_id"]) if isinstance(tags, dict) else None
    if (
        schema_definition is None
        or schema_definition.get("registry_id") != payload["registry_id"]
        or schema_definition.get("schema_version") != payload["schema_version"]
        or not isinstance(definition, dict)
        or definition.get("lifecycle_state") != "active"
        or payload["entity_type"] not in definition.get("applies_to", [])
        or not _matches_type(payload["value"], definition.get("value_type"))
    ):
        return None
    allowed_values = definition.get("allowed_values")
    if isinstance(allowed_values, list) and payload["value"] not in allowed_values:
        return None
    return dict(payload)
