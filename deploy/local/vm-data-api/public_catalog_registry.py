"""Versioned source and dataset registration contract for the shared catalog."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from typing import Any
from urllib.parse import urlsplit

CONTRACT_VERSION = "public-source-registration-v1"
SOURCE_ID = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
DATASET_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SCHEMA_ID = re.compile(r"^[a-z][a-z0-9.-]{0,127}$")
SCHEMA_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
CRITERIA_COVERAGE = {"mapped", "partial", "not_mapped", "not_assessed"}
RECORD_CONTRACT_VERSION = "normalized-public-record-v1"
RECORD_ROLES = {"program_listing", "criteria", "referral", "context", "history"}


def is_public_https_url(value: object, *, nullable: bool = False) -> bool:
    """Accept public HTTPS references and reject credentials/local/private hosts."""
    if value is None:
        return nullable
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return False
    if parsed.scheme != "https" or not hostname or parsed.username or parsed.password:
        return False
    if hostname.lower() in {
        "localhost",
        "localhost.localdomain",
    } or hostname.lower().endswith((".local", ".internal", ".localhost")):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return True
    return address.is_global


def _is_json_object(value: object, *, maximum_bytes: int = 16_384) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        return (
            len(json.dumps(value, ensure_ascii=True, allow_nan=False).encode("utf-8"))
            <= maximum_bytes
        )
    except (TypeError, ValueError):
        return False


def _valid_authority_context(value: dict[str, Any]) -> bool:
    authority = value.get("authority")
    return (
        isinstance(authority, dict)
        and set(authority)
        == {"level", "owner_name", "verification_status", "evidence_url"}
        and authority.get("level")
        in {"federal", "state", "local", "tribal", "institutional"}
        and isinstance(authority.get("owner_name"), str)
        and 1 <= len(authority["owner_name"].strip()) <= 200
        and authority.get("verification_status") == "verified"
        and is_public_https_url(authority.get("evidence_url"))
    )


def validate_source_registration(payload: object) -> dict[str, object] | None:
    """Validate one source with one or more datasets before a catalog write."""
    if not isinstance(payload, dict) or set(payload) != {
        "contract_version",
        "source",
        "datasets",
    }:
        return None
    source = payload["source"]
    datasets = payload["datasets"]
    if (
        payload["contract_version"] != CONTRACT_VERSION
        or not isinstance(source, dict)
        or set(source)
        != {
            "source_id",
            "source_name",
            "source_url",
            "access_status",
            "source_context",
        }
        or not isinstance(source["source_id"], str)
        or SOURCE_ID.fullmatch(source["source_id"]) is None
        or not isinstance(source["source_name"], str)
        or not 1 <= len(source["source_name"].strip()) <= 200
        or not is_public_https_url(source["source_url"])
        or not isinstance(source["access_status"], str)
        or source["access_status"] not in {"available", "blocked", "metadata_only"}
        or not _is_json_object(source["source_context"])
        or not _valid_authority_context(source["source_context"])
        or not isinstance(datasets, list)
        or not 1 <= len(datasets) <= 100
    ):
        return None

    seen_dataset_ids: set[str] = set()
    seen_source_dataset_ids: set[str] = set()
    for dataset in datasets:
        if not isinstance(dataset, dict) or set(dataset) != {
            "dataset_id",
            "source_dataset_id",
            "title",
            "landing_page",
            "schema_version",
            "namespace_ids",
            "metadata",
        }:
            return None
        dataset_id = dataset["dataset_id"]
        source_dataset_id = dataset["source_dataset_id"]
        title = dataset["title"]
        schema_version = dataset["schema_version"]
        namespace_ids = dataset["namespace_ids"]
        metadata = dataset["metadata"]
        if (
            not isinstance(dataset_id, str)
            or DATASET_ID.fullmatch(dataset_id) is None
            or dataset_id in seen_dataset_ids
            or not isinstance(source_dataset_id, str)
            or not 1 <= len(source_dataset_id.strip()) <= 200
            or source_dataset_id in seen_source_dataset_ids
            or not isinstance(title, str)
            or not 1 <= len(title.strip()) <= 300
            or not is_public_https_url(dataset["landing_page"], nullable=True)
            or not isinstance(schema_version, str)
            or SCHEMA_VERSION.fullmatch(schema_version) is None
            or not isinstance(namespace_ids, list)
            or not namespace_ids
            or any(
                not isinstance(item, str) or SCHEMA_ID.fullmatch(item) is None
                for item in namespace_ids
            )
            or len(namespace_ids) != len(set(namespace_ids))
            or not _is_json_object(metadata)
        ):
            return None
        registry_ids = metadata.get("schema_registry_ids")
        schema_versions = metadata.get("schema_versions")
        if (
            not isinstance(registry_ids, dict)
            or not isinstance(schema_versions, dict)
            or set(registry_ids) != set(namespace_ids)
            or set(schema_versions) != set(namespace_ids)
            or any(
                not isinstance(registry_ids[item], str)
                or not SCHEMA_ID.fullmatch(registry_ids[item])
                or not isinstance(schema_versions[item], str)
                or not SCHEMA_VERSION.fullmatch(schema_versions[item])
                for item in namespace_ids
            )
            or schema_version not in schema_versions.values()
            or not isinstance(metadata.get("catalog_namespace"), str)
            or not metadata["catalog_namespace"].strip()
            or metadata.get("criteria_coverage") not in CRITERIA_COVERAGE
            or metadata.get("record_contract_version") != RECORD_CONTRACT_VERSION
            or metadata.get("visibility_scope") != "cluster_public"
        ):
            return None
        criteria_profile_schema_refs = metadata.get("criteria_profile_schema_refs")
        if criteria_profile_schema_refs is not None:
            if (
                not isinstance(criteria_profile_schema_refs, list)
                or not criteria_profile_schema_refs
                or any(
                    not isinstance(reference, dict)
                    or set(reference) != {"registry_id", "schema_version"}
                    or not isinstance(reference.get("registry_id"), str)
                    or SCHEMA_ID.fullmatch(reference["registry_id"]) is None
                    or not isinstance(reference.get("schema_version"), str)
                    or SCHEMA_VERSION.fullmatch(reference["schema_version"]) is None
                    for reference in criteria_profile_schema_refs
                )
                or len(
                    {
                        (reference["registry_id"], reference["schema_version"])
                        for reference in criteria_profile_schema_refs
                        if isinstance(reference, dict)
                        and isinstance(reference.get("registry_id"), str)
                        and isinstance(reference.get("schema_version"), str)
                    }
                )
                != len(criteria_profile_schema_refs)
            ):
                return None
        seen_dataset_ids.add(dataset_id)
        seen_source_dataset_ids.add(source_dataset_id)
    return payload


def canonical_registration_digest(registration: dict[str, Any]) -> str:
    """Return a stable digest for a validated registration request."""
    canonical = json.dumps(
        registration, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
