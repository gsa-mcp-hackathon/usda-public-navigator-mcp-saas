"""Strict, pattern-neutral tenant onboarding specification validation."""

from __future__ import annotations

import json
import re
from typing import Any

from template_packages import valid_handler_endpoint

_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_NAMESPACE = re.compile(r"[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63})*\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}\Z")
_LEVEL = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")
_OPERATION = re.compile(r"[a-z][a-z0-9.-]{0,127}\Z")


class InvalidTenantPackage(ValueError):
    """A tenant onboarding specification is malformed or incompatible."""


def validate_tenant_package(payload: object) -> dict[str, Any]:
    """Validate a bounded tenant spec without embedding pattern-specific rules."""
    fields = {
        "tenant_id",
        "template_id",
        "template_version",
        "endpoint",
        "jurisdictions",
        "catalog_bindings",
        "operation_ids",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        raise InvalidTenantPackage("Tenant specification has an invalid structure.")
    tenant_id = payload["tenant_id"]
    template_id = payload["template_id"]
    version = payload["template_version"]
    if not isinstance(tenant_id, str) or not _ID.fullmatch(tenant_id):
        raise InvalidTenantPackage("Tenant identity is invalid.")
    if not isinstance(template_id, str) or not _ID.fullmatch(template_id):
        raise InvalidTenantPackage("Template identity is invalid.")
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise InvalidTenantPackage("Template version is invalid.")
    endpoint = payload["endpoint"]
    if not valid_handler_endpoint(endpoint):
        raise InvalidTenantPackage(
            "Tenant endpoint is outside the private MCP contract."
        )

    jurisdictions = payload["jurisdictions"]
    if (
        not isinstance(jurisdictions, list)
        or not jurisdictions
        or len(jurisdictions) > 100
    ):
        raise InvalidTenantPackage("At least one bounded jurisdiction is required.")
    normalized_jurisdictions: list[dict[str, str]] = []
    seen_jurisdictions: set[tuple[str, str]] = set()
    for item in jurisdictions:
        if not isinstance(item, dict) or set(item) != {"level", "code"}:
            raise InvalidTenantPackage("Jurisdiction entries require level and code.")
        level, code = item["level"], item["code"]
        if (
            not isinstance(level, str)
            or not _LEVEL.fullmatch(level)
            or not isinstance(code, str)
            or not _CODE.fullmatch(code)
            or (level, code) in seen_jurisdictions
        ):
            raise InvalidTenantPackage("Jurisdiction entry is invalid or duplicated.")
        seen_jurisdictions.add((level, code))
        normalized_jurisdictions.append({"level": level, "code": code})

    bindings = payload["catalog_bindings"]
    if not isinstance(bindings, list) or not bindings or len(bindings) > 100:
        raise InvalidTenantPackage("One to 100 catalog bindings are required.")
    normalized_bindings: list[dict[str, Any]] = []
    seen_bindings: set[tuple[str, str]] = set()
    for item in bindings:
        if not isinstance(item, dict) or set(item) != {"dataset_id", "binding_context"}:
            raise InvalidTenantPackage("Catalog binding has an invalid structure.")
        dataset_id = item["dataset_id"]
        context = item["binding_context"]
        if not isinstance(dataset_id, str) or not _ID.fullmatch(dataset_id):
            raise InvalidTenantPackage("Catalog dataset identity is invalid.")
        if not isinstance(context, dict):
            raise InvalidTenantPackage("Catalog binding context must be an object.")
        required_context = {"catalog_namespace", "catalog_version", "source_id"}
        if not required_context <= context.keys():
            raise InvalidTenantPackage("Catalog binding context is incomplete.")
        namespace = context["catalog_namespace"]
        catalog_version = context["catalog_version"]
        source_id = context["source_id"]
        if (
            not isinstance(namespace, str)
            or not _NAMESPACE.fullmatch(namespace)
            or not isinstance(catalog_version, str)
            or not catalog_version.strip()
            or len(catalog_version) > 64
            or not isinstance(source_id, str)
            or re.fullmatch(r"[a-z][a-z0-9.-]{0,127}", source_id) is None
        ):
            raise InvalidTenantPackage("Catalog binding context is invalid.")
        try:
            encoded_context = json.dumps(
                context, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as exc:
            raise InvalidTenantPackage(
                "Catalog binding context must be JSON data."
            ) from exc
        if len(encoded_context) > 4096:
            raise InvalidTenantPackage("Catalog binding context is too large.")
        identity = (dataset_id, namespace)
        if identity in seen_bindings:
            raise InvalidTenantPackage("Dataset/namespace bindings must be unique.")
        seen_bindings.add(identity)
        normalized_bindings.append(
            {"dataset_id": dataset_id, "binding_context": context}
        )

    operation_ids = payload["operation_ids"]
    if (
        not isinstance(operation_ids, list)
        or not operation_ids
        or len(operation_ids) > 100
        or any(
            not isinstance(value, str) or not _OPERATION.fullmatch(value)
            for value in operation_ids
        )
        or len(operation_ids) != len(set(operation_ids))
    ):
        raise InvalidTenantPackage(
            "Tenant operation bindings are invalid or duplicated."
        )

    return {
        "tenant_id": tenant_id,
        "template_id": template_id,
        "template_version": version,
        "endpoint": endpoint,
        "jurisdictions": normalized_jurisdictions,
        "catalog_bindings": normalized_bindings,
        "operation_ids": operation_ids,
    }
