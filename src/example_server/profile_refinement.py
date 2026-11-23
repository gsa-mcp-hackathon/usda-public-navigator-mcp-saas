"""Fixed, schema-declared numeric profile refinements."""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation
from typing import Any


def preview_profile_refinement(
    schema: dict[str, Any],
    *,
    target_attribute_id: str,
    method_id: str,
    method_version: str,
    input_values: dict[str, object],
) -> dict[str, object]:
    """Preview one active-schema-declared multiply-by-factor transformation."""
    attributes = schema.get("attributes")
    if not isinstance(attributes, dict):
        return {"status": "invalid_schema"}

    target = attributes.get(target_attribute_id)
    if not isinstance(target, dict) or target.get("type") not in {"integer", "number"}:
        return {"status": "target_attribute_not_numeric"}

    methods = target.get("refinement_methods")
    if not isinstance(methods, list):
        return {"status": "method_not_declared"}
    method = next(
        (
            item
            for item in methods
            if isinstance(item, dict)
            and item.get("method_id") == method_id
            and item.get("method_version") == method_version
        ),
        None,
    )
    if method is None:
        return {"status": "method_not_declared"}

    required_keys = {
        "method_id",
        "method_version",
        "method_type",
        "input_attribute_id",
        "factor",
        "input_unit",
        "output_unit",
        "context",
        "assumptions",
        "uncertainty",
    }
    if set(method) != required_keys or method["method_type"] != "scale_by_factor":
        return {"status": "unsupported_method"}

    input_attribute_id = method["input_attribute_id"]
    input_definition = attributes.get(input_attribute_id)
    if (
        not isinstance(input_attribute_id, str)
        or input_attribute_id == target_attribute_id
        or not isinstance(input_definition, dict)
        or input_definition.get("type") not in {"integer", "number"}
    ):
        return {"status": "invalid_method_metadata"}
    if set(input_values) != {input_attribute_id}:
        return {"status": "required_input_mismatch"}

    input_number = _decimal(input_values[input_attribute_id])
    factor = _decimal(method["factor"])
    if input_number is None or factor is None or factor <= 0:
        return {"status": "numeric_input_required"}

    if (
        not isinstance(method["input_unit"], str)
        or not method["input_unit"]
        or not isinstance(method["output_unit"], str)
        or not method["output_unit"]
        or not isinstance(method["context"], dict)
        or not isinstance(method["assumptions"], list)
        or not all(isinstance(item, str) and item for item in method["assumptions"])
        or not isinstance(method["uncertainty"], str)
        or not method["uncertainty"]
    ):
        return {"status": "invalid_method_metadata"}

    try:
        value = input_number * factor
    except InvalidOperation:
        return {"status": "numeric_overflow"}
    json_value: int | float = (
        int(value) if value == value.to_integral_value() else float(value)
    )
    if isinstance(json_value, float) and not math.isfinite(json_value):
        return {"status": "numeric_overflow"}

    return {
        "status": "ready",
        "schema_registry_id": schema.get("registry_id"),
        "schema_version": schema.get("schema_version"),
        "target_attribute_id": target_attribute_id,
        "value": json_value,
        "unit": method["output_unit"],
        "input_attribute_ids": [input_attribute_id],
        "input_values": {input_attribute_id: input_values[input_attribute_id]},
        "derivation": {"method_id": method_id, "method_version": method_version},
        "provenance_preview": {
            "origin": "derived",
            "confirmation_status": "pending",
            "method_type": method["method_type"],
            "factor": method["factor"],
            "input_unit": method["input_unit"],
            "output_unit": method["output_unit"],
            "context": method["context"],
            "assumptions": method["assumptions"],
            "uncertainty": method["uncertainty"],
        },
        "confirmation_required": True,
    }


def confirmed_derivation_provenance(
    schema: dict[str, Any],
    *,
    target_attribute_id: str,
    derivation: dict[str, object],
    input_values: dict[str, object],
    confirmed_value: object,
) -> dict[str, object] | None:
    """Recompute a confirmed result and return trusted stored provenance."""
    method_id = derivation.get("method_id")
    method_version = derivation.get("method_version")
    if not isinstance(method_id, str) or not isinstance(method_version, str):
        return None
    preview = preview_profile_refinement(
        schema,
        target_attribute_id=target_attribute_id,
        method_id=method_id,
        method_version=method_version,
        input_values=input_values,
    )
    if preview.get("status") != "ready" or preview.get("value") != confirmed_value:
        return None
    method = next(
        item
        for item in schema["attributes"][target_attribute_id]["refinement_methods"]
        if item["method_id"] == method_id and item["method_version"] == method_version
    )
    return {
        "origin": "derived",
        "confirmation_status": "confirmed",
        "schema_registry_id": schema["registry_id"],
        "schema_version": schema["schema_version"],
        "method_id": method_id,
        "method_version": method_version,
        "method_type": method["method_type"],
        "input_attribute_ids": preview["input_attribute_ids"],
        "input_values": preview["input_values"],
        "factor": method["factor"],
        "input_unit": method["input_unit"],
        "output_unit": method["output_unit"],
        "context": method["context"],
        "assumptions": method["assumptions"],
        "uncertainty": method["uncertainty"],
    }


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None
