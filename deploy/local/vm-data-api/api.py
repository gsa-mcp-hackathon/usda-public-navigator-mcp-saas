"""Persistence Access API: the bounded HTTP boundary to the sandbox PostgreSQL.

This is the only application that holds database credentials. Core and tenant
runtimes call it over HTTP and never receive a DSN. It serves bounded routes for
health and probes, Core route reads, tenant and template-package lifecycle,
public catalog reads and ingestion, source-policy and program-candidate
records, profile and saved-interest storage, requirements packages, criteria
rulesets, and route telemetry. Each route validates its payload and checks a
purpose-scoped token before touching the database.

Run:
    python api.py                 # listens on 0.0.0.0:8080 inside the container

Configuration (environment; secrets are read from files, never from variables):
    DB_DSN_FILE, API_TOKEN_FILE, and the per-capability *_TOKEN_FILE settings
    defined below, plus API_EVALUATION_FIXTURE_SETS_FILE.

See README.md in this directory for the route and token model.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from criteria_rulesets import (
    CriteriaRulesetConflict,
    InvalidCriteriaRuleset,
    read_active_criteria_ruleset,
    register_criteria_ruleset,
)
from evaluation_fixtures import load_fixture_sets, valid_registration_command
from operator_audit import validate_operator_audit_context
from profile_interests import validate_saved_interest
from program_candidate_publications import (
    InvalidProgramCandidatePublication,
    ProgramCandidatePublicationConflict,
    approved_policy_entry_for_url,
    build_public_listing_record,
    validate_program_candidate_publication_lifecycle,
    validate_program_candidate_publication_request,
)
from program_candidate_records import (
    InvalidProgramCandidate,
    ProgramCandidateConflict,
    ProgramCandidateDecisionConflict,
    ProgramCandidateNotFound,
    program_candidate_sha256,
    validate_program_candidate,
    validate_program_candidate_decision,
    validate_program_candidate_registration,
)
from public_catalog_registry import (
    RECORD_CONTRACT_VERSION,
    RECORD_ROLES,
    canonical_registration_digest,
    is_public_https_url,
    validate_source_registration,
)
from pydantic import SecretStr, ValidationError
from schema_tags import validate_assignment
from source_policy_manifests import (
    InvalidSourcePolicyManifest,
    SourcePolicyActivationConflict,
    SourcePolicyDecisionConflict,
    SourcePolicyManifestConflict,
    SourcePolicyManifestNotFound,
    source_policy_manifest_sha256,
    validate_manifest_registration,
    validate_source_policy_activation,
    validate_source_policy_decision,
    validate_source_policy_manifest,
)
from template_packages import (
    IncompatibleTemplatePackage,
    InvalidTemplatePackage,
    valid_handler_endpoint,
    validate_template_package,
)
from tenant_packages import InvalidTenantPackage, validate_tenant_package

from example_server.profile_refinement import confirmed_derivation_provenance
from example_server.registry import (
    PostgresRegistryRepository,
    SchemaDefinition,
    SchemaProjectionManifest,
)

DSN_FILE = Path(os.environ.get("DB_DSN_FILE", "/run/secrets/mcp-saas-data-api-dsn"))
PUBLIC_CATALOG_INGEST_DSN_FILE = Path(
    os.environ.get(
        "PUBLIC_CATALOG_INGEST_DSN_FILE",
        "/run/secrets/mcp-saas-public-catalog-ingest-dsn",
    )
)
TOKEN_FILE = Path(
    os.environ.get("API_TOKEN_FILE", "/run/secrets/mcp-saas-data-api-token")
)
REGISTRY_READ_TOKEN_FILE = Path(
    os.environ.get(
        "API_REGISTRY_READ_TOKEN_FILE", "/run/secrets/mcp-saas-registry-read-token"
    )
)
REGISTRY_WRITE_TOKEN_FILE = Path(
    os.environ.get(
        "API_REGISTRY_WRITE_TOKEN_FILE", "/run/secrets/mcp-saas-registry-write-token"
    )
)
TEMPLATE_REGISTRY_WRITE_TOKEN_FILE = Path(
    os.environ.get(
        "API_TEMPLATE_REGISTRY_WRITE_TOKEN_FILE",
        "/run/secrets/mcp-saas-template-registry-write-token",
    )
)
TENANT_REGISTRY_WRITE_TOKEN_FILE = Path(
    os.environ.get(
        "API_TENANT_REGISTRY_WRITE_TOKEN_FILE",
        "/run/secrets/mcp-saas-tenant-registry-write-token",
    )
)
ROUTE_TELEMETRY_WRITE_TOKEN_FILE = Path(
    os.environ.get(
        "API_ROUTE_TELEMETRY_WRITE_TOKEN_FILE",
        "/run/secrets/mcp-saas-route-telemetry-write-token",
    )
)
PROFILE_READ_TOKEN_FILE = Path(
    os.environ.get(
        "API_PROFILE_READ_TOKEN_FILE", "/run/secrets/mcp-saas-profile-read-token"
    )
)
PROFILE_FIXTURE_READ_TOKEN_FILE = Path(
    os.environ.get(
        "API_PROFILE_FIXTURE_READ_TOKEN_FILE",
        "/run/secrets/mcp-saas-profile-fixture-read-token",
    )
)
PROFILE_WRITE_TOKEN_FILE = Path(
    os.environ.get(
        "API_PROFILE_WRITE_TOKEN_FILE", "/run/secrets/mcp-saas-profile-write-token"
    )
)
_DEFAULT_FIXTURE_SETS_FILE = Path(__file__).with_name("fixture-sets.v1.json")
if not _DEFAULT_FIXTURE_SETS_FILE.is_file():
    _DEFAULT_FIXTURE_SETS_FILE = (
        Path(__file__).resolve().parents[3]
        / "configs"
        / "evaluation-fixture-sets.v1.json"
    )
EVALUATION_FIXTURE_SETS_FILE = Path(
    os.environ.get("API_EVALUATION_FIXTURE_SETS_FILE", _DEFAULT_FIXTURE_SETS_FILE)
)
CATALOG_READ_TOKEN_FILE = Path(
    os.environ.get(
        "API_CATALOG_READ_TOKEN_FILE", "/run/secrets/mcp-saas-catalog-read-token"
    )
)
PUBLIC_CATALOG_WRITE_TOKEN_FILE = Path(
    os.environ.get(
        "API_PUBLIC_CATALOG_WRITE_TOKEN_FILE",
        "/run/secrets/mcp-saas-public-catalog-write-token",
    )
)
CRITERIA_RULESET_WRITE_TOKEN_FILE = Path(
    os.environ.get(
        "API_CRITERIA_RULESET_WRITE_TOKEN_FILE",
        "/run/secrets/mcp-saas-criteria-ruleset-write-token",
    )
)
CRITERIA_RULESET_WRITE_TOKEN_FILES_JSON = os.environ.get(
    "API_CRITERIA_RULESET_WRITE_TOKEN_FILES_JSON"
)
CRITERIA_RULESET_READ_TOKEN_FILES_JSON = os.environ.get(
    "API_CRITERIA_RULESET_READ_TOKEN_FILES_JSON"
)
REQUIREMENTS_PACKAGE_WRITE_TOKEN_FILE = Path(
    os.environ.get(
        "API_REQUIREMENTS_PACKAGE_WRITE_TOKEN_FILE",
        "/run/secrets/mcp-saas-requirements-package-write-token",
    )
)
MAX_BODY = 16384
MAX_TEMPLATE_PACKAGE_BODY = 131072
MAX_REQUIREMENTS_PACKAGE_BODY = 32768
MAX_PUBLIC_CATALOG_INGEST_BODY = 2_097_152
MAX_CRITERIA_RULESET_BODY = 262_144
MAX_SOURCE_REGISTRATION_BODY = 1_800_000
MAX_PUBLIC_CATALOG_PREVIEW_BYTES = 262_144
MAX_SOURCE_POLICY_MANIFEST_BODY = 32768
MAX_PROGRAM_CANDIDATE_BODY = 131072
MAX_SOURCE_POLICY_ACTIVATION_BODY = 8192
MAX_SCHEMA_PROJECTION_BODY = 2_097_152
MAX_ROUTE_EVENT_BODY = 4096
MAX_PROFILE_BODY = 16384
ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
PROGRAM_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}\Z")
NAMESPACE_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63})*\Z")
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
USE_CASE_PATTERN = re.compile(r"[a-z][a-z0-9.-]{0,127}\Z")
OPERATION_PATTERN = re.compile(r"[a-z][a-z0-9.-]{0,127}\Z")
INGESTION_REJECTION_CODE_PATTERN = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")


def _cluster_bound_criteria_token_file(
    configured: str | None,
    cluster_id: str,
    legacy_file: Path,
) -> Path | None:
    """Resolve a unique token-file binding for a cluster namespace."""
    if not ID_PATTERN.fullmatch(cluster_id):
        return None
    if configured is None:
        return legacy_file if cluster_id == "eligibility" else None
    try:
        bindings = json.loads(configured)
    except (TypeError, ValueError):
        return None
    if not isinstance(bindings, dict) or any(
        not isinstance(key, str)
        or not ID_PATTERN.fullmatch(key)
        or not isinstance(value, str)
        or not value.strip()
        for key, value in bindings.items()
    ):
        return None
    normalized_paths = [str(Path(value).resolve()) for value in bindings.values()]
    if len(normalized_paths) != len(set(normalized_paths)):
        return None
    path = bindings.get(cluster_id)
    return Path(path) if path is not None else None


def criteria_ruleset_writer_token_file(cluster_id: str) -> Path | None:
    """Resolve the dedicated writer capability bound to one cluster ID."""
    return _cluster_bound_criteria_token_file(
        CRITERIA_RULESET_WRITE_TOKEN_FILES_JSON,
        cluster_id,
        CRITERIA_RULESET_WRITE_TOKEN_FILE,
    )


def criteria_ruleset_reader_token_file(cluster_id: str) -> Path | None:
    """Resolve the read capability bound to one cluster ID."""
    return _cluster_bound_criteria_token_file(
        CRITERIA_RULESET_READ_TOKEN_FILES_JSON, cluster_id, CATALOG_READ_TOKEN_FILE
    )


PROFILE_ID_PATTERN = re.compile(r"(?:demo|synthetic)-[a-z0-9][a-z0-9-]{0,62}\Z")
PROFILE_REFERENCE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
PROFILE_RESUME_CLAIM_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
PROFILE_SCOPE_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
PROFILE_KIND_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
PROFILE_ATTRIBUTE_PATTERN = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\Z")


class TemplatePackageConflict(RuntimeError):
    """An immutable package identity was reused with different content."""


class TenantLifecycleConflict(RuntimeError):
    """A tenant lifecycle transition is stale or conflicts with its receipt."""

    def __init__(self, message: str, *, reason: str = "lifecycle_conflict") -> None:
        super().__init__(message)
        self.reason = reason


class TemplatePackageNotFound(RuntimeError):
    """A requested template package or operation is not registered."""


class RequirementsPackageOutOfScope(RuntimeError):
    """The package tenant and template version are not actively registered."""


class RequirementsPackageRevisionConflict(RuntimeError):
    """A package enrichment was based on a stale revision."""

    def __init__(self, current_revision: int) -> None:
        super().__init__("Requirements package revision is stale.")
        self.current_revision = current_revision


def read_secret(path: Path) -> str:
    """Read a secret from a file and strip surrounding whitespace."""
    return path.read_text(encoding="utf-8").strip()


def valid_probe(payload: object):
    """Return the probe id if the payload is a valid probe, otherwise None."""
    if not isinstance(payload, dict) or set(payload) != {"probe_id", "label"}:
        return None
    try:
        probe_id = str(uuid.UUID(str(payload["probe_id"])))
    except (ValueError, TypeError, AttributeError):
        return None
    label = payload["label"]
    if not isinstance(label, str) or not re.fullmatch(
        r"mcp-saas-e2e-[a-z0-9-]{1,40}", label
    ):
        return None
    return probe_id, {"kind": "synthetic-connectivity-probe", "label": label}


def valid_pattern_catalog_binding(payload: object) -> dict[str, object] | None:
    """Validate a tenant/pattern catalog binding without accepting SQL or extras."""
    fields = {
        "tenant_id",
        "cluster_id",
        "pattern_version",
        "dataset_id",
        "binding_context",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        return None
    tenant_id = payload["tenant_id"]
    cluster_id = payload["cluster_id"]
    pattern_version = payload["pattern_version"]
    dataset_id = payload["dataset_id"]
    context = payload["binding_context"]
    if (
        not all(isinstance(value, str) for value in (tenant_id, cluster_id, dataset_id))
        or not ID_PATTERN.fullmatch(tenant_id)
        or not ID_PATTERN.fullmatch(cluster_id)
        or not ID_PATTERN.fullmatch(dataset_id)
        or not isinstance(pattern_version, str)
        or not VERSION_PATTERN.fullmatch(pattern_version)
        or not isinstance(context, dict)
        or not isinstance(context.get("catalog_namespace"), str)
        or not NAMESPACE_PATTERN.fullmatch(context["catalog_namespace"])
        or not isinstance(context.get("catalog_version"), str)
        or not context["catalog_version"]
        or len(context["catalog_version"]) > 64
        or not isinstance(context.get("source_id"), str)
        or not re.fullmatch(r"[a-z][a-z0-9.-]{0,127}", context["source_id"])
    ):
        return None
    try:
        if len(json.dumps(context, separators=(",", ":"))) > 4096:
            return None
    except (TypeError, ValueError):
        return None
    return {
        "tenant_id": tenant_id,
        "cluster_id": cluster_id,
        "pattern_version": pattern_version,
        "dataset_id": dataset_id,
        "binding_context": context,
    }


def connect_db():
    """Open a database connection using the DSN read from its protected file."""
    import psycopg

    return psycopg.connect(read_secret(DSN_FILE), connect_timeout=4)


def project_schema_registry(payload: object) -> dict[str, object]:
    """Apply a validated schema-only manifest through the Data API DB role."""
    if not isinstance(payload, dict) or set(payload) != {
        "manifest",
        "schemas",
        "git_revision",
    }:
        raise ValueError("invalid_schema_projection")
    manifest = SchemaProjectionManifest.model_validate(payload["manifest"])
    if (
        re.fullmatch(
            r"usda-mcp-saas-vs8-schema-projection-v[1-9][0-9]*", manifest.manifest_id
        )
        is None
    ):
        raise ValueError("unsupported_schema_projection_manifest")
    definitions = payload["schemas"]
    if not isinstance(definitions, list) or len(definitions) > 128:
        raise ValueError("invalid_schema_projection")
    schemas = [SchemaDefinition.model_validate(item) for item in definitions]
    expected = {(item.registry_id, item.schema_version) for item in manifest.schemas}
    actual = {(item.registry_id, item.schema_version) for item in schemas}
    if actual != expected:
        raise ValueError("schema_definitions_do_not_match_manifest")
    git_revision = payload["git_revision"]
    if not isinstance(git_revision, str):
        raise ValueError("invalid_schema_projection_revision")
    repository = PostgresRegistryRepository(SecretStr(read_secret(DSN_FILE)))
    repository.reconcile_schemas_only(manifest, schemas, git_revision)
    return {
        "status": "projected",
        "manifest_id": manifest.manifest_id,
        "manifest_version": manifest.manifest_version,
        "schema_count": len(schemas),
        "git_revision": git_revision,
    }


def connect_public_catalog_ingest_db():
    """Open the separate least-privilege public-catalog writer connection."""
    import psycopg

    return psycopg.connect(
        read_secret(PUBLIC_CATALOG_INGEST_DSN_FILE), connect_timeout=4
    )


def _valid_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def valid_public_catalog_ingestion(payload: object) -> dict[str, object] | None:
    """Validate a compact generic dataset snapshot before any database write."""
    required_fields = {
        "outcome",
        "source_version",
        "retrieved_at",
        "transform_version",
        "validation_outcome",
        "detail",
        "records",
    }
    if (
        not isinstance(payload, dict)
        or not required_fields.issubset(payload)
        or set(payload)
        - required_fields
        - {"rejected_count", "rejection_reasons", "record_contract_version"}
    ):
        return None
    outcome = payload["outcome"]
    validation = payload["validation_outcome"]
    records = payload["records"]
    detail = payload["detail"]
    rejected_count = payload.get("rejected_count", 0)
    rejection_reasons = payload.get("rejection_reasons", [])
    record_contract_version = payload.get("record_contract_version")
    if (
        not isinstance(outcome, str)
        or not isinstance(validation, str)
        or outcome not in {"succeeded", "failed", "blocked"}
        or validation not in {"valid", "invalid", "unavailable", "partial"}
        or not isinstance(records, list)
        or len(records) > 100
        or not isinstance(detail, str)
        or len(detail) > 240
        or isinstance(rejected_count, bool)
        or not isinstance(rejected_count, int)
        or not 0 <= rejected_count <= 100
        or not isinstance(rejection_reasons, list)
        or len(rejection_reasons) > 20
        or not isinstance(payload["transform_version"], str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", payload["transform_version"])
        or record_contract_version not in {None, RECORD_CONTRACT_VERSION}
        or (
            record_contract_version == RECORD_CONTRACT_VERSION
            and not {"rejected_count", "rejection_reasons"}.issubset(payload)
        )
    ):
        return None
    reason_total = 0
    seen_reasons: set[str] = set()
    for reason in rejection_reasons:
        if (
            not isinstance(reason, dict)
            or set(reason) != {"code", "count"}
            or not isinstance(reason["code"], str)
            or INGESTION_REJECTION_CODE_PATTERN.fullmatch(reason["code"]) is None
            or reason["code"] in seen_reasons
            or isinstance(reason["count"], bool)
            or not isinstance(reason["count"], int)
            or not 1 <= reason["count"] <= 100
        ):
            return None
        seen_reasons.add(reason["code"])
        reason_total += reason["count"]
    if reason_total != rejected_count:
        return None
    source_version = payload["source_version"]
    retrieved_at = payload["retrieved_at"]
    if source_version is not None and (
        not isinstance(source_version, str) or len(source_version) > 200
    ):
        return None
    if retrieved_at is not None and not _valid_timestamp(retrieved_at):
        return None
    if outcome == "succeeded":
        if (
            validation not in {"valid", "partial"}
            or (not records and (validation != "partial" or rejected_count == 0))
            or not source_version
            or not retrieved_at
            or (validation == "valid" and rejected_count != 0)
            or (validation == "partial" and rejected_count == 0)
            or len(records) + rejected_count > 100
        ):
            return None
    elif outcome == "failed":
        if validation != "invalid" or records:
            return None
    elif validation != "unavailable" or records or rejected_count:
        return None
    record_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "source_record_id",
            "record_type",
            "canonical_attributes",
            "semantic_context",
            "source_metadata",
            "source_url",
            "content_hash",
            "schema_version",
            "observed_at",
        }:
            return None
        if (
            not isinstance(record["source_record_id"], str)
            or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", record["source_record_id"]
            )
            or not isinstance(record["record_type"], str)
            or not re.fullmatch(r"[a-z][a-z0-9._-]{0,63}", record["record_type"])
            or not isinstance(record["schema_version"], str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", record["schema_version"])
            or not all(
                isinstance(record[key], dict)
                for key in (
                    "canonical_attributes",
                    "semantic_context",
                    "source_metadata",
                )
            )
            or not is_public_https_url(record["source_url"])
            or not isinstance(record["content_hash"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", record["content_hash"])
            or not _valid_timestamp(record["observed_at"])
        ):
            return None
        record_id = record["source_record_id"]
        metadata = record["source_metadata"]
        if (
            record_id in record_ids
            or metadata.get("raw_payload_stored", False) is not False
        ):
            return None
        record_ids.add(record_id)
        if (
            record_contract_version == RECORD_CONTRACT_VERSION
            and not _valid_normalized_record_v1(record)
        ):
            return None
    return payload


def _valid_normalized_record_v1(record: dict[str, object]) -> bool:
    """Require the provenance envelope for records using the shared v1 contract."""
    context = record["semantic_context"]
    metadata = record["source_metadata"]
    attributes = record["canonical_attributes"]
    jurisdiction = context.get("jurisdiction")
    provenance = metadata.get("field_provenance")
    return (
        context.get("record_role") in RECORD_ROLES
        and context.get("criteria_coverage")
        in {"mapped", "partial", "not_mapped", "not_assessed"}
        and isinstance(jurisdiction, dict)
        and _valid_source_jurisdiction(jurisdiction)
        and isinstance(metadata.get("effective_at"), (str, type(None)))
        and (
            metadata.get("effective_at") is None
            or _valid_timestamp(metadata["effective_at"])
        )
        and isinstance(metadata.get("source_id"), str)
        and re.fullmatch(r"[a-z][a-z0-9.-]{0,127}", metadata["source_id"]) is not None
        and isinstance(metadata.get("source_version"), (str, type(None)))
        and (
            metadata.get("source_version") is None
            or len(metadata["source_version"]) <= 200
        )
        and _valid_timestamp(metadata.get("retrieved_at"))
        and isinstance(metadata.get("transform_version"), str)
        and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", metadata["transform_version"])
        is not None
        and metadata.get("raw_payload_stored") is False
        and isinstance(provenance, dict)
        and set(provenance) == set(attributes)
        and all(
            isinstance(attribute_id, str)
            and isinstance(item, dict)
            and set(item) == {"source_url", "observed_at", "extraction_method"}
            and is_public_https_url(item["source_url"])
            and _valid_timestamp(item["observed_at"])
            and item["extraction_method"]
            in {
                "api_field",
                "json_pointer",
                "html_selector",
                "document_location",
                "operator_review",
            }
            for attribute_id, item in provenance.items()
        )
    )


def _valid_source_jurisdiction(value: dict[str, object]) -> bool:
    """Validate jurisdiction syntax without restricting supported states."""
    if set(value) - {"level", "country_code", "state_code", "region_code"}:
        return False
    if value.get("level") not in {
        "federal",
        "national",
        "state",
        "territory",
        "local",
        "tribal",
        "institutional",
    }:
        return False
    if (
        not isinstance(value.get("country_code"), str)
        or re.fullmatch(r"[A-Z]{2}", value["country_code"]) is None
    ):
        return False
    if value["level"] in {"state", "territory"} and (
        not isinstance(value.get("state_code"), str)
        or re.fullmatch(r"[A-Z]{2}", value["state_code"]) is None
    ):
        return False
    return value.get("region_code") is None or (
        isinstance(value["region_code"], str)
        and re.fullmatch(r"[A-Z0-9-]{1,16}", value["region_code"]) is not None
    )


def _json_object(value: object) -> dict[str, object] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _value_matches_attribute(value: object, definition: dict[str, object]) -> bool:
    attribute_type = definition.get("type")
    if isinstance(attribute_type, str):
        allowed_types = (attribute_type,)
    elif (
        isinstance(attribute_type, list)
        and attribute_type
        and all(isinstance(item, str) for item in attribute_type)
    ):
        allowed_types = tuple(attribute_type)
    else:
        return False

    type_matchers = {
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: (
            isinstance(item, (int, float)) and not isinstance(item, bool)
        ),
        "boolean": lambda item: isinstance(item, bool),
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "null": lambda item: item is None,
    }
    matched_type = next(
        (
            item_type
            for item_type in allowed_types
            if item_type in type_matchers and type_matchers[item_type](value)
        ),
        None,
    )
    if matched_type is None:
        return False
    if matched_type == "array" and isinstance(definition.get("items"), dict):
        item_definition = definition["items"]
        required = item_definition.get("required", [])
        properties = item_definition.get("properties", {})
        if (
            item_definition.get("type") != "object"
            or not isinstance(required, list)
            or not isinstance(properties, dict)
            or any(not isinstance(item, dict) for item in value)
        ):
            return False
        for item in value:
            if any(key not in item for key in required):
                return False
            if item_definition.get("additionalProperties") is False and any(
                key not in properties for key in item
            ):
                return False
            if any(
                key not in properties
                or not _value_matches_attribute(item[key], properties[key])
                for key in item
            ):
                return False
    if "allowed_values" in definition and value not in definition["allowed_values"]:
        return False
    if "enum" in definition and value not in definition["enum"]:
        return False
    if matched_type == "null":
        return True
    if (
        matched_type == "string"
        and "minLength" in definition
        and (not isinstance(value, str) or len(value) < definition["minLength"])
    ):
        return False
    if (
        matched_type == "string"
        and "maxLength" in definition
        and (not isinstance(value, str) or len(value) > definition["maxLength"])
    ):
        return False
    if matched_type == "string" and "pattern" in definition:
        pattern = definition["pattern"]
        if not isinstance(pattern, str) or not isinstance(value, str):
            return False
        try:
            # Patterns control anchoring; JSON Schema pattern uses search semantics.
            if re.search(pattern, value) is None:
                return False
        except re.error:
            return False
    if matched_type in {"integer", "number"}:
        for bound in ("minimum", "maximum"):
            if bound in definition:
                limit = definition[bound]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not isinstance(limit, (int, float))
                    or (bound == "minimum" and value < limit)
                    or (bound == "maximum" and value > limit)
                ):
                    return False
    return True


def _registered_dataset_attributes(
    connection, dataset: tuple[object, ...]
) -> dict[str, dict[str, object]] | None:
    """Resolve a dataset's declared namespaces to active global definitions."""
    schema_version, namespace_ids, metadata_value = dataset[3:6]
    metadata = _json_object(metadata_value)
    registry_ids = metadata.get("schema_registry_ids") if metadata else None
    schema_versions = metadata.get("schema_versions") if metadata else None
    if (
        not isinstance(namespace_ids, (list, tuple))
        or not namespace_ids
        or any(not isinstance(item, str) for item in namespace_ids)
        or len(set(namespace_ids)) != len(namespace_ids)
        or not isinstance(registry_ids, dict)
        or not isinstance(schema_versions, dict)
        or set(registry_ids) != set(namespace_ids)
        or set(schema_versions) != set(namespace_ids)
        or schema_version not in schema_versions.values()
    ):
        return None

    attributes: dict[str, dict[str, object]] = {}
    for namespace_id in namespace_ids:
        registry_id = registry_ids[namespace_id]
        version = schema_versions[namespace_id]
        if not all(isinstance(item, str) and item for item in (registry_id, version)):
            return None
        row = connection.execute(
            """SELECT definition FROM mcp_saas.schema_registry
               WHERE registry_id = %s AND schema_version = %s
                 AND lifecycle_state = 'active'""",
            (registry_id, version),
        ).fetchone()
        if row is None:
            return None
        definition = _json_object(row[0])
        namespaces = definition.get("namespaces") if definition else None
        registered_attributes = definition.get("attributes") if definition else None
        if (
            definition is None
            or not isinstance(namespaces, dict)
            or namespace_id not in namespaces
            or not isinstance(registered_attributes, dict)
        ):
            return None
        for attribute_id, attribute_definition in registered_attributes.items():
            if not isinstance(attribute_id, str):
                return None
            if attribute_id.split(".", maxsplit=1)[0] != namespace_id:
                continue
            if not isinstance(attribute_definition, dict) or attribute_id in attributes:
                return None
            attributes[attribute_id] = attribute_definition
    return attributes or None


def _registered_criteria_profile_attributes(
    connection,
    dataset: tuple[object, ...],
    fallback_attributes: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]] | None:
    """Resolve declared profile schemas used by criteria, separate from row schemas.

    Older datasets omit criteria_profile_schema_refs and continue using their
    record schemas for rule targets. New mapped-rule datasets declare explicit
    active profile schemas so row validation does not require profile fields to
    be stored in the catalog record namespace.
    """
    metadata = _json_object(dataset[5])
    if metadata is None:
        return None
    if "criteria_profile_schema_refs" not in metadata:
        return fallback_attributes
    references = metadata.get("criteria_profile_schema_refs")
    if not isinstance(references, list) or not references:
        return None

    attributes: dict[str, dict[str, object]] = {}
    for reference in references:
        if (
            not isinstance(reference, dict)
            or set(reference) != {"registry_id", "schema_version"}
            or not isinstance(reference.get("registry_id"), str)
            or re.fullmatch(r"[a-z][a-z0-9.-]{0,127}", reference["registry_id"]) is None
            or not isinstance(reference.get("schema_version"), str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", reference["schema_version"])
            is None
        ):
            return None
        row = connection.execute(
            """SELECT definition FROM mcp_saas.schema_registry
               WHERE registry_id = %s AND schema_version = %s
                 AND lifecycle_state = 'active'""",
            (reference["registry_id"], reference["schema_version"]),
        ).fetchone()
        definition = _json_object(row[0]) if row is not None else None
        namespaces = definition.get("namespaces") if definition else None
        registered_attributes = definition.get("attributes") if definition else None
        if (
            not isinstance(namespaces, dict)
            or not namespaces
            or not isinstance(registered_attributes, dict)
        ):
            return None
        for attribute_id, attribute_definition in registered_attributes.items():
            if not isinstance(attribute_id, str):
                return None
            namespace_id = attribute_id.split(".", maxsplit=1)[0]
            if namespace_id not in namespaces:
                continue
            if not isinstance(attribute_definition, dict) or attribute_id in attributes:
                return None
            attributes[attribute_id] = attribute_definition
    return attributes or None


def _record_matches_dataset_schema(
    dataset: tuple[object, ...],
    record: dict[str, object],
    source_id: str,
    attributes: dict[str, dict[str, object]],
    criteria_profile_attributes: dict[str, dict[str, object]] | None = None,
) -> bool:
    canonical_attributes = record["canonical_attributes"]
    metadata = _json_object(dataset[5])
    namespace_ids = set(dataset[4]) if isinstance(dataset[4], (list, tuple)) else set()
    if (
        attributes is None
        or record["schema_version"] != dataset[3]
        or not isinstance(canonical_attributes, dict)
        or not canonical_attributes
        or not namespace_ids
        or not isinstance(metadata, dict)
        or record["semantic_context"].get("catalog_namespace")
        != metadata.get("catalog_namespace")
    ):
        return False
    for attribute_id, value in canonical_attributes.items():
        if (
            not isinstance(attribute_id, str)
            or attribute_id.split(".", maxsplit=1)[0] not in namespace_ids
            or attribute_id not in attributes
            or not _value_matches_attribute(value, attributes[attribute_id])
        ):
            return False
    criteria_ids = {
        f"{metadata.get('catalog_namespace')}.criteria",
        "eligibility.program.criteria",
    }
    rule_attributes = (
        attributes
        if criteria_profile_attributes is None
        else criteria_profile_attributes
    )
    for criteria_id in criteria_ids:
        criteria = canonical_attributes.get(criteria_id)
        if criteria is None:
            continue
        if not isinstance(criteria, list):
            return False
        for criterion in criteria:
            if not isinstance(criterion, dict):
                return False
            criterion_attribute = criterion.get("attribute")
            criterion_value = criterion.get("value")
            operator = criterion.get("operator")
            if (
                not isinstance(criterion_attribute, str)
                or criterion_attribute not in rule_attributes
                or criterion.get("source_id") != source_id
            ):
                return False
            value_definition = rule_attributes[criterion_attribute]
            if operator == "lte_by":
                context_attribute = criterion.get("context_attribute")
                thresholds = criterion_value
                context_definition = (
                    rule_attributes.get(context_attribute)
                    if isinstance(context_attribute, str)
                    else None
                )
                if (
                    not isinstance(context_attribute, str)
                    or not isinstance(context_definition, dict)
                    or context_definition.get("type") != "integer"
                    or not isinstance(thresholds, dict)
                    or not thresholds
                    or any(
                        not isinstance(key, str)
                        or re.fullmatch(r"[0-9]+", key) is None
                        or int(key) < 1
                        or not _value_matches_attribute(value, value_definition)
                        for key, value in thresholds.items()
                    )
                ):
                    return False
                continue
            values = (
                criterion_value
                if isinstance(criterion_value, list)
                else [criterion_value]
            )
            if any(
                not _value_matches_attribute(value, value_definition)
                for value in values
            ):
                return False
    return True


def _public_dataset_run_summary(
    row: tuple[object, ...] | None,
) -> dict[str, object] | None:
    """Convert one ingestion-run row to a bounded public status summary."""
    if row is None:
        return None
    details = row[2] if isinstance(row[2], dict) else {}
    return {
        "run_id": row[0],
        "status": row[1],
        "details": details,
        "started_at": row[3].isoformat(),
        "completed_at": row[4].isoformat() if row[4] else None,
        "fetched_count": row[5],
        "inserted_count": row[6],
        "updated_count": row[7],
        "duplicate_count": row[8],
        "rejected_count": details.get("rejected_count", 0),
    }


def summarize_public_dataset_freshness(
    latest_run: dict[str, object] | None,
    latest_successful_run: dict[str, object] | None,
    *,
    refresh_mode: str | None,
) -> dict[str, object]:
    """Report source health from observed runs without inventing an age threshold."""
    if latest_run is None:
        health_status = "unknown"
        freshness_status = "unknown"
        latest_details: dict[str, object] = {}
    else:
        status = latest_run["status"]
        latest_details = latest_run["details"]
        if not isinstance(latest_details, dict):
            latest_details = {}
        if (
            status == "succeeded"
            and latest_details.get("validation_outcome") == "partial"
        ):
            health_status = "degraded"
            freshness_status = "partial"
        elif status == "succeeded":
            health_status = "healthy"
            freshness_status = "fresh_as_of_last_check"
        elif status in {"failed", "blocked"}:
            health_status = "degraded"
            freshness_status = "stale" if latest_successful_run else "unavailable"
        elif status == "running":
            health_status = "refreshing"
            freshness_status = "refreshing"
        else:
            health_status = "unknown"
            freshness_status = "unknown"

    successful_details = (
        latest_successful_run["details"] if latest_successful_run else {}
    )
    if not isinstance(successful_details, dict):
        successful_details = {}
    if not isinstance(latest_details, dict):
        latest_details = {}
    counts = (
        {
            key: latest_run[key]
            for key in (
                "fetched_count",
                "inserted_count",
                "updated_count",
                "duplicate_count",
                "rejected_count",
            )
        }
        if latest_run
        else {
            "fetched_count": 0,
            "inserted_count": 0,
            "updated_count": 0,
            "duplicate_count": 0,
            "rejected_count": 0,
        }
    )
    return {
        "health_status": health_status,
        "freshness_status": freshness_status,
        "latest_run_status": latest_run["status"] if latest_run else None,
        "last_attempt_at": latest_run["started_at"] if latest_run else None,
        "last_attempt_completed_at": latest_run["completed_at"] if latest_run else None,
        "last_successful_retrieval_at": successful_details.get("retrieved_at"),
        "source_version": latest_details.get("source_version")
        or successful_details.get("source_version"),
        "validation_outcome": latest_details.get("validation_outcome"),
        "counts": counts,
        "refresh_mode": refresh_mode,
        "time_based_stale_after_seconds": None,
    }


def read_public_dataset(
    dataset_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    source_record_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, object] | None:
    """Read one bounded page of active rows and current source outcome."""
    if (
        not 1 <= limit <= 1000
        or not 0 <= offset <= 10_000_000
        or (
            source_record_id is not None
            and (
                not isinstance(source_record_id, str)
                or not source_record_id
                or len(source_record_id) > 160
            )
        )
    ):
        raise ValueError(
            "Public dataset page must use limit 1-1000 and a bounded offset."
        )
    postgres_started_at = time.perf_counter()
    query_timings_ms: dict[str, float] = {}
    query_started_at = time.perf_counter()
    with connect_db() as conn:
        dataset = conn.execute(
            """SELECT dataset.dataset_id, source.source_id, dataset.title,
                      dataset.schema_version, dataset.metadata
               FROM mcp_saas.public_datasets AS dataset
               JOIN mcp_saas.public_data_sources AS source
                 ON source.source_id = dataset.source_id
               WHERE dataset.dataset_id = %s AND source.access_status = 'available'""",
            (dataset_id,),
        ).fetchone()
        query_timings_ms["dataset_lookup"] = (
            time.perf_counter() - query_started_at
        ) * 1000
        if dataset is None:
            print(
                "data_api_pg_timing operation=public_dataset_records "
                f"correlation_id={correlation_id or 'unavailable'} "
                "outcome=not_found "
                f"dataset_lookup_ms={query_timings_ms['dataset_lookup']:.1f} "
                f"db_total_ms={(time.perf_counter() - postgres_started_at) * 1000:.1f}",
                flush=True,
            )
            return None
        record_filter = " AND source_record_id = %s" if source_record_id else ""
        query_started_at = time.perf_counter()
        rows = conn.execute(
            """SELECT source_record_id, record_type, canonical_attributes,
                      semantic_context, source_metadata, source_url, content_hash,
                      schema_version, observed_at
               FROM mcp_saas.active_public_dataset_records
               WHERE dataset_id = %s"""
            + record_filter
            + " ORDER BY source_record_id LIMIT %s OFFSET %s",
            (dataset_id, source_record_id, limit, offset)
            if source_record_id
            else (dataset_id, limit, offset),
        ).fetchall()
        query_timings_ms["records_page"] = (
            time.perf_counter() - query_started_at
        ) * 1000
        query_started_at = time.perf_counter()
        total_count = conn.execute(
            """SELECT count(*) FROM mcp_saas.active_public_dataset_records
               WHERE dataset_id = %s"""
            + record_filter,
            (dataset_id, source_record_id) if source_record_id else (dataset_id,),
        ).fetchone()[0]
        query_timings_ms["record_count"] = (
            time.perf_counter() - query_started_at
        ) * 1000
        query_started_at = time.perf_counter()
        latest = conn.execute(
            """SELECT run_id, status, details, started_at, completed_at,
                      fetched_count, inserted_count, updated_count, duplicate_count
               FROM mcp_saas.public_dataset_ingestion_runs
               WHERE source_id = %s AND details ->> 'dataset_id' = %s
               ORDER BY started_at DESC, run_id DESC LIMIT 1""",
            (dataset[1], dataset_id),
        ).fetchone()
        query_timings_ms["latest_ingestion"] = (
            time.perf_counter() - query_started_at
        ) * 1000
        query_started_at = time.perf_counter()
        latest_successful = conn.execute(
            """SELECT run_id, status, details, started_at, completed_at,
                      fetched_count, inserted_count, updated_count, duplicate_count
               FROM mcp_saas.public_dataset_ingestion_runs
               WHERE source_id = %s AND details ->> 'dataset_id' = %s
                 AND status = 'succeeded'
               ORDER BY started_at DESC, run_id DESC LIMIT 1""",
            (dataset[1], dataset_id),
        ).fetchone()
        query_timings_ms["latest_successful_ingestion"] = (
            time.perf_counter() - query_started_at
        ) * 1000
    print(
        "data_api_pg_timing operation=public_dataset_records outcome=ok "
        f"correlation_id={correlation_id or 'unavailable'} "
        f"rows={len(rows)} "
        f"dataset_lookup_ms={query_timings_ms['dataset_lookup']:.1f} "
        f"records_page_ms={query_timings_ms['records_page']:.1f} "
        f"record_count_ms={query_timings_ms['record_count']:.1f} "
        f"latest_ingestion_ms={query_timings_ms['latest_ingestion']:.1f} "
        "latest_successful_ingestion_ms="
        f"{query_timings_ms['latest_successful_ingestion']:.1f} "
        f"db_total_ms={(time.perf_counter() - postgres_started_at) * 1000:.1f}",
        flush=True,
    )
    records = [
        {
            "source_record_id": row[0],
            "record_type": row[1],
            "canonical_attributes": row[2],
            "semantic_context": row[3],
            "source_metadata": row[4],
            "source_url": row[5],
            "content_hash": row[6],
            "schema_version": row[7],
            "observed_at": row[8].isoformat(),
        }
        for row in rows
    ]
    latest_summary = _public_dataset_run_summary(latest)
    latest_successful_summary = _public_dataset_run_summary(latest_successful)
    dataset_metadata = dataset[4] if isinstance(dataset[4], dict) else {}
    return {
        "status": "ok",
        "dataset": {
            "dataset_id": dataset[0],
            "source_id": dataset[1],
            "title": dataset[2],
            "schema_version": dataset[3],
            "metadata": dataset[4],
        },
        "latest_ingestion": latest_summary,
        "freshness": summarize_public_dataset_freshness(
            latest_summary,
            latest_successful_summary,
            refresh_mode=dataset_metadata.get("refresh_mode"),
        ),
        "records": records,
        "pagination": {
            "limit": limit,
            "offset": offset,
            "total_count": total_count,
            "next_offset": (
                offset + len(records) if offset + len(records) < total_count else None
            ),
        },
    }


def validate_public_source_registration(payload: object) -> dict[str, object] | None:
    """Validate source/dataset metadata and active schema references without writes."""
    registration = validate_source_registration(payload)
    if registration is None:
        return None
    with connect_public_catalog_ingest_db() as conn:
        for dataset in registration["datasets"]:
            metadata = dataset["metadata"]
            attributes = _registered_dataset_attributes(
                conn,
                (
                    dataset["dataset_id"],
                    registration["source"]["source_id"],
                    dataset["title"],
                    dataset["schema_version"],
                    dataset["namespace_ids"],
                    metadata,
                ),
            )
            if attributes is None:
                return None
    return {
        "status": "valid",
        "contract_version": registration["contract_version"],
        "registration_sha256": canonical_registration_digest(registration),
        "source_id": registration["source"]["source_id"],
        "dataset_ids": [dataset["dataset_id"] for dataset in registration["datasets"]],
        "write_performed": False,
    }


def register_public_source(payload: object) -> dict[str, object] | None:
    """Idempotently register a source and datasets after schema validation."""
    registration = validate_source_registration(payload)
    if registration is None:
        return None
    digest = canonical_registration_digest(registration)
    with connect_public_catalog_ingest_db() as conn:
        return _apply_public_source_registration(conn, registration, digest)


def _apply_public_source_registration(
    conn, registration: dict[str, object], digest: str
) -> dict[str, object] | None:
    """Apply one validated registration on the caller's transaction."""
    source = registration["source"]
    outcomes: list[dict[str, object]] = []
    source_created = False
    source_updated = False
    for dataset in registration["datasets"]:
        attributes = _registered_dataset_attributes(
            conn,
            (
                dataset["dataset_id"],
                source["source_id"],
                dataset["title"],
                dataset["schema_version"],
                dataset["namespace_ids"],
                dataset["metadata"],
            ),
        )
        if attributes is None:
            return None
    existing_source = conn.execute(
        """SELECT source_name, source_url, access_status, source_context
           FROM mcp_saas.public_data_sources WHERE source_id = %s FOR UPDATE""",
        (source["source_id"],),
    ).fetchone()
    source_values = (
        source["source_name"].strip(),
        source["source_url"],
        source["access_status"],
        source["source_context"],
    )
    if (
        existing_source is None
        or (
            existing_source[0],
            existing_source[1],
            existing_source[2],
            _json_object(existing_source[3]) or {},
        )
        != source_values
    ):
        conn.execute(
            """INSERT INTO mcp_saas.public_data_sources
                 (source_id, source_name, source_url, access_status, source_context)
               VALUES (%s, %s, %s, %s, %s::jsonb)
               ON CONFLICT (source_id) DO UPDATE SET
                 source_name = EXCLUDED.source_name,
                 source_url = EXCLUDED.source_url,
                 access_status = EXCLUDED.access_status,
                 source_context = EXCLUDED.source_context,
                 updated_at = now()""",
            (
                source["source_id"],
                source_values[0],
                source_values[1],
                source_values[2],
                json.dumps(source_values[3], sort_keys=True),
            ),
        )
        source_created = existing_source is None
        source_updated = existing_source is not None
    for dataset in registration["datasets"]:
        existing = conn.execute(
            """SELECT source_id, source_dataset_id, title, landing_page,
                      schema_version, namespace_ids, metadata
               FROM mcp_saas.public_datasets WHERE dataset_id = %s""",
            (dataset["dataset_id"],),
        ).fetchone()
        if existing is not None and tuple(existing[:2]) != (
            source["source_id"],
            dataset["source_dataset_id"],
        ):
            raise ValueError("dataset_identity_conflict")
        source_identity = conn.execute(
            """SELECT dataset_id FROM mcp_saas.public_datasets
               WHERE source_id = %s AND source_dataset_id = %s""",
            (source["source_id"], dataset["source_dataset_id"]),
        ).fetchone()
        if source_identity is not None and source_identity[0] != dataset["dataset_id"]:
            raise ValueError("dataset_identity_conflict")
        dataset_values = (
            source["source_id"],
            dataset["source_dataset_id"],
            dataset["title"].strip(),
            dataset["landing_page"],
            dataset["schema_version"],
            dataset["namespace_ids"],
            dataset["metadata"],
        )
        current_values = (
            tuple(existing[:5]) + (list(existing[5]), _json_object(existing[6]) or {})
            if existing is not None
            else None
        )
        if current_values != dataset_values:
            conn.execute(
                """INSERT INTO mcp_saas.public_datasets
                     (dataset_id, source_id, source_dataset_id, title, landing_page,
                      schema_version, namespace_ids, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                   ON CONFLICT (dataset_id) DO UPDATE SET
                     title = EXCLUDED.title,
                     landing_page = EXCLUDED.landing_page,
                     schema_version = EXCLUDED.schema_version,
                     namespace_ids = EXCLUDED.namespace_ids,
                     metadata = EXCLUDED.metadata,
                     updated_at = now()""",
                (
                    dataset["dataset_id"],
                    *dataset_values[:6],
                    json.dumps(dataset_values[6], sort_keys=True),
                ),
            )
        outcomes.append(
            {
                "dataset_id": dataset["dataset_id"],
                "status": ("registered" if existing is None else "updated")
                if current_values != dataset_values
                else "unchanged",
            }
        )
    statuses = {item["status"] for item in outcomes}
    if source_created or "registered" in statuses:
        result_status = "registered"
    elif source_updated or "updated" in statuses:
        result_status = "updated"
    else:
        result_status = "unchanged"
    return {
        "status": result_status,
        "contract_version": registration["contract_version"],
        "registration_sha256": digest,
        "source_id": source["source_id"],
        "datasets": outcomes,
    }


def _public_source_registration_state(
    conn, registration: dict[str, object], *, lock_rows: bool = False
) -> dict[str, object]:
    """Read only the source and dataset rows named by a registration request."""
    source = registration["source"]
    source_row = conn.execute(
        """SELECT source_name, source_url, access_status, source_context
           FROM mcp_saas.public_data_sources WHERE source_id = %s"""
        + (" FOR UPDATE" if lock_rows else ""),
        (source["source_id"],),
    ).fetchone()
    source_state = (
        {
            "source_id": source["source_id"],
            "source_name": source_row[0],
            "source_url": source_row[1],
            "access_status": source_row[2],
            "source_context": _json_object(source_row[3]) or {},
        }
        if source_row is not None
        else None
    )
    dataset_states = []
    for requested in sorted(
        registration["datasets"], key=lambda item: item["dataset_id"]
    ):
        row = conn.execute(
            """SELECT source_id, source_dataset_id, title, landing_page,
                      schema_version, namespace_ids, metadata
               FROM mcp_saas.public_datasets WHERE dataset_id = %s"""
            + (" FOR UPDATE" if lock_rows else ""),
            (requested["dataset_id"],),
        ).fetchone()
        dataset_state = (
            {
                "dataset_id": requested["dataset_id"],
                "source_id": row[0],
                "source_dataset_id": row[1],
                "title": row[2],
                "landing_page": row[3],
                "schema_version": row[4],
                "namespace_ids": list(row[5]),
                "metadata": _json_object(row[6]) or {},
            }
            if row is not None
            else None
        )
        dataset_states.append(
            {"dataset_id": requested["dataset_id"], "record": dataset_state}
        )
    return {"source": source_state, "datasets": dataset_states}


def _registration_state_sha256(state: dict[str, object]) -> str:
    canonical = json.dumps(
        state, ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _registration_proposed_state(
    registration: dict[str, object],
) -> dict[str, object]:
    """Build the normalized metadata state that the registration writer persists."""
    source = registration["source"]
    return {
        "source": {
            "source_id": source["source_id"],
            "source_name": source["source_name"].strip(),
            "source_url": source["source_url"],
            "access_status": source["access_status"],
            "source_context": source["source_context"],
        },
        "datasets": [
            {
                "dataset_id": dataset["dataset_id"],
                "record": {
                    "dataset_id": dataset["dataset_id"],
                    "source_id": source["source_id"],
                    "source_dataset_id": dataset["source_dataset_id"],
                    "title": dataset["title"].strip(),
                    "landing_page": dataset["landing_page"],
                    "schema_version": dataset["schema_version"],
                    "namespace_ids": dataset["namespace_ids"],
                    "metadata": dataset["metadata"],
                },
            }
            for dataset in sorted(
                registration["datasets"], key=lambda item: item["dataset_id"]
            )
        ],
    }


def _public_catalog_registration_changes(
    before: dict[str, object], proposed: dict[str, object]
) -> list[dict[str, object]]:
    """Return a field-level diff containing only metadata changed by this request."""
    changes: list[dict[str, object]] = []

    def walk(
        old: object,
        new: object,
        path: str,
        *,
        old_present: bool = True,
        new_present: bool = True,
    ) -> None:
        if isinstance(old, dict) or isinstance(new, dict):
            old_values = old if isinstance(old, dict) else {}
            new_values = new if isinstance(new, dict) else {}
            for key in sorted(set(old_values) | set(new_values)):
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                walk(
                    old_values.get(key),
                    new_values.get(key),
                    f"{path}/{escaped}",
                    old_present=key in old_values,
                    new_present=key in new_values,
                )
            return
        if old_present != new_present or old != new:
            changes.append(
                {
                    "field": path,
                    "before_present": old_present,
                    "before_value": old,
                    "proposed_present": new_present,
                    "proposed_value": new,
                }
            )

    before_source = before.get("source")
    proposed_source = proposed.get("source")
    walk(
        before_source,
        proposed_source,
        "/source",
        old_present=before_source is not None,
        new_present=proposed_source is not None,
    )
    before_datasets = {
        item["dataset_id"]: item.get("record") for item in before.get("datasets", [])
    }
    proposed_datasets = {
        item["dataset_id"]: item.get("record") for item in proposed.get("datasets", [])
    }
    for dataset_id in sorted(set(before_datasets) | set(proposed_datasets)):
        old = before_datasets.get(dataset_id)
        new = proposed_datasets.get(dataset_id)
        walk(
            old,
            new,
            f"/datasets/{dataset_id}",
            old_present=old is not None,
            new_present=new is not None,
        )
    return changes


def _has_public_catalog_identity_conflict(
    conn, registration: dict[str, object]
) -> bool:
    """Check immutable upstream dataset identity before preview or apply."""
    source_id = registration["source"]["source_id"]
    for dataset in registration["datasets"]:
        by_dataset_id = conn.execute(
            """SELECT source_id, source_dataset_id FROM mcp_saas.public_datasets
               WHERE dataset_id = %s""",
            (dataset["dataset_id"],),
        ).fetchone()
        if by_dataset_id is not None and tuple(by_dataset_id) != (
            source_id,
            dataset["source_dataset_id"],
        ):
            return True
        by_source_identity = conn.execute(
            """SELECT dataset_id FROM mcp_saas.public_datasets
               WHERE source_id = %s AND source_dataset_id = %s""",
            (source_id, dataset["source_dataset_id"]),
        ).fetchone()
        if (
            by_source_identity is not None
            and by_source_identity[0] != dataset["dataset_id"]
        ):
            return True
    return False


def preview_public_source_registration(payload: object) -> dict[str, object] | None:
    """Preview a typed catalog change and return its compare-and-set digest."""
    registration = validate_source_registration(payload)
    if registration is None:
        return None
    proposed_digest = canonical_registration_digest(registration)
    with connect_public_catalog_ingest_db() as conn:
        for dataset in registration["datasets"]:
            if (
                _registered_dataset_attributes(
                    conn,
                    (
                        dataset["dataset_id"],
                        registration["source"]["source_id"],
                        dataset["title"],
                        dataset["schema_version"],
                        dataset["namespace_ids"],
                        dataset["metadata"],
                    ),
                )
                is None
            ):
                return None
        before_state = _public_source_registration_state(conn, registration)
        if _has_public_catalog_identity_conflict(conn, registration):
            raise ValueError("dataset_identity_conflict")
    proposed_state = _registration_proposed_state(registration)
    changes = _public_catalog_registration_changes(before_state, proposed_state)
    if len(json.dumps(changes, ensure_ascii=True).encode("utf-8")) > (
        MAX_PUBLIC_CATALOG_PREVIEW_BYTES
    ):
        raise ValueError("public_catalog_preview_too_large")
    return {
        "status": "preview_ready",
        "source_id": registration["source"]["source_id"],
        "dataset_ids": sorted(item["dataset_id"] for item in registration["datasets"]),
        "before_sha256": _registration_state_sha256(before_state),
        "proposed_sha256": proposed_digest,
        "changes": changes,
        "write_performed": False,
    }


def apply_governed_public_source_registration(
    payload: object,
) -> dict[str, object] | None:
    """Apply a typed source edit atomically with compare-and-set and its receipt."""
    required = {
        "registration",
        "expected_before_sha256",
        "expected_proposed_sha256",
        "transaction_id",
        "approval_reference",
        "operator_audit_context",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        return None
    registration = validate_source_registration(payload["registration"])
    expected_before = payload["expected_before_sha256"]
    expected_proposed = payload["expected_proposed_sha256"]
    transaction_id = payload["transaction_id"]
    approval_reference = payload["approval_reference"]
    try:
        audit_context = validate_operator_audit_context(
            payload["operator_audit_context"]
        )
    except ValueError:
        return None
    try:
        normalized_transaction_id = str(uuid.UUID(transaction_id))
    except (TypeError, ValueError, AttributeError):
        return None
    if (
        registration is None
        or not isinstance(expected_before, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_before) is None
        or not isinstance(expected_proposed, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_proposed) is None
        or not isinstance(transaction_id, str)
        or normalized_transaction_id != transaction_id
        or not isinstance(approval_reference, str)
        or not 1 <= len(approval_reference.strip()) <= 512
        or any(ord(char) < 32 for char in approval_reference)
        or audit_context is None
    ):
        return None
    proposed_digest = canonical_registration_digest(registration)
    if proposed_digest != expected_proposed:
        raise ValueError("public_catalog_proposal_conflict")
    source_id = registration["source"]["source_id"]
    dataset_ids = sorted(item["dataset_id"] for item in registration["datasets"])
    with connect_public_catalog_ingest_db() as conn:
        # Serialize the same source and dataset targets, including the absent-row case.
        for key in sorted(
            [
                f"source:{source_id}",
                *(f"dataset:{item}" for item in dataset_ids),
                *(
                    f"source-dataset:{source_id}:{item['source_dataset_id']}"
                    for item in registration["datasets"]
                ),
            ]
        ):
            conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
        replay = conn.execute(
            """SELECT proposed_sha256, expected_before_sha256, approval_reference,
                      receipt
               FROM mcp_saas.public_catalog_registration_events
               WHERE transaction_id = %s""",
            (transaction_id,),
        ).fetchone()
        if replay is not None:
            if tuple(replay[:3]) != (
                proposed_digest,
                expected_before,
                approval_reference,
            ):
                raise ValueError("public_catalog_transaction_conflict")
            receipt = _json_object(replay[3])
            return {**(receipt or {}), "idempotent_replay": True}
        for dataset in registration["datasets"]:
            if (
                _registered_dataset_attributes(
                    conn,
                    (
                        dataset["dataset_id"],
                        source_id,
                        dataset["title"],
                        dataset["schema_version"],
                        dataset["namespace_ids"],
                        dataset["metadata"],
                    ),
                )
                is None
            ):
                return None
        if _has_public_catalog_identity_conflict(conn, registration):
            raise ValueError("dataset_identity_conflict")
        before_state = _public_source_registration_state(
            conn, registration, lock_rows=True
        )
        before_digest = _registration_state_sha256(before_state)
        if before_digest != expected_before:
            receipt = {
                "status": "stale_before_state",
                "source_id": source_id,
                "dataset_ids": dataset_ids,
                "expected_before_sha256": expected_before,
                "before_sha256": before_digest,
                "after_sha256": before_digest,
                "proposed_sha256": proposed_digest,
                "transaction_id": transaction_id,
                "approval_reference": approval_reference,
            }
            conn.execute(
                """INSERT INTO mcp_saas.public_catalog_registration_events
                   (transaction_id, source_id, dataset_ids, expected_before_sha256,
                    before_sha256, after_sha256, proposed_sha256,
                    approval_reference, outcome, operator_audit_context, receipt)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)""",
                (
                    transaction_id,
                    source_id,
                    dataset_ids,
                    expected_before,
                    before_digest,
                    before_digest,
                    proposed_digest,
                    approval_reference.strip(),
                    receipt["status"],
                    json.dumps(audit_context, sort_keys=True),
                    json.dumps(receipt, sort_keys=True),
                ),
            )
            return receipt
        result = _apply_public_source_registration(conn, registration, proposed_digest)
        if result is None:
            return None
        after_state = _public_source_registration_state(conn, registration)
        after_digest = _registration_state_sha256(after_state)
        receipt = {
            "status": "applied",
            "source_id": source_id,
            "dataset_ids": dataset_ids,
            "registration_status": result["status"],
            "datasets": result["datasets"],
            "expected_before_sha256": expected_before,
            "before_sha256": before_digest,
            "after_sha256": after_digest,
            "proposed_sha256": proposed_digest,
            "transaction_id": transaction_id,
            "approval_reference": approval_reference.strip(),
        }
        conn.execute(
            """INSERT INTO mcp_saas.public_catalog_registration_events
               (transaction_id, source_id, dataset_ids, expected_before_sha256,
                before_sha256, after_sha256, proposed_sha256,
                approval_reference, outcome, operator_audit_context, receipt)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)""",
            (
                transaction_id,
                source_id,
                dataset_ids,
                expected_before,
                before_digest,
                after_digest,
                proposed_digest,
                approval_reference.strip(),
                receipt["status"],
                json.dumps(audit_context, sort_keys=True),
                json.dumps(receipt, sort_keys=True),
            ),
        )
        return receipt


def read_public_catalog_registration_receipt(
    transaction_id: str,
) -> dict[str, object] | None:
    """Read one stored metadata-edit receipt without returning source content."""
    try:
        normalized_transaction_id = str(uuid.UUID(transaction_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("invalid_transaction_id") from exc
    if normalized_transaction_id != transaction_id:
        raise ValueError("invalid_transaction_id")

    with connect_public_catalog_ingest_db() as conn:
        row = conn.execute(
            """SELECT transaction_id::text, source_id, dataset_ids,
                      expected_before_sha256, before_sha256, after_sha256,
                      proposed_sha256, approval_reference, outcome,
                      operator_audit_context, receipt, recorded_at
               FROM mcp_saas.public_catalog_registration_events
               WHERE transaction_id = %s""",
            (normalized_transaction_id,),
        ).fetchone()
    if row is None:
        return None

    expected_before = str(row[3]).strip()
    before = str(row[4]).strip()
    after = str(row[5]).strip()
    proposed = str(row[6]).strip()
    audit_context = _json_object(row[9])
    receipt = _json_object(row[10])
    if (
        not isinstance(audit_context, dict)
        or not isinstance(receipt, dict)
        or receipt.get("transaction_id") != normalized_transaction_id
        or receipt.get("status") != row[8]
        or receipt.get("source_id") != row[1]
        or receipt.get("dataset_ids") != list(row[2])
        or receipt.get("expected_before_sha256") != expected_before
        or receipt.get("before_sha256") != before
        or receipt.get("after_sha256") != after
        or receipt.get("proposed_sha256") != proposed
        or receipt.get("approval_reference") != row[7]
    ):
        raise RuntimeError("public_catalog_registration_receipt_invalid")
    safe_receipt = _json_object(row[10]) or {}
    safe_readback = {
        key: safe_receipt[key]
        for key in ("source_record_id", "readback")
        if key in safe_receipt
    }
    return {
        "status": "recorded",
        "transaction_id": normalized_transaction_id,
        "source_id": row[1],
        "dataset_ids": list(row[2]),
        "expected_before_sha256": expected_before,
        "before_sha256": before,
        "after_sha256": after,
        "proposed_sha256": proposed,
        "approval_reference": row[7],
        "outcome": row[8],
        "operator_audit_context": audit_context,
        "receipt": safe_readback,
        "recorded_at": row[11].isoformat(),
    }


_RECORD_JURISDICTION_LEVELS = {
    "federal",
    "state",
    "territory",
    "local",
    "tribal",
}


def _validated_record_jurisdiction_request(payload: object) -> dict[str, object] | None:
    required = {
        "dataset_id",
        "source_record_id",
        "proposed_jurisdiction",
        "evidence_url",
        "evidence_observed_at",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        return None
    dataset_id = payload["dataset_id"]
    record_id = payload["source_record_id"]
    jurisdiction = payload["proposed_jurisdiction"]
    evidence_url = payload["evidence_url"]
    evidence_observed_at = payload["evidence_observed_at"]
    if (
        not isinstance(dataset_id, str)
        or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", dataset_id) is None
        or not isinstance(record_id, str)
        or not 1 <= len(record_id) <= 160
        or any(ord(char) < 32 for char in record_id)
        or not isinstance(jurisdiction, dict)
        or set(jurisdiction) - {"level", "country_code", "state_code", "county_name"}
        or jurisdiction.get("level") not in _RECORD_JURISDICTION_LEVELS
        or jurisdiction.get("country_code") != "US"
        or not isinstance(evidence_url, str)
        or not is_public_https_url(evidence_url)
        or not _valid_timestamp(evidence_observed_at)
    ):
        return None
    level = jurisdiction["level"]
    state_code = jurisdiction.get("state_code")
    county_name = jurisdiction.get("county_name")
    if level in {"state", "territory", "local"}:
        if (
            not isinstance(state_code, str)
            or re.fullmatch(r"[A-Z]{2}", state_code) is None
        ):
            return None
    elif state_code is not None:
        return None
    if level == "local":
        if not isinstance(county_name, str) or not county_name.strip():
            return None
    elif county_name is not None:
        return None
    return {
        "dataset_id": dataset_id,
        "source_record_id": record_id,
        "proposed_jurisdiction": {
            "level": level,
            "country_code": "US",
            **({"state_code": state_code} if state_code else {}),
            **({"county_name": county_name.strip()} if county_name else {}),
        },
        "evidence_url": evidence_url,
        "evidence_observed_at": evidence_observed_at,
    }


def _public_dataset_record_state(
    conn, dataset_id: str, source_record_id: str, *, lock_rows: bool = False
) -> tuple[tuple[object, ...], dict[str, object]] | None:
    dataset = conn.execute(
        """SELECT source.source_id, dataset.landing_page, source.access_status,
                  dataset.schema_version, dataset.namespace_ids, dataset.metadata
           FROM mcp_saas.public_datasets AS dataset
           JOIN mcp_saas.public_data_sources AS source
             ON source.source_id = dataset.source_id
           WHERE dataset.dataset_id = %s"""
        + (" FOR UPDATE OF dataset" if lock_rows else ""),
        (dataset_id,),
    ).fetchone()
    if dataset is None or dataset[2] != "available":
        return None
    row = conn.execute(
        """SELECT source_record_id, record_type, canonical_attributes,
                  semantic_context, source_metadata, source_url, content_hash,
                  schema_version, observed_at
           FROM mcp_saas.public_dataset_records
           WHERE dataset_id = %s AND source_record_id = %s"""
        + (" FOR UPDATE" if lock_rows else ""),
        (dataset_id, source_record_id),
    ).fetchone()
    if row is None:
        return None
    record = {
        "source_record_id": row[0],
        "record_type": row[1],
        "canonical_attributes": _json_object(row[2]) or {},
        "semantic_context": _json_object(row[3]) or {},
        "source_metadata": _json_object(row[4]) or {},
        "source_url": row[5],
        "content_hash": row[6],
        "schema_version": row[7],
        "observed_at": row[8].isoformat(),
    }
    return dataset, record


def _catalog_record_sha256(record: dict[str, object]) -> str:
    canonical = json.dumps(
        record,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _preview_public_catalog_record_jurisdiction(
    conn, request: dict[str, object], *, lock_rows: bool = False
) -> dict[str, object] | None:
    dataset_id = str(request["dataset_id"])
    source_record_id = str(request["source_record_id"])
    loaded = _public_dataset_record_state(
        conn, dataset_id, source_record_id, lock_rows=lock_rows
    )
    if loaded is None:
        return None
    dataset, before_record = loaded
    if (
        dataset[3] != "public-listing-v4"
        or before_record["record_type"] != "public_listing"
        or before_record["source_metadata"].get("source_id") != dataset[0]
    ):
        return None
    attributes = _registered_dataset_attributes(conn, dataset)
    if attributes is None:
        return None
    before_attributes = before_record["canonical_attributes"]
    if not isinstance(before_attributes, dict):
        return None
    source_urls = {
        before_record["source_url"],
        before_attributes.get("listing.program_url"),
        before_attributes.get("listing.website_url"),
    }
    if request["evidence_url"] not in source_urls:
        return None
    proposed = json.loads(json.dumps(before_record))
    proposed_attributes = proposed["canonical_attributes"]
    jurisdiction = request["proposed_jurisdiction"]
    for field in ("core.geography.state_code", "core.geography.county_name"):
        proposed_attributes.pop(field, None)
    proposed_attributes["core.geography.jurisdiction_level"] = jurisdiction["level"]
    proposed_attributes["core.geography.country_code"] = jurisdiction["country_code"]
    if jurisdiction.get("state_code"):
        proposed_attributes["core.geography.state_code"] = jurisdiction["state_code"]
    if jurisdiction.get("county_name"):
        proposed_attributes["core.geography.county_name"] = jurisdiction["county_name"]
    proposed["semantic_context"]["jurisdiction"] = jurisdiction
    proposed["schema_version"] = dataset[3]
    source_metadata = proposed["source_metadata"]
    source_metadata["schema_version"] = dataset[3]
    if dataset[0] == "sam.gov.assistance_listings":
        source_metadata["transform_version"] = "sam-assistance-listing-import-v2"
    provenance = source_metadata.get("field_provenance")
    if not isinstance(provenance, dict):
        provenance = {}
    jurisdiction_fields = (
        "core.geography.jurisdiction_level",
        "core.geography.country_code",
        "core.geography.state_code",
        "core.geography.county_name",
    )
    for field in jurisdiction_fields:
        provenance.pop(field, None)
    for field in jurisdiction_fields:
        if field in proposed_attributes:
            provenance[field] = {
                "source_url": request["evidence_url"],
                "observed_at": request["evidence_observed_at"],
                "extraction_method": "operator_review",
            }
    source_metadata["field_provenance"] = provenance
    stable_source_metadata = {
        key: value for key, value in source_metadata.items() if key != "retrieved_at"
    }
    stable_content = {
        "attributes": proposed_attributes,
        "semantic_context": proposed["semantic_context"],
        "source_metadata": stable_source_metadata,
    }
    proposed["content_hash"] = hashlib.sha256(
        json.dumps(
            stable_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    if not _record_matches_dataset_schema(dataset, proposed, dataset[0], attributes):
        return None
    before_sha256 = _catalog_record_sha256(before_record)
    proposed_sha256 = _catalog_record_sha256(proposed)
    changes = [
        {
            "field": "jurisdiction",
            "before_value": before_record["semantic_context"].get("jurisdiction"),
            "proposed_value": jurisdiction,
        },
        {
            "field": "schema_version",
            "before_value": before_record["schema_version"],
            "proposed_value": proposed["schema_version"],
        },
        {
            "field": "content_hash",
            "before_value": before_record["content_hash"],
            "proposed_value": proposed["content_hash"],
        },
    ]
    return {
        "status": "preview_ready",
        "source_id": dataset[0],
        "dataset_id": dataset_id,
        "source_record_id": source_record_id,
        "before_sha256": before_sha256,
        "proposed_sha256": proposed_sha256,
        "changes": changes,
        "write_performed": False,
        "_before_record": before_record,
        "_proposed_record": proposed,
    }


def preview_public_catalog_record_jurisdiction(
    payload: object,
) -> dict[str, object] | None:
    """Preview one exact, source-backed jurisdiction metadata correction."""
    request = _validated_record_jurisdiction_request(payload)
    if request is None:
        return None
    with connect_public_catalog_ingest_db() as conn:
        preview = _preview_public_catalog_record_jurisdiction(conn, request)
    if preview is None:
        return None
    return {key: value for key, value in preview.items() if not key.startswith("_")}


def apply_governed_public_catalog_record_jurisdiction(
    payload: object,
) -> dict[str, object] | None:
    """Apply an exact-digest jurisdiction correction with an audit receipt."""
    request_fields = {
        "dataset_id",
        "source_record_id",
        "proposed_jurisdiction",
        "evidence_url",
        "evidence_observed_at",
    }
    required = request_fields | {
        "expected_before_sha256",
        "expected_proposed_sha256",
        "transaction_id",
        "approval_reference",
        "operator_audit_context",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        return None
    request = _validated_record_jurisdiction_request(
        {key: payload[key] for key in request_fields}
    )
    expected_before = payload["expected_before_sha256"]
    expected_proposed = payload["expected_proposed_sha256"]
    transaction_id = payload["transaction_id"]
    approval_reference = payload["approval_reference"]
    try:
        normalized_transaction_id = str(uuid.UUID(transaction_id))
    except (TypeError, ValueError, AttributeError):
        return None
    try:
        audit_context = validate_operator_audit_context(
            payload["operator_audit_context"]
        )
    except ValueError:
        return None
    if (
        request is None
        or not isinstance(expected_before, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_before) is None
        or not isinstance(expected_proposed, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_proposed) is None
        or not isinstance(transaction_id, str)
        or normalized_transaction_id != transaction_id
        or not isinstance(approval_reference, str)
        or not 1 <= len(approval_reference.strip()) <= 512
        or any(ord(char) < 32 for char in approval_reference)
        or audit_context is None
    ):
        return None

    dataset_id = request["dataset_id"]
    source_record_id = request["source_record_id"]
    with connect_public_catalog_ingest_db() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"catalog-record-jurisdiction:{dataset_id}:{source_record_id}",),
        )
        replay = conn.execute(
            """SELECT source_id, dataset_ids, expected_before_sha256,
                      proposed_sha256, receipt
               FROM mcp_saas.public_catalog_registration_events
               WHERE transaction_id = %s""",
            (normalized_transaction_id,),
        ).fetchone()
        if replay is not None:
            receipt = _json_object(replay[4])
            if (
                replay[1] != [dataset_id]
                or replay[2].strip() != expected_before
                or replay[3].strip() != expected_proposed
                or not isinstance(receipt, dict)
                or receipt.get("source_record_id") != source_record_id
            ):
                raise ValueError("public_catalog_metadata_transaction_conflict")
            return receipt

        preview = _preview_public_catalog_record_jurisdiction(
            conn, request, lock_rows=True
        )
        if preview is None:
            return None
        source_id = str(preview["source_id"])
        before_sha256 = str(preview["before_sha256"])
        if before_sha256 != expected_before:
            outcome = "stale_before_state"
            receipt = {
                "status": outcome,
                "operation": "record_jurisdiction_update",
                "source_id": source_id,
                "dataset_ids": [dataset_id],
                "source_record_id": source_record_id,
                "expected_before_sha256": expected_before,
                "before_sha256": before_sha256,
                "after_sha256": before_sha256,
                "proposed_sha256": expected_proposed,
                "transaction_id": normalized_transaction_id,
                "approval_reference": approval_reference.strip(),
            }
        else:
            if preview["proposed_sha256"] != expected_proposed:
                raise ValueError("public_catalog_proposal_conflict")
            published = conn.execute(
                """SELECT 1 FROM mcp_saas.program_candidate_publications
                   WHERE dataset_id = %s AND source_record_id = %s""",
                (dataset_id, source_record_id),
            ).fetchone()
            if published is not None:
                raise ValueError("published_program_record_is_immutable")
            before_record = preview["_before_record"]
            proposed_record = preview["_proposed_record"]
            updated = conn.execute(
                """UPDATE mcp_saas.public_dataset_records
                   SET canonical_attributes = %s::jsonb,
                       semantic_context = %s::jsonb,
                       source_metadata = %s::jsonb,
                       content_hash = %s,
                       schema_version = %s
                   WHERE dataset_id = %s AND source_record_id = %s
                     AND content_hash = %s""",
                (
                    json.dumps(proposed_record["canonical_attributes"], sort_keys=True),
                    json.dumps(proposed_record["semantic_context"], sort_keys=True),
                    json.dumps(proposed_record["source_metadata"], sort_keys=True),
                    proposed_record["content_hash"],
                    proposed_record["schema_version"],
                    dataset_id,
                    source_record_id,
                    before_record["content_hash"],
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("public_catalog_record_compare_and_set_conflict")
            loaded_after = _public_dataset_record_state(
                conn, dataset_id, source_record_id
            )
            if loaded_after is None:
                raise RuntimeError("public_catalog_record_readback_missing")
            _, after_record = loaded_after
            after_sha256 = _catalog_record_sha256(after_record)
            if after_sha256 != expected_proposed:
                raise RuntimeError("public_catalog_record_readback_mismatch")
            outcome = "applied"
            receipt = {
                "status": outcome,
                "operation": "record_jurisdiction_update",
                "source_id": source_id,
                "dataset_ids": [dataset_id],
                "source_record_id": source_record_id,
                "expected_before_sha256": expected_before,
                "before_sha256": before_sha256,
                "after_sha256": after_sha256,
                "proposed_sha256": expected_proposed,
                "transaction_id": normalized_transaction_id,
                "approval_reference": approval_reference.strip(),
                "readback": {
                    "schema_version": after_record["schema_version"],
                    "content_hash": after_record["content_hash"],
                    "jurisdiction": after_record["semantic_context"].get(
                        "jurisdiction"
                    ),
                    "criteria_status": after_record["source_metadata"].get(
                        "criteria_status"
                    ),
                },
            }
        conn.execute(
            """INSERT INTO mcp_saas.public_catalog_registration_events
               (transaction_id, source_id, dataset_ids, expected_before_sha256,
                before_sha256, after_sha256, proposed_sha256,
                approval_reference, outcome, operator_audit_context, receipt)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)""",
            (
                normalized_transaction_id,
                source_id,
                [dataset_id],
                expected_before,
                receipt["before_sha256"],
                receipt["after_sha256"],
                expected_proposed,
                approval_reference.strip(),
                outcome,
                json.dumps(audit_context, sort_keys=True),
                json.dumps(receipt, sort_keys=True),
            ),
        )
        return receipt


def list_public_catalog_inventory(
    *, limit: int = 25, cursor: str | None = None
) -> dict[str, object]:
    """Return a bounded, unbound source inventory with data and criteria status."""
    limit = _bounded_inventory_limit(limit)
    key = _decode_inventory_cursor(cursor, "public-catalog-v1", 2)
    where = ""
    parameters: tuple[object, ...] = (limit + 1,)
    if key is not None:
        where = "WHERE (source.source_id, dataset.dataset_id) > (%s, %s)"
        parameters = (*key, limit + 1)
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT source.source_id, source.source_name, source.source_url,
                      source.access_status, source.source_context,
                      dataset.dataset_id, dataset.source_dataset_id, dataset.title,
                      dataset.landing_page, dataset.schema_version,
                      dataset.namespace_ids, dataset.metadata,
                      (SELECT count(*) FROM
                       mcp_saas.active_public_dataset_records AS record
                       WHERE record.dataset_id = dataset.dataset_id),
                      latest.run_id, latest.status,
                      latest.details, latest.started_at, latest.completed_at,
                      latest.fetched_count, latest.inserted_count, latest.updated_count,
                      latest.duplicate_count, successful.run_id, successful.status,
                      successful.details, successful.started_at,
                      successful.completed_at,
                      successful.fetched_count, successful.inserted_count,
                      successful.updated_count, successful.duplicate_count
               FROM mcp_saas.public_data_sources AS source
               JOIN mcp_saas.public_datasets AS dataset
                 ON dataset.source_id = source.source_id
               LEFT JOIN LATERAL (
                 SELECT run_id, status, details, started_at, completed_at,
                        fetched_count, inserted_count, updated_count, duplicate_count
                 FROM mcp_saas.public_dataset_ingestion_runs
                 WHERE source_id = source.source_id
                   AND details ->> 'dataset_id' = dataset.dataset_id
                 ORDER BY started_at DESC, run_id DESC LIMIT 1
               ) AS latest ON true
               LEFT JOIN LATERAL (
                 SELECT run_id, status, details, started_at, completed_at,
                        fetched_count, inserted_count, updated_count, duplicate_count
                 FROM mcp_saas.public_dataset_ingestion_runs
                 WHERE source_id = source.source_id
                   AND details ->> 'dataset_id' = dataset.dataset_id
                   AND status = 'succeeded'
                 ORDER BY started_at DESC, run_id DESC LIMIT 1
               ) AS successful ON true
               """
            + where
            + " ORDER BY source.source_id, dataset.dataset_id "
            + "LIMIT %s",
            parameters,
        ).fetchall()
    truncated = len(rows) > limit
    rows = rows[:limit]
    items: list[dict[str, object]] = []
    for row in rows:
        metadata = _json_object(row[11]) or {}
        count = int(row[12])
        latest = _inventory_run_summary(row, 13)
        successful = _inventory_run_summary(row, 22)
        freshness = summarize_public_dataset_freshness(
            latest,
            successful,
            refresh_mode=metadata.get("refresh_mode"),
        )
        items.append(
            {
                "source": {
                    "source_id": row[0],
                    "source_name": row[1],
                    "source_url": row[2],
                    "access_status": row[3],
                    "source_context": _json_object(row[4]) or {},
                },
                "dataset": {
                    "dataset_id": row[5],
                    "source_dataset_id": row[6],
                    "title": row[7],
                    "landing_page": row[8],
                    "schema_version": row[9],
                    "namespace_ids": list(row[10]),
                    "metadata": metadata,
                },
                "data_status": (
                    "unavailable"
                    if row[3] != "available"
                    or (not count and freshness["freshness_status"] == "unavailable")
                    else "registered_no_records"
                    if not count
                    else "partial"
                    if freshness["freshness_status"] == "partial"
                    else "stale"
                    if freshness["freshness_status"] == "stale"
                    else "loaded"
                ),
                "record_count": count,
                "searchable": row[3] == "available" and count > 0,
                "criteria_coverage": metadata.get("criteria_coverage", "not_assessed"),
                "registration_status": "registered",
                "latest_ingestion": latest,
                "freshness": freshness,
                "coverage_status": (
                    "unavailable"
                    if row[3] != "available"
                    else freshness["freshness_status"]
                ),
            }
        )
    next_cursor = (
        _encode_inventory_cursor("public-catalog-v1", (rows[-1][0], rows[-1][5]))
        if truncated and rows
        else None
    )
    return {
        "status": "ok",
        "items": items,
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


def _parse_public_catalog_inventory_query(path: str) -> tuple[int, str | None]:
    """Parse bounded inventory paging parameters and reject unknown query fields."""
    return _parse_inventory_query(path)


def _inventory_run_summary(
    row: tuple[object, ...], start: int
) -> dict[str, object] | None:
    """Project safe ingestion metadata without exposing free-form run details."""
    if row[start] is None:
        return None
    details = _json_object(row[start + 2]) or {}
    safe_details = {
        key: details[key]
        for key in (
            "source_version",
            "retrieved_at",
            "transform_version",
            "validation_outcome",
        )
        if key in details
    }
    return {
        "run_id": row[start],
        "status": row[start + 1],
        "details": safe_details,
        "started_at": row[start + 3].isoformat(),
        "completed_at": row[start + 4].isoformat() if row[start + 4] else None,
        "fetched_count": row[start + 5],
        "inserted_count": row[start + 6],
        "updated_count": row[start + 7],
        "duplicate_count": row[start + 8],
        "rejected_count": details.get("rejected_count", 0),
        "rejection_reasons": details.get("rejection_reasons", []),
    }


def read_tenant_public_dataset(
    tenant_id: str,
    cluster_id: str,
    pattern_version: str,
    dataset_id: str,
    catalog_namespace: str | None = None,
) -> dict[str, object] | None:
    """Read one shared dataset under an exact tenant namespace binding."""
    with connect_db() as conn:
        bindings = conn.execute(
            """SELECT binding.binding_context, source.source_id
               FROM mcp_saas.pattern_catalog_bindings
               AS binding
               JOIN mcp_saas.public_datasets AS dataset
                 ON dataset.dataset_id = binding.dataset_id
               JOIN mcp_saas.public_data_sources AS source
                 ON source.source_id = dataset.source_id
               WHERE binding.tenant_id = %s AND binding.cluster_id = %s
                 AND binding.pattern_version = %s
                 AND binding.dataset_id = %s
                 AND (CAST(%s AS text) IS NULL OR binding.catalog_namespace = %s)
                 AND source.access_status = 'available'""",
            (
                tenant_id,
                cluster_id,
                pattern_version,
                dataset_id,
                catalog_namespace,
                catalog_namespace,
            ),
        ).fetchall()
    if catalog_namespace is None and len(bindings) > 1:
        return {"status": "namespace_required"}
    if not bindings:
        return None
    binding = bindings[0]
    if not isinstance(binding[0], dict):
        return None
    if binding[0].get("source_id") != binding[1]:
        return None
    result = read_public_dataset(dataset_id)
    if result is None:
        return None
    result["tenant_scope"] = {
        "tenant_id": tenant_id,
        "cluster_id": cluster_id,
        "pattern_version": pattern_version,
    }
    result["binding_context"] = binding[0]
    return result


def register_pattern_catalog_binding(
    binding: dict[str, object],
) -> dict[str, object] | None:
    """Persist a binding only for an active template namespace and public dataset."""
    tenant_id = str(binding["tenant_id"])
    cluster_id = str(binding["cluster_id"])
    pattern_version = str(binding["pattern_version"])
    dataset_id = str(binding["dataset_id"])
    context = binding["binding_context"]
    with connect_db() as conn:
        package = conn.execute(
            """SELECT definition FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s
                 AND lifecycle_state = 'active'""",
            (cluster_id, pattern_version),
        ).fetchone()
        if package is None:
            return None
        definition = package[0]
        if isinstance(definition, str):
            definition = json.loads(definition)
        namespaces = definition.get("catalog_namespaces", [])
        if context["catalog_namespace"] not in namespaces:
            return None
        dataset = conn.execute(
            """SELECT source.source_id
               FROM mcp_saas.public_datasets AS dataset
               JOIN mcp_saas.public_data_sources AS source
                 ON source.source_id = dataset.source_id
               WHERE dataset.dataset_id = %s
                 AND source.access_status = 'available'""",
            (dataset_id,),
        ).fetchone()
        if dataset is None or dataset[0] != context["source_id"]:
            return None
        conn.execute(
            """INSERT INTO mcp_saas.pattern_catalog_bindings
               (dataset_id, cluster_id, pattern_version, tenant_id,
                catalog_namespace, binding_context)
               VALUES (%s, %s, %s, %s, %s, %s::jsonb)
               ON CONFLICT (dataset_id, cluster_id, tenant_id,
                            (COALESCE(pattern_version, '')),
                            (COALESCE(catalog_namespace, '')))
               DO UPDATE SET binding_context = EXCLUDED.binding_context""",
            (
                dataset_id,
                cluster_id,
                pattern_version,
                tenant_id,
                context["catalog_namespace"],
                json.dumps(context, sort_keys=True, separators=(",", ":")),
            ),
        )
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "cluster_id": cluster_id,
        "pattern_version": pattern_version,
        "dataset_id": dataset_id,
        "binding_context": context,
    }


def _validated_public_dataset_ingestion(
    conn, dataset_id: str, payload: object
) -> tuple[dict[str, object], tuple[object, ...]] | None:
    """Validate one batch against a registered dataset and its active schemas."""
    validated = valid_public_catalog_ingestion(payload)
    if validated is None:
        return None
    source = conn.execute(
        """SELECT source.source_id, dataset.landing_page, source.access_status,
                  dataset.schema_version, dataset.namespace_ids, dataset.metadata
           FROM mcp_saas.public_datasets AS dataset
           JOIN mcp_saas.public_data_sources AS source
             ON source.source_id = dataset.source_id
           WHERE dataset.dataset_id = %s""",
        (dataset_id,),
    ).fetchone()
    if source is None or source[2] != "available":
        return None
    dataset_metadata = _json_object(source[5]) or {}
    if dataset_metadata.get("record_contract_version") != validated.get(
        "record_contract_version"
    ):
        return None
    records = validated["records"]
    if any(
        not is_public_https_url(record["source_url"])
        or record["source_metadata"].get("source_id") != source[0]
        or record["source_metadata"].get("source_version")
        != validated["source_version"]
        or record["source_metadata"].get("transform_version")
        != validated["transform_version"]
        or record["source_metadata"].get("retrieved_at") != validated["retrieved_at"]
        for record in records
    ):
        return None
    attributes = _registered_dataset_attributes(conn, source)
    criteria_profile_attributes = (
        _registered_criteria_profile_attributes(conn, source, attributes)
        if attributes is not None
        else None
    )
    if (
        attributes is None
        or criteria_profile_attributes is None
        or any(
            not _record_matches_dataset_schema(
                source,
                record,
                source[0],
                attributes,
                criteria_profile_attributes,
            )
            for record in records
        )
    ):
        return None
    return validated, source


def validate_public_dataset_ingestion(
    dataset_id: str, payload: object
) -> dict[str, object] | None:
    """Dry-run a bounded batch against a registered dataset without writing."""
    with connect_public_catalog_ingest_db() as conn:
        checked = _validated_public_dataset_ingestion(conn, dataset_id, payload)
    if checked is None:
        return None
    validated, source = checked
    return {
        "status": "valid",
        "dataset_id": dataset_id,
        "source_id": source[0],
        "outcome": validated["outcome"],
        "validation_outcome": validated["validation_outcome"],
        "accepted_count": len(validated["records"]),
        "rejected_count": validated.get("rejected_count", 0),
        "rejection_reasons": validated.get("rejection_reasons", []),
        "record_contract_version": validated.get("record_contract_version"),
        "write_performed": False,
    }


def ingest_public_dataset(
    dataset_id: str, payload: dict[str, object]
) -> dict[str, object] | None:
    """Record one complete source outcome, atomically preserving prior good rows."""
    run_id = str(uuid.uuid4())
    with connect_public_catalog_ingest_db() as conn:
        checked = _validated_public_dataset_ingestion(conn, dataset_id, payload)
        if checked is None:
            return None
        validated, source = checked
        records = validated["records"]
        inserted = updated = duplicate = 0
        # A partial batch is useful audit evidence, but is not a complete
        # replacement snapshot. Keep the active catalog at its last known-good
        # version until every record in a later refresh validates.
        records_to_apply = (
            [] if validated["validation_outcome"] == "partial" else records
        )
        for record in records_to_apply:
            existing = conn.execute(
                """SELECT content_hash FROM mcp_saas.public_dataset_records
                   WHERE dataset_id = %s AND source_record_id = %s""",
                (dataset_id, record["source_record_id"]),
            ).fetchone()
            if existing is None:
                inserted += 1
            elif existing[0] == record["content_hash"]:
                duplicate += 1
                continue
            else:
                updated += 1
            conn.execute(
                """INSERT INTO mcp_saas.public_dataset_records
                     (dataset_id, source_record_id, record_type, canonical_attributes,
                      semantic_context, source_metadata, source_url, content_hash,
                      schema_version, observed_at)
                   VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
                   ON CONFLICT (dataset_id, source_record_id) DO UPDATE SET
                     record_type = EXCLUDED.record_type,
                     canonical_attributes = EXCLUDED.canonical_attributes,
                     semantic_context = EXCLUDED.semantic_context,
                     source_metadata = EXCLUDED.source_metadata,
                     source_url = EXCLUDED.source_url,
                     content_hash = EXCLUDED.content_hash,
                     schema_version = EXCLUDED.schema_version,
                     observed_at = EXCLUDED.observed_at""",
                (
                    dataset_id,
                    record["source_record_id"],
                    record["record_type"],
                    json.dumps(record["canonical_attributes"]),
                    json.dumps(record["semantic_context"]),
                    json.dumps(record["source_metadata"]),
                    record["source_url"],
                    record["content_hash"],
                    record["schema_version"],
                    record["observed_at"],
                ),
            )
        details = {
            "source_version": validated["source_version"],
            "retrieved_at": validated["retrieved_at"],
            "transform_version": validated["transform_version"],
            "validation_outcome": validated["validation_outcome"],
            "record_contract_version": validated.get("record_contract_version"),
            "rejected_count": validated.get("rejected_count", 0),
            "rejection_reasons": validated.get("rejection_reasons", []),
            "detail": validated["detail"],
            "dataset_id": dataset_id,
        }
        conn.execute(
            """INSERT INTO mcp_saas.public_dataset_ingestion_runs
                 (run_id, source_id, requested_limit, fetched_count, inserted_count,
                  updated_count, duplicate_count, status, details, completed_at)
               VALUES (%s, %s, 100, %s, %s, %s, %s, %s, %s::jsonb, now())""",
            (
                run_id,
                source[0],
                len(records) + validated.get("rejected_count", 0),
                inserted,
                updated,
                duplicate,
                validated["outcome"],
                json.dumps(details),
            ),
        )
    return {
        "status": validated["outcome"],
        "validation_outcome": validated["validation_outcome"],
        "catalog_update_status": (
            "not_applied_partial"
            if validated["validation_outcome"] == "partial"
            else "applied"
        ),
        "run_id": run_id,
        "inserted_count": inserted,
        "updated_count": updated,
        "duplicate_count": duplicate,
        "rejected_count": validated.get("rejected_count", 0),
        "rejection_reasons": validated.get("rejection_reasons", []),
    }


def configured_catalog_scope() -> tuple[str, str, str] | None:
    """Return the API's fixed pattern/version/tenant scope, or fail closed."""
    cluster_id = os.environ.get("API_CATALOG_CLUSTER_ID", "").strip()
    pattern_version = os.environ.get("API_CATALOG_PATTERN_VERSION", "").strip()
    tenant_id = os.environ.get("API_CATALOG_TENANT_ID", "").strip()
    if not (
        ID_PATTERN.fullmatch(cluster_id)
        and VERSION_PATTERN.fullmatch(pattern_version)
        and ID_PATTERN.fullmatch(tenant_id)
    ):
        return None
    return cluster_id, pattern_version, tenant_id


def read_pattern_catalog(scope: tuple[str, str, str]) -> dict[str, object]:
    """Read only records bound to this API instance's configured scope."""
    cluster_id, pattern_version, tenant_id = scope
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT record.dataset_id, record.source_record_id, record.record_type,
                      record.schema_version, record.source_url, record.observed_at
               FROM mcp_saas.pattern_catalog_bindings AS binding
               JOIN mcp_saas.active_public_dataset_records AS record
                 ON record.dataset_id = binding.dataset_id
               WHERE binding.cluster_id = %s
                 AND binding.pattern_version = %s
                 AND binding.tenant_id = %s
               ORDER BY record.dataset_id, record.source_record_id
               LIMIT 10""",
            (cluster_id, pattern_version, tenant_id),
        ).fetchall()
    records = [
        {
            "dataset_id": row[0],
            "source_record_id": row[1],
            "record_type": row[2],
            "schema_version": row[3],
            "source_url": row[4],
            "observed_at": row[5].isoformat(),
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "scope": {
            "cluster_id": cluster_id,
            "pattern_version": pattern_version,
            "tenant_id": tenant_id,
        },
        "records": records,
    }


def read_pattern_cluster_bindings(
    cluster_id: str, catalog_namespace: str
) -> dict[str, object]:
    """Project active tenant bindings and unbound shared inventory datasets."""
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT binding.tenant_id, binding.dataset_id,
                      binding.pattern_version,
                      binding.binding_context, dataset.source_id, dataset.title,
                      dataset.schema_version
               FROM mcp_saas.pattern_catalog_bindings AS binding
               JOIN mcp_saas.tenant_registry AS tenant
                 ON tenant.tenant_id = binding.tenant_id
                AND tenant.pattern_id = binding.cluster_id
                AND tenant.pattern_version = binding.pattern_version
               JOIN mcp_saas.pattern_template_packages AS package
                 ON package.template_id = binding.cluster_id
                AND package.template_version = binding.pattern_version
               JOIN mcp_saas.public_datasets AS dataset
                 ON dataset.dataset_id = binding.dataset_id
               JOIN mcp_saas.public_data_sources AS source
                 ON source.source_id = dataset.source_id
               WHERE binding.cluster_id = %s
                 AND binding.catalog_namespace = %s
                 AND tenant.lifecycle_state = 'active'
                 AND package.lifecycle_state = 'active'
                 AND source.access_status = 'available'
               ORDER BY binding.dataset_id, binding.tenant_id
               LIMIT 101""",
            (cluster_id, catalog_namespace),
        ).fetchall()
        inventory_rows = conn.execute(
            """SELECT dataset.dataset_id, dataset.source_id, dataset.title,
                      dataset.schema_version, dataset.metadata
               FROM mcp_saas.public_datasets AS dataset
               JOIN mcp_saas.public_data_sources AS source
                 ON source.source_id = dataset.source_id
               WHERE source.access_status = 'available'
                 AND dataset.metadata ->> 'catalog_namespace' = %s
                 AND NOT EXISTS (
                   SELECT 1
                   FROM mcp_saas.pattern_catalog_bindings AS binding
                   JOIN mcp_saas.tenant_registry AS tenant
                     ON tenant.tenant_id = binding.tenant_id
                    AND tenant.pattern_id = binding.cluster_id
                    AND tenant.pattern_version = binding.pattern_version
                   JOIN mcp_saas.pattern_template_packages AS package
                     ON package.template_id = binding.cluster_id
                    AND package.template_version = binding.pattern_version
                   WHERE binding.dataset_id = dataset.dataset_id
                     AND binding.cluster_id = %s
                     AND binding.catalog_namespace = %s
                     AND tenant.lifecycle_state = 'active'
                     AND package.lifecycle_state = 'active'
                 )
               ORDER BY dataset.dataset_id
               LIMIT 101""",
            (catalog_namespace, cluster_id, catalog_namespace),
        ).fetchall()
    truncated = len(rows) > 100
    rows = rows[:100]
    inventory_truncated = len(inventory_rows) > 100
    inventory_rows = inventory_rows[:100]
    bindings = [
        {
            "tenant_id": row[0],
            "dataset_id": row[1],
            "pattern_version": row[2],
            "binding_context": row[3] if isinstance(row[3], dict) else {},
            "source_id": row[4],
            "title": row[5],
            "schema_version": row[6],
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "cluster_id": cluster_id,
        "catalog_namespace": catalog_namespace,
        "bindings": bindings,
        "truncated": truncated,
        "inventory_datasets": [
            {
                "dataset_id": row[0],
                "source_id": row[1],
                "title": row[2],
                "schema_version": row[3],
                "metadata": row[4] if isinstance(row[4], dict) else {},
            }
            for row in inventory_rows
        ],
        "inventory_truncated": inventory_truncated,
    }


def read_active_use_case(
    use_case_id: str, use_case_version: str
) -> dict[str, object] | None:
    """Resolve a use case and its active tenant routes from the persisted registry."""
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT use_case.use_case_id, use_case.use_case_version,
                      use_case.pattern_id, use_case.pattern_version,
                      use_case.operation_id, use_case.lifecycle_state AS use_case_state,
                      pattern.definition AS pattern_definition,
                      tenant.tenant_id, tenant.pattern_id AS tenant_pattern_id,
                      tenant.pattern_version AS tenant_pattern_version,
                      tenant.mcp_endpoint
               FROM mcp_saas.use_case_registry AS use_case
               JOIN mcp_saas.pattern_templates AS pattern
                 ON pattern.pattern_id = use_case.pattern_id
                AND pattern.pattern_version = use_case.pattern_version
               LEFT JOIN mcp_saas.use_case_tenants AS eligible
                 ON eligible.use_case_id = use_case.use_case_id
                AND eligible.use_case_version = use_case.use_case_version
               LEFT JOIN mcp_saas.tenant_registry AS tenant
                 ON tenant.tenant_id = eligible.tenant_id
                AND tenant.pattern_id = eligible.pattern_id
                AND tenant.pattern_version = eligible.pattern_version
                AND tenant.lifecycle_state = 'active'
               WHERE use_case.use_case_id = %s
                 AND use_case.use_case_version = %s
                 AND use_case.lifecycle_state = 'active'
                 AND pattern.lifecycle_state = 'active'
               ORDER BY tenant.tenant_id""",
            (use_case_id, use_case_version),
        ).fetchall()
    if not rows:
        return None
    first = rows[0]
    return {
        "use_case": {
            "use_case_id": first[0],
            "use_case_version": first[1],
            "pattern_id": first[2],
            "pattern_version": first[3],
            "operation_id": first[4],
            "lifecycle_state": first[5],
            "tenant_ids": [row[7] for row in rows if row[7]],
        },
        "pattern": first[6],
        "tenants": [
            {
                "tenant_id": row[7],
                "pattern_id": row[8],
                "pattern_version": row[9],
                "endpoint": row[10],
            }
            for row in rows
            if row[7]
        ],
    }


def read_core_route(route_id: str, operation_id: str) -> dict[str, str] | None:
    """Resolve one current active route; its version stays internal to Core."""
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT route_id, route_version, operation_id, endpoint,
                      mcp_tool_name, protocol, template_id, tenant_id
               FROM mcp_saas.core_route_metadata
               WHERE route_id = %s AND operation_id = %s
                 AND lifecycle_state = 'active'
               LIMIT 2""",
            (route_id, operation_id),
        ).fetchall()
    if len(rows) > 1:
        raise RuntimeError("Core route lookup returned ambiguous metadata")
    if not rows:
        return None
    row = rows[0]
    return {
        "route_id": row[0],
        "route_version": row[1],
        "operation_id": row[2],
        "endpoint": row[3],
        "mcp_tool_name": row[4],
        "protocol": row[5],
        "template_id": row[6],
        "tenant_id": row[7],
    }


def read_core_routes(limit: int = 25, cursor: str | None = None) -> dict[str, object]:
    """Return one stable, bounded route page without private target endpoints."""
    return read_core_route_page(limit=limit, cursor=cursor)


def _encode_inventory_cursor(kind: str, key: tuple[str, ...]) -> str:
    """Encode a versioned keyset cursor without exposing a raw database key."""
    payload = json.dumps(
        {"kind": kind, "key": key}, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_inventory_cursor(
    cursor: str | None, kind: str, width: int
) -> tuple[str, ...] | None:
    """Validate an opaque cursor before using its values in parameterized SQL."""
    if cursor is None:
        return None
    if (
        not cursor
        or len(cursor) > 2048
        or re.fullmatch(r"[A-Za-z0-9_-]+", cursor) is None
    ):
        raise ValueError("invalid_inventory_cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid_inventory_cursor") from exc
    key = payload.get("key") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"kind", "key"}
        or payload.get("kind") != kind
        or not isinstance(key, list)
        or len(key) != width
        or any(not isinstance(value, str) or len(value) > 256 for value in key)
    ):
        raise ValueError("invalid_inventory_cursor")
    return tuple(key)


def _bounded_inventory_limit(limit: int) -> int:
    """Keep registry pages small and deterministic for agent clients."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("invalid_inventory_limit")
    return limit


def _parse_inventory_query(path: str) -> tuple[int, str | None]:
    """Parse the only supported bounded inventory paging parameters."""
    query = parse_qs(urlsplit(path).query, keep_blank_values=True, strict_parsing=True)
    if set(query) - {"limit", "cursor"} or any(
        len(values) != 1 for values in query.values()
    ):
        raise ValueError("invalid_inventory_page")
    raw_limit = query.get("limit", ["25"])[0]
    if re.fullmatch(r"[0-9]{1,3}", raw_limit) is None:
        raise ValueError("invalid_inventory_limit")
    limit = _bounded_inventory_limit(int(raw_limit))
    cursor = query.get("cursor", [None])[0]
    if cursor == "":
        raise ValueError("invalid_inventory_cursor")
    return limit, cursor


def read_core_route_page(
    limit: int = 25, cursor: str | None = None
) -> dict[str, object]:
    """Return one bounded page of active routes without target endpoint data."""
    limit = _bounded_inventory_limit(limit)
    key = _decode_inventory_cursor(cursor, "routes-v2", 2)
    with connect_db() as conn:
        where = "WHERE lifecycle_state = 'active'"
        parameters: tuple[object, ...] = (limit + 1,)
        if key is not None:
            where += " AND (route_id, operation_id) > (%s, %s)"
            parameters = (*key, limit + 1)
        rows = conn.execute(
            """SELECT route_id, route_version, operation_id, mcp_tool_name,
                      protocol, lifecycle_state, template_id, tenant_id
               FROM mcp_saas.core_route_metadata
            """
            + where
            + " ORDER BY route_id, operation_id LIMIT %s",
            parameters,
        ).fetchall()
    truncated = len(rows) > limit
    page = rows[:limit]
    return {
        "routes": [
            {
                "route_id": row[0],
                "operation_id": row[2],
                "mcp_tool_name": row[3],
                "protocol": row[4],
                "lifecycle_state": row[5],
                "template_id": row[6],
                "tenant_id": row[7],
            }
            for row in page
        ],
        "truncated": truncated,
        "next_cursor": (
            _encode_inventory_cursor("routes-v2", (page[-1][0], page[-1][2]))
            if truncated and page
            else None
        ),
    }


def valid_route_event(payload: object) -> dict[str, object] | None:
    """Accept only the event fields required for privacy-safe route reports."""
    fields = {
        "correlation_id",
        "route_id",
        "route_version",
        "operation_id",
        "template_id",
        "tenant_id",
        "result_status",
        "latency_ms",
        "error_class",
    }
    if not isinstance(payload, dict) or set(payload) not in (
        fields,
        fields | {"package_id"},
    ):
        return None
    try:
        correlation_id = str(uuid.UUID(payload["correlation_id"]))
    except (ValueError, TypeError, AttributeError):
        return None
    package_id = payload.get("package_id")
    if package_id is not None:
        try:
            package_id = str(uuid.UUID(package_id))
        except (ValueError, TypeError, AttributeError):
            return None
    if any(
        value is not None and not isinstance(value, str)
        for value in (
            payload["route_id"],
            payload["route_version"],
            payload["operation_id"],
            payload["template_id"],
            payload["tenant_id"],
            payload["error_class"],
        )
    ):
        return None
    if (
        (
            payload["route_id"] is not None
            and re.fullmatch(r"[a-z][a-z0-9.-]{0,128}", payload["route_id"]) is None
        )
        or (
            payload["route_version"] is not None
            and not VERSION_PATTERN.fullmatch(payload["route_version"])
        )
        or (
            payload["operation_id"] is not None
            and not OPERATION_PATTERN.fullmatch(payload["operation_id"])
        )
    ):
        return None
    route_fields = (
        payload["route_id"],
        payload["route_version"],
        payload["operation_id"],
    )
    if any(value is None for value in route_fields) != all(
        value is None for value in route_fields
    ):
        return None
    if any(
        value is not None and not ID_PATTERN.fullmatch(value)
        for value in (payload["template_id"], payload["tenant_id"])
    ):
        return None
    if payload["tenant_id"] is not None and payload["template_id"] is None:
        return None
    if payload["template_id"] is not None and any(
        value is None for value in route_fields
    ):
        return None
    if not isinstance(payload["result_status"], str) or payload[
        "result_status"
    ] not in {
        "ok",
        "unknown_route",
        "incompatible",
        "invalid_request",
        "downstream_error",
        "unavailable",
    }:
        return None
    latency = payload["latency_ms"]
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not 0 <= latency <= 600_000
    ):
        return None
    errors = {
        "registry_unavailable",
        "tenant_unavailable",
        "unknown_route",
        "incompatible_route",
        "tenant_argument_validation",
        "tenant_tool_error",
    }
    if payload["error_class"] is not None and (
        not isinstance(payload["error_class"], str)
        or payload["error_class"] not in errors
    ):
        return None
    return {
        **payload,
        "correlation_id": correlation_id,
        "latency_ms": float(latency),
        "package_id": package_id,
    }


def record_core_route_event(payload: object) -> dict[str, object] | None:
    """Persist one validated event without any free-form caller data."""
    event = valid_route_event(payload)
    if event is None:
        return None
    with connect_db() as conn:
        conn.execute(
            """INSERT INTO mcp_saas.core_route_events
               (correlation_id, route_id, route_version, operation_id, template_id,
                tenant_id, result_status, latency_ms, error_class, package_id)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                event["correlation_id"],
                event["route_id"],
                event["route_version"],
                event["operation_id"],
                event["template_id"],
                event["tenant_id"],
                event["result_status"],
                event["latency_ms"],
                event["error_class"],
                event["package_id"],
            ),
        )
    return {"status": "recorded"}


def _parse_report_bound(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        return None
    return result.astimezone(timezone.utc)


def read_core_route_report(
    from_utc: object, to_utc: object
) -> dict[str, object] | None:
    """Aggregate a bounded UTC interval; no raw route-event rows are returned."""
    start, end = _parse_report_bound(from_utc), _parse_report_bound(to_utc)
    if start is None or end is None or end <= start or end - start > timedelta(days=31):
        return None
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT route_id, route_version, operation_id, template_id, tenant_id,
                      count(*)::bigint,
                      count(*) FILTER (WHERE result_status = 'ok')::bigint,
                      count(*) FILTER (WHERE result_status = 'unavailable')::bigint,
                      count(*) FILTER (WHERE result_status = 'incompatible')::bigint,
                      count(*) FILTER (WHERE result_status = 'unknown_route')::bigint,
                      count(*) FILTER (WHERE result_status = 'invalid_request')::bigint,
                      count(*) FILTER (
                          WHERE result_status = 'downstream_error'
                      )::bigint,
                      round(avg(latency_ms)::numeric, 3),
                      round((percentile_cont(0.95) WITHIN GROUP
                             (ORDER BY latency_ms))::numeric, 3)
               FROM mcp_saas.core_route_events
               WHERE occurred_at >= %s AND occurred_at < %s
               GROUP BY route_id, route_version, operation_id, template_id, tenant_id
               ORDER BY template_id NULLS LAST, tenant_id NULLS LAST,
                        route_id NULLS LAST, operation_id NULLS LAST
               LIMIT 1001""",
            (start, end),
        ).fetchall()
        event_count = conn.execute(
            """SELECT count(*)::bigint FROM mcp_saas.core_route_events
               WHERE occurred_at >= %s AND occurred_at < %s""",
            (start, end),
        ).fetchone()[0]
    truncated = len(rows) > 1000
    groups = [
        {
            "route_id": row[0],
            "route_version": row[1],
            "operation_id": row[2],
            "template_id": row[3],
            "tenant_id": row[4],
            "request_count": row[5],
            "outcomes": {
                "ok": row[6],
                "unavailable": row[7],
                "incompatible": row[8],
                "unknown_route": row[9],
                "invalid_request": row[10],
                "downstream_error": row[11],
            },
            "mean_latency_ms": float(row[12]) if row[12] is not None else None,
            "p95_latency_ms": float(row[13]) if row[13] is not None else None,
        }
        for row in rows[:1000]
    ]
    return {
        "status": "available",
        "window": {"from_utc": start.isoformat(), "to_utc": end.isoformat()},
        "request_count": event_count,
        "groups": groups,
        "truncated": truncated,
        "usage": None,
        "cost": None,
        "measurement_status": "unknown",
        "measurement_reason": "No provider usage or cost measurement is configured.",
    }


def valid_profile_write(payload: object, *, updating: bool) -> dict[str, object] | None:
    """Accept schema-bound values; only fixture commands may name a profile ID."""
    required_fields = {
        "profile_kind",
        "schema_registry_id",
        "schema_version",
        "attributes",
        "confirmed",
    }
    allowed_fields = required_fields | {"profile_id", "command"}
    if updating:
        allowed_fields.add("expected_revision")
    if (
        not isinstance(payload, dict)
        or not required_fields <= set(payload)
        or not set(payload) <= allowed_fields
    ):
        return None
    profile_id = payload.get("profile_id")
    command_supplied = "command" in payload
    if (
        (
            profile_id is not None
            and (
                not isinstance(profile_id, str)
                or not PROFILE_ID_PATTERN.fullmatch(profile_id)
            )
        )
        or not isinstance(payload["profile_kind"], str)
        or not PROFILE_KIND_PATTERN.fullmatch(payload["profile_kind"])
        or not isinstance(payload["schema_registry_id"], str)
        or not ID_PATTERN.fullmatch(payload["schema_registry_id"])
        or not isinstance(payload["schema_version"], str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", payload["schema_version"])
        or payload["confirmed"] is not True
        or not isinstance(payload["attributes"], list)
        or not 1 <= len(payload["attributes"]) <= 64
    ):
        return None
    if updating and (
        isinstance(payload["expected_revision"], bool)
        or not isinstance(payload["expected_revision"], int)
        or payload["expected_revision"] < 1
    ):
        return None
    command = None
    if command_supplied:
        if (
            updating
            or profile_id is None
            or not profile_id.startswith("synthetic-eval-")
        ):
            return None
        try:
            fixture_sets = load_fixture_sets(EVALUATION_FIXTURE_SETS_FILE)
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        command = valid_registration_command(
            payload["command"],
            profile_id=profile_id,
            updating=updating,
            fixture_sets=fixture_sets,
        )
        if command is None:
            return None
    elif profile_id is not None:
        return None
    seen: set[str] = set()
    normalized_fields: list[dict[str, object]] = []
    for item in payload["attributes"]:
        if (
            not isinstance(item, dict)
            or set(item)
            not in (
                {"attribute_id", "value"},
                {"attribute_id", "value", "derivation"},
            )
            or not isinstance(item["attribute_id"], str)
            or not PROFILE_ATTRIBUTE_PATTERN.fullmatch(item["attribute_id"])
            or item["attribute_id"] in seen
        ):
            return None
        try:
            json.dumps(item["value"], allow_nan=False)
        except (TypeError, ValueError):
            return None
        seen.add(item["attribute_id"])
        normalized = {"attribute_id": item["attribute_id"], "value": item["value"]}
        if "derivation" in item:
            derivation = item["derivation"]
            if (
                not isinstance(derivation, dict)
                or set(derivation) != {"method_id", "method_version"}
                or not isinstance(derivation.get("method_id"), str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", derivation["method_id"])
                or not isinstance(derivation.get("method_version"), str)
                or not re.fullmatch(
                    r"[a-z0-9][a-z0-9._-]{0,63}", derivation["method_version"]
                )
            ):
                return None
            normalized["derivation"] = derivation
        normalized_fields.append(normalized)
    return {
        **({"profile_id": profile_id} if profile_id is not None else {}),
        "profile_kind": payload["profile_kind"],
        "schema_registry_id": payload["schema_registry_id"],
        "schema_version": payload["schema_version"],
        "attributes": normalized_fields,
        "confirmed": True,
        **({"command": command} if command is not None else {}),
        **({"expected_revision": payload["expected_revision"]} if updating else {}),
    }


def _active_profile_schema(
    connection, registry_id: str, schema_version: str
) -> dict[str, object] | None:
    """Read one active versioned profile schema for profile operations."""
    row = connection.execute(
        """SELECT definition FROM mcp_saas.schema_registry
           WHERE registry_id = %s AND schema_version = %s
             AND lifecycle_state = 'active'""",
        (registry_id, schema_version),
    ).fetchone()
    if row is None:
        return None
    definition = _json_object(row[0])
    if (
        definition is None
        or definition.get("registry_id") != registry_id
        or definition.get("schema_version") != schema_version
        or not isinstance(definition.get("attributes"), dict)
        or not isinstance(definition.get("dimensions"), dict)
    ):
        return None
    return definition


def read_profile_schema(
    registry_id: str, schema_version: str
) -> dict[str, object] | None:
    """Return one active schema definition for the authorized profile client."""
    if not ID_PATTERN.fullmatch(registry_id) or not re.fullmatch(
        r"[a-z0-9][a-z0-9._-]{0,63}", schema_version
    ):
        return None
    with connect_db() as connection:
        return _active_profile_schema(connection, registry_id, schema_version)


def list_active_profile_schemas(registry_id: str) -> list[dict[str, object]]:
    """Return bounded active schema definitions for mapping validation."""
    if not ID_PATTERN.fullmatch(registry_id):
        return []
    with connect_db() as connection:
        rows = connection.execute(
            """SELECT definition FROM mcp_saas.schema_registry
               WHERE registry_id = %s AND lifecycle_state = 'active'
               ORDER BY schema_version LIMIT 32""",
            (registry_id,),
        ).fetchall()
    definitions: list[dict[str, object]] = []
    for row in rows:
        definition = _json_object(row[0])
        if (
            definition is None
            or definition.get("registry_id") != registry_id
            or not isinstance(definition.get("schema_version"), str)
            or not isinstance(definition.get("attributes"), dict)
            or not isinstance(definition.get("dimensions"), dict)
        ):
            continue
        definitions.append(definition)
    return definitions


def _active_profile_attributes(
    connection, registry_id: str, schema_version: str, values: list[dict[str, object]]
) -> list[dict[str, object]] | None:
    """Validate fields against the root schema or active catalog profile schemas.

    The profile header and session reference remain bound to the root schema.
    Additional fields are accepted only when an available catalog dataset
    explicitly declares their active schema in ``criteria_profile_schema_refs``.
    """
    root = _active_profile_schema(connection, registry_id, schema_version)
    if root is None:
        return None
    root_attributes = root.get("attributes")
    if not isinstance(root_attributes, dict):
        return None
    if all(field["attribute_id"] in root_attributes for field in values):
        resolved = _resolve_profile_attributes(root, values)
        if resolved is None:
            return None
        return [
            {
                **field,
                "schema_registry_id": registry_id,
                "schema_version": schema_version,
                "schema_definition": root,
            }
            for field in resolved
        ]
    schemas: dict[tuple[str, str], dict[str, object]] = {
        (registry_id, schema_version): root
    }
    for row in connection.execute(
        """SELECT dataset.metadata
           FROM mcp_saas.public_datasets AS dataset
           JOIN mcp_saas.public_data_sources AS source
             ON source.source_id = dataset.source_id
           WHERE source.access_status = 'available'"""
    ).fetchall():
        metadata = _json_object(row[0])
        references = metadata.get("criteria_profile_schema_refs") if metadata else None
        if references is None:
            continue
        if not isinstance(references, list):
            continue
        for reference in references:
            if (
                not isinstance(reference, dict)
                or set(reference) != {"registry_id", "schema_version"}
                or not isinstance(reference.get("registry_id"), str)
                or not ID_PATTERN.fullmatch(reference["registry_id"])
                or not isinstance(reference.get("schema_version"), str)
                or not re.fullmatch(
                    r"[a-z0-9][a-z0-9._-]{0,63}", reference["schema_version"]
                )
            ):
                continue
            identity = (reference["registry_id"], reference["schema_version"])
            if identity in schemas:
                continue
            definition = _active_profile_schema(connection, *identity)
            if definition is None:
                continue
            schemas[identity] = definition

    resolved: list[dict[str, object]] = []
    for field in values:
        attribute_id = field["attribute_id"]
        matches = (
            [((registry_id, schema_version), root)]
            if isinstance(root_attributes, dict) and attribute_id in root_attributes
            else [
                (identity, definition)
                for identity, definition in schemas.items()
                if identity != (registry_id, schema_version)
                and isinstance(definition.get("attributes"), dict)
                and attribute_id in definition["attributes"]
            ]
        )
        if len(matches) != 1:
            return None
        (field_registry_id, field_schema_version), definition = matches[0]
        validated = _resolve_profile_attributes(definition, [field])
        if validated is None:
            return None
        resolved.append(
            {
                **validated[0],
                "schema_registry_id": field_registry_id,
                "schema_version": field_schema_version,
                "schema_definition": definition,
            }
        )
    return resolved


def _resolve_profile_attributes(
    definition: dict[str, object], values: list[dict[str, object]]
) -> list[dict[str, object]] | None:
    """Validate submitted values against an already loaded active schema."""
    normalized: list[dict[str, object]] = []
    for field in values:
        attribute_id = field["attribute_id"]
        attribute = definition["attributes"].get(attribute_id)
        if (
            not isinstance(attribute, dict)
            or not isinstance(attribute.get("dimension_id"), str)
            or attribute.get("dimension_id") not in definition["dimensions"]
            or not _value_matches_attribute(field["value"], attribute)
        ):
            return None
        normalized.append(
            {
                "attribute_id": attribute_id,
                "dimension_id": attribute["dimension_id"],
                "value_type": attribute["type"],
                "value": field["value"],
                "refinement_methods": attribute.get("refinement_methods", []),
                **(
                    {"derivation": field["derivation"]} if "derivation" in field else {}
                ),
            }
        )
    return normalized


def _read_synthetic_profile(connection, profile_id: str) -> dict[str, object] | None:
    """Read one synthetic profile and its namespaced field provenance."""
    row = connection.execute(
        """SELECT profile_id, profile_kind, schema_registry_id, schema_version,
                  provenance, revision, updated_at
           FROM mcp_saas.synthetic_profiles WHERE profile_id = %s""",
        (profile_id,),
    ).fetchone()
    if row is None:
        return None
    provenance = _json_object(row[4])
    if provenance is None or provenance.get("record_type") != "synthetic":
        return None
    attributes = connection.execute(
        """SELECT attribute_id, dimension_id, value_type, value, source_id,
                  schema_version, provenance, observed_at
           FROM mcp_saas.profile_attribute_values
           WHERE profile_id = %s ORDER BY attribute_id""",
        (profile_id,),
    ).fetchall()
    saved_interests = connection.execute(
        """SELECT lead_id, source_id, source_entry_id, canonical_source_url,
                  policy_manifest_id, policy_manifest_version,
                  policy_content_sha256, retrieved_at, selected, updated_at,
                  candidate_ref, candidate_id, candidate_identity_basis,
                  source_record_id, source_version, source_content_sha256
           FROM mcp_saas.profile_saved_interests
           WHERE profile_id = %s ORDER BY updated_at DESC, lead_id LIMIT 100""",
        (profile_id,),
    ).fetchall()
    return {
        "status": "available",
        "profile_id": row[0],
        "profile_kind": row[1],
        "schema_registry_id": row[2],
        "schema_version": row[3],
        "revision": row[5],
        "updated_at": row[6].isoformat() if hasattr(row[6], "isoformat") else row[6],
        "attributes": [
            {
                "attribute_id": item[0],
                "dimension_id": item[1],
                "value_type": item[2],
                "value": item[3],
                "source_id": item[4],
                "schema_version": item[5],
                "provenance": _json_object(item[6]) or {},
                "observed_at": (
                    item[7].isoformat() if hasattr(item[7], "isoformat") else item[7]
                ),
            }
            for item in attributes
        ],
        "saved_interests": [
            {
                "lead_id": item[0],
                "source_id": item[1],
                "source_entry_id": item[2],
                "canonical_source_url": item[3],
                "policy": {
                    "manifest_id": item[4],
                    "manifest_version": item[5],
                    "content_sha256": item[6].strip(),
                },
                "retrieved_at_utc": (
                    item[7].isoformat() if hasattr(item[7], "isoformat") else item[7]
                ),
                "selected": item[8],
                "updated_at_utc": (
                    item[9].isoformat() if hasattr(item[9], "isoformat") else item[9]
                ),
                "candidate_ref": item[10],
                "candidate_id": item[11],
                "candidate_identity_basis": item[12],
                "source_record_id": item[13],
                "source_version": item[14],
                "source_content_sha256": (
                    item[15].strip() if item[15] is not None else None
                ),
            }
            for item in saved_interests
        ],
    }


def _profile_reference_digest(profile_ref: str) -> str | None:
    """Hash a well-formed opaque profile reference for storage lookup."""
    if not PROFILE_REFERENCE_PATTERN.fullmatch(profile_ref):
        return None
    return hashlib.sha256(profile_ref.encode("ascii")).hexdigest()


def _profile_resume_claim_digest(claim: str) -> str | None:
    """Domain-separate a well-formed one-time bearer claim before storage."""
    if not PROFILE_RESUME_CLAIM_PATTERN.fullmatch(claim):
        return None
    return hashlib.sha256(
        b"mcp-profile-resume-claim-v1\0" + claim.encode("ascii")
    ).hexdigest()


def create_profile_resume_claim(
    profile_ref: str, profile_scope_sha256: str
) -> dict[str, object]:
    """Issue one expiring cross-session claim for a currently scoped profile."""
    reference_digest = _profile_reference_digest(profile_ref)
    if reference_digest is None or not PROFILE_SCOPE_SHA256_PATTERN.fullmatch(
        profile_scope_sha256
    ):
        return {"status": "profile_not_found_in_scope"}

    claim = secrets.token_urlsafe(32)
    claim_digest = _profile_resume_claim_digest(claim)
    assert claim_digest is not None
    with connect_db() as connection:
        # Access references are immutable; the profile row lock below serializes
        # claim lifecycle changes without requiring UPDATE on this table.
        access = connection.execute(
            """SELECT profile_id, purpose_id, expires_at <= now(), revoked_at
               FROM mcp_saas.profile_access_references
               WHERE reference_sha256 = %s AND scope_sha256 = %s""",
            (reference_digest, profile_scope_sha256),
        ).fetchone()
        if access is None or access[3] is not None or access[2]:
            return {"status": "profile_not_found_in_scope"}
        profile_id, purpose_id = str(access[0]), str(access[1])
        connection.execute(
            "SELECT profile_id FROM mcp_saas.synthetic_profiles "
            "WHERE profile_id = %s FOR UPDATE",
            (profile_id,),
        )
        profile = _read_synthetic_profile(connection, profile_id)
        if profile is None or purpose_id != (
            f"{profile['schema_registry_id']}@{profile['schema_version']}"
        ):
            return {"status": "profile_not_found_in_scope"}
        connection.execute(
            """UPDATE mcp_saas.profile_resume_claims
               SET revoked_at = now()
               WHERE profile_id = %s AND purpose_id = %s
                 AND expires_at > now() AND consumed_at IS NULL
                 AND revoked_at IS NULL""",
            (profile_id, purpose_id),
        )
        expiry = connection.execute(
            """INSERT INTO mcp_saas.profile_resume_claims
               (claim_sha256, profile_id, purpose_id)
               VALUES (%s, %s, %s) RETURNING expires_at""",
            (claim_digest, profile_id, purpose_id),
        ).fetchone()[0]
    return {
        "status": "available",
        "resume_claim": claim,
        "expires_at": expiry.isoformat(),
    }


def resume_synthetic_profile(
    claim: str, profile_scope_sha256: str
) -> dict[str, object]:
    """Consume a one-time claim and atomically issue new session ref and claim."""
    claim_digest = _profile_resume_claim_digest(claim)
    if claim_digest is None or not PROFILE_SCOPE_SHA256_PATTERN.fullmatch(
        profile_scope_sha256
    ):
        return {"status": "profile_resume_unavailable"}

    next_profile_ref = secrets.token_urlsafe(32)
    next_claim = secrets.token_urlsafe(32)
    next_claim_digest = _profile_resume_claim_digest(next_claim)
    assert next_claim_digest is not None
    with connect_db() as connection:
        initial = connection.execute(
            """SELECT profile_id FROM mcp_saas.profile_resume_claims
               WHERE claim_sha256 = %s""",
            (claim_digest,),
        ).fetchone()
        if initial is None:
            return {"status": "profile_resume_unavailable"}
        profile_id = str(initial[0])
        profile_lock = connection.execute(
            """SELECT profile_id FROM mcp_saas.synthetic_profiles
               WHERE profile_id = %s FOR UPDATE""",
            (profile_id,),
        ).fetchone()
        if profile_lock is None:
            return {"status": "profile_resume_unavailable"}
        stored = connection.execute(
            """SELECT purpose_id, expires_at <= now(), revoked_at, consumed_at
               FROM mcp_saas.profile_resume_claims
               WHERE claim_sha256 = %s AND profile_id = %s FOR UPDATE""",
            (claim_digest, profile_id),
        ).fetchone()
        if (
            stored is None
            or stored[1]
            or stored[2] is not None
            or stored[3] is not None
        ):
            return {"status": "profile_resume_unavailable"}
        purpose_id = str(stored[0])
        profile = _read_synthetic_profile(connection, profile_id)
        if profile is None or purpose_id != (
            f"{profile['schema_registry_id']}@{profile['schema_version']}"
        ):
            return {"status": "profile_resume_unavailable"}
        consumed = connection.execute(
            """UPDATE mcp_saas.profile_resume_claims
               SET consumed_at = now()
               WHERE claim_sha256 = %s AND consumed_at IS NULL
                 AND revoked_at IS NULL AND expires_at > now()
               RETURNING claim_sha256""",
            (claim_digest,),
        ).fetchone()
        if consumed is None:
            return {"status": "profile_resume_unavailable"}
        connection.execute(
            """UPDATE mcp_saas.profile_resume_claims
               SET revoked_at = now()
               WHERE profile_id = %s AND purpose_id = %s
                 AND claim_sha256 <> %s AND expires_at > now()
                 AND consumed_at IS NULL AND revoked_at IS NULL""",
            (profile_id, purpose_id, claim_digest),
        )
        reference_digest = _profile_reference_digest(next_profile_ref)
        assert reference_digest is not None
        connection.execute(
            """INSERT INTO mcp_saas.profile_access_references
               (reference_sha256, profile_id, scope_sha256, purpose_id,
                expires_at)
               VALUES (%s, %s, %s, %s, now() + interval '12 hours')""",
            (reference_digest, profile_id, profile_scope_sha256, purpose_id),
        )
        next_expiry = connection.execute(
            """INSERT INTO mcp_saas.profile_resume_claims
               (claim_sha256, profile_id, purpose_id)
               VALUES (%s, %s, %s) RETURNING expires_at""",
            (next_claim_digest, profile_id, purpose_id),
        ).fetchone()[0]
    return {
        "status": "available",
        "profile_ref": next_profile_ref,
        "resume_claim": next_claim,
        "resume_claim_expires_at": next_expiry.isoformat(),
        "schema_registry_id": profile["schema_registry_id"],
        "schema_version": profile["schema_version"],
        "revision": profile["revision"],
    }


def revoke_profile_resume_claims(
    profile_ref: str, profile_scope_sha256: str
) -> dict[str, object]:
    """Revoke active resume claims only for the currently scoped profile."""
    reference_digest = _profile_reference_digest(profile_ref)
    if reference_digest is None or not PROFILE_SCOPE_SHA256_PATTERN.fullmatch(
        profile_scope_sha256
    ):
        return {"status": "profile_not_found_in_scope"}
    with connect_db() as connection:
        access = connection.execute(
            """SELECT profile_id, purpose_id, expires_at <= now(), revoked_at
               FROM mcp_saas.profile_access_references
               WHERE reference_sha256 = %s AND scope_sha256 = %s""",
            (reference_digest, profile_scope_sha256),
        ).fetchone()
        if access is None or access[3] is not None or access[2]:
            return {"status": "profile_not_found_in_scope"}
        profile_id, purpose_id = str(access[0]), str(access[1])
        connection.execute(
            """SELECT profile_id FROM mcp_saas.synthetic_profiles
               WHERE profile_id = %s FOR UPDATE""",
            (profile_id,),
        )
        connection.execute(
            """UPDATE mcp_saas.profile_resume_claims
               SET revoked_at = now()
               WHERE profile_id = %s AND purpose_id = %s
                 AND expires_at > now() AND consumed_at IS NULL
                 AND revoked_at IS NULL""",
            (profile_id, purpose_id),
        )
    return {"status": "available", "resume_claims_revoked": True}


def _log_profile_read_reject(
    reason: str,
    correlation_id: str | None,
    reference_digest: str | None,
    scope_sha256: str | None,
    stored_scope_sha256: str | None = None,
) -> None:
    """Log why a profile read was refused, using short digest fingerprints only.

    Raw references, scopes and claims are never logged. The public response keeps
    its fixed status; this line is the internal rejection reason.
    """
    print(
        "profile_read_reject "
        f"correlation_id={correlation_id or 'unavailable'} reason={reason} "
        f"ref_fp={(reference_digest or '')[:8] or 'none'} "
        f"scope_fp={(scope_sha256 or '')[:12] or 'none'} "
        f"stored_scope_fp={(stored_scope_sha256 or '')[:12] or 'none'}",
        flush=True,
    )


def read_scoped_synthetic_profile(
    profile_ref: str,
    profile_scope_sha256: str,
    correlation_id: str | None = None,
) -> dict[str, object]:
    """Read one profile only through a live ref bound to this session scope."""
    reference_digest = _profile_reference_digest(profile_ref)
    if reference_digest is None:
        _log_profile_read_reject("reference_malformed", correlation_id, None, None)
        return {"status": "profile_not_found_in_scope"}
    if not PROFILE_SCOPE_SHA256_PATTERN.fullmatch(profile_scope_sha256):
        _log_profile_read_reject(
            "scope_malformed", correlation_id, reference_digest, None
        )
        return {"status": "profile_not_found_in_scope"}
    with connect_db() as connection:
        row = connection.execute(
            """SELECT profile_id, purpose_id, expires_at <= now(), revoked_at
               FROM mcp_saas.profile_access_references
               WHERE reference_sha256 = %s AND scope_sha256 = %s""",
            (reference_digest, profile_scope_sha256),
        ).fetchone()
        if row is None:
            other = connection.execute(
                """SELECT scope_sha256 FROM mcp_saas.profile_access_references
                   WHERE reference_sha256 = %s LIMIT 1""",
                (reference_digest,),
            ).fetchone()
            _log_profile_read_reject(
                "scope_mismatch" if other is not None else "reference_unknown",
                correlation_id,
                reference_digest,
                profile_scope_sha256,
                str(other[0]) if other is not None else None,
            )
            return {"status": "profile_not_found_in_scope"}
        if row[3] is not None or row[2]:
            _log_profile_read_reject(
                "reference_revoked" if row[3] is not None else "reference_expired",
                correlation_id,
                reference_digest,
                profile_scope_sha256,
            )
            return {"status": "profile_reference_expired"}
        profile = _read_synthetic_profile(connection, row[0])
    if profile is None:
        _log_profile_read_reject(
            "profile_missing", correlation_id, reference_digest, profile_scope_sha256
        )
        return {"status": "profile_not_found_in_scope"}
    purpose_id = f"{profile['schema_registry_id']}@{profile['schema_version']}"
    if row[1] != purpose_id:
        _log_profile_read_reject(
            "purpose_mismatch", correlation_id, reference_digest, profile_scope_sha256
        )
        return {"status": "profile_not_found_in_scope"}
    profile.pop("profile_id", None)
    return profile


def list_evaluation_fixture_members(
    fixture_set_id: str, fixture_set_version: str
) -> dict[str, object] | None:
    """List opaque allowlisted fixture identities, never profile values."""
    try:
        sets = load_fixture_sets(EVALUATION_FIXTURE_SETS_FILE)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (fixture_set_id, fixture_set_version) not in sets:
        return None
    with connect_db() as connection:
        rows = connection.execute(
            """SELECT member.fixture_id, member.use_case_id,
                      member.schema_registry_id, member.schema_version
               FROM mcp_saas.evaluation_fixture_members AS member
               JOIN mcp_saas.schema_tag_assignments AS tag
                 ON tag.entity_type = 'profile'
                AND tag.entity_id = member.profile_id
                AND tag.registry_id = 'core-tag-registry'
                AND tag.schema_version = '1.0.0'
                AND tag.tag_id = 'core.evaluation.purpose'
                AND tag.tag_value = '"synthetic_fixture"'::jsonb
               WHERE member.fixture_set_id = %s
                 AND member.fixture_set_version = %s
               ORDER BY member.fixture_id
               LIMIT 500""",
            (fixture_set_id, fixture_set_version),
        ).fetchall()
    return {
        "status": "available",
        "fixture_set_id": fixture_set_id,
        "fixture_set_version": fixture_set_version,
        "fixtures": [
            {
                "fixture_id": row[0],
                "use_case_id": row[1],
                "schema_registry_id": row[2],
                "schema_version": row[3],
            }
            for row in rows
        ],
    }


def read_evaluation_fixture_member(
    fixture_set_id: str, fixture_set_version: str, fixture_id: str
) -> dict[str, object] | None:
    """Read one explicit fixture member, returning no internal profile ID."""
    try:
        sets = load_fixture_sets(EVALUATION_FIXTURE_SETS_FILE)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if (fixture_set_id, fixture_set_version) not in sets:
        return None
    with connect_db() as connection:
        row = connection.execute(
            """SELECT member.use_case_id, member.profile_id,
                      member.schema_registry_id, member.schema_version
               FROM mcp_saas.evaluation_fixture_members AS member
               JOIN mcp_saas.schema_tag_assignments AS tag
                 ON tag.entity_type = 'profile'
                AND tag.entity_id = member.profile_id
                AND tag.registry_id = 'core-tag-registry'
                AND tag.schema_version = '1.0.0'
                AND tag.tag_id = 'core.evaluation.purpose'
                AND tag.tag_value = '"synthetic_fixture"'::jsonb
               WHERE member.fixture_set_id = %s
                 AND member.fixture_set_version = %s
                 AND member.fixture_id = %s""",
            (fixture_set_id, fixture_set_version, fixture_id),
        ).fetchone()
        if row is None:
            return None
        profile = _read_synthetic_profile(connection, row[1])
    if (
        profile is None
        or profile.get("schema_registry_id") != row[2]
        or profile.get("schema_version") != row[3]
    ):
        return None
    profile.pop("profile_id", None)
    return {
        "status": "available",
        "fixture_set_id": fixture_set_id,
        "fixture_set_version": fixture_set_version,
        "fixture_id": fixture_id,
        "use_case_id": row[0],
        "schema_registry_id": row[2],
        "schema_version": row[3],
        "profile": profile,
    }


def _write_synthetic_profile(
    payload: object,
    *,
    updating: bool,
    profile_scope_sha256: str | None = None,
    profile_ref: str | None = None,
) -> tuple[dict[str, object] | None, str]:
    """Create or revision-check one confirmed synthetic profile transactionally."""
    request = valid_profile_write(payload, updating=updating)
    if request is None:
        return None, "invalid_profile"
    attributes = request["attributes"]
    assert isinstance(attributes, list)
    schema_version = str(request["schema_version"])
    fixture_command = request.get("command")
    reference: str | None = None
    if fixture_command is not None:
        profile_id = str(request["profile_id"])
    elif updating:
        reference_digest = _profile_reference_digest(profile_ref or "")
        if (
            reference_digest is None
            or profile_scope_sha256 is None
            or not PROFILE_SCOPE_SHA256_PATTERN.fullmatch(profile_scope_sha256)
        ):
            return None, "profile_not_found_in_scope"
        profile_id = ""
    else:
        if profile_scope_sha256 is None or not PROFILE_SCOPE_SHA256_PATTERN.fullmatch(
            profile_scope_sha256
        ):
            return None, "profile_scope_required"
        profile_id = f"synthetic-{uuid.uuid4().hex}"
        reference = secrets.token_urlsafe(32)
    with connect_db() as connection:
        if updating:
            access = connection.execute(
                """SELECT profile_id, purpose_id, expires_at <= now(), revoked_at
                   FROM mcp_saas.profile_access_references
                   WHERE reference_sha256 = %s AND scope_sha256 = %s""",
                (reference_digest, profile_scope_sha256),
            ).fetchone()
            if access is None:
                return None, "profile_not_found_in_scope"
            if access[3] is not None or access[2]:
                return None, "profile_reference_expired"
            expected_purpose = (
                f"{request['schema_registry_id']}@{request['schema_version']}"
            )
            if access[1] != expected_purpose:
                return None, "profile_not_found_in_scope"
            profile_id = str(access[0])
        schema = _active_profile_schema(
            connection, str(request["schema_registry_id"]), schema_version
        )
        if schema is None:
            return None, "invalid_or_inactive_profile_schema"
        resolved = _active_profile_attributes(
            connection,
            str(request["schema_registry_id"]),
            schema_version,
            attributes,
        )
        if resolved is None:
            return None, "invalid_or_inactive_profile_schema"
        submitted_by_id = {item["attribute_id"]: item for item in attributes}
        for field in resolved:
            derivation = field.get("derivation")
            if derivation is None:
                continue
            target_id = str(field["attribute_id"])
            field_schema = field.get("schema_definition")
            field_schema = field_schema if isinstance(field_schema, dict) else schema
            target_metadata = field_schema["attributes"].get(target_id)
            methods = target_metadata.get("refinement_methods", [])
            declared = next(
                (
                    method
                    for method in methods
                    if isinstance(method, dict)
                    and method.get("method_id") == derivation["method_id"]
                    and method.get("method_version") == derivation["method_version"]
                ),
                None,
            )
            if not isinstance(declared, dict):
                return None, "profile_derivation_not_declared"
            input_id = declared.get("input_attribute_id")
            raw_input = submitted_by_id.get(input_id)
            if (
                not isinstance(input_id, str)
                or raw_input is None
                or raw_input.get("derivation") is not None
            ):
                return None, "profile_derivation_input_missing_or_derived"
            provenance = confirmed_derivation_provenance(
                field_schema,
                target_attribute_id=target_id,
                derivation=derivation,
                input_values={input_id: raw_input["value"]},
                confirmed_value=field["value"],
            )
            if provenance is None:
                return None, "profile_derivation_value_mismatch"
            field["stored_provenance"] = provenance
        fixture_tag_schema = None
        if fixture_command is not None:
            if updating:
                return None, "evaluation_fixture_create_only"
            fixture_schema_row = connection.execute(
                """SELECT definition FROM mcp_saas.schema_registry
                   WHERE registry_id = 'core-tag-registry'
                     AND schema_version = '1.0.0'
                     AND lifecycle_state = 'active'"""
            ).fetchone()
            fixture_tag_schema = (
                _json_object(fixture_schema_row[0]) if fixture_schema_row else None
            )
            tag_assignment = {
                "registry_id": "core-tag-registry",
                "schema_version": "1.0.0",
                "tag_id": "core.evaluation.purpose",
                "entity_type": "profile",
                "entity_id": profile_id,
                "context_id": (
                    "evaluation."
                    f"{fixture_command['fixture_set_id']}.v"
                    f"{fixture_command['fixture_set_version'].replace('.', '-')}"
                ),
                "value": "synthetic_fixture",
                "context": {
                    "fixture_set_id": fixture_command["fixture_set_id"],
                    "fixture_set_version": fixture_command["fixture_set_version"],
                    "use_case_id": fixture_command["use_case_id"],
                },
                "provenance": {
                    "source_id": "profile_service.evaluation_fixture_command",
                    "operation": "register_evaluation_fixture",
                },
            }
            if validate_assignment(tag_assignment, fixture_tag_schema) is None:
                return None, "evaluation_fixture_tag_schema_unavailable"
            duplicate = connection.execute(
                """SELECT 1 FROM mcp_saas.evaluation_fixture_members
                   WHERE fixture_set_id = %s AND fixture_set_version = %s
                     AND (fixture_id = %s OR profile_id = %s)""",
                (
                    fixture_command["fixture_set_id"],
                    fixture_command["fixture_set_version"],
                    fixture_command["fixture_id"],
                    profile_id,
                ),
            ).fetchone()
            if duplicate is not None:
                return {"status": "fixture_identity_conflict"}, "conflict"
        if updating:
            profile = connection.execute(
                """SELECT profile_kind, schema_version, revision, provenance,
                      schema_registry_id
                   FROM mcp_saas.synthetic_profiles
                   WHERE profile_id = %s FOR UPDATE""",
                (profile_id,),
            ).fetchone()
            if profile is None:
                return None, "profile_not_found"
            stored_provenance = _json_object(profile[3])
            if (
                stored_provenance is None
                or stored_provenance.get("record_type") != "synthetic"
            ):
                return None, "profile_scope_rejected"
            if profile[0] != request["profile_kind"]:
                return None, "profile_scope_rejected"
            if profile[1] != schema_version or profile[4] not in (
                None,
                request["schema_registry_id"],
            ):
                return None, "profile_schema_migration_required"
            expected_revision = request["expected_revision"]
            if profile[2] != expected_revision:
                return {
                    "status": "revision_conflict",
                    "current_revision": profile[2],
                }, "conflict"
            revision = profile[2] + 1
            connection.execute(
                """UPDATE mcp_saas.synthetic_profiles
                   SET schema_registry_id = COALESCE(schema_registry_id, %s),
                       revision = %s, updated_at = now()
                   WHERE profile_id = %s""",
                (request["schema_registry_id"], revision, profile_id),
            )
        else:
            revision = 1
            inserted = connection.execute(
                """INSERT INTO mcp_saas.synthetic_profiles
                   (profile_id, profile_kind, schema_registry_id, schema_version,
                    provenance, created_at, revision, updated_at)
                   VALUES (%s, %s, %s, %s, %s, now(), 1, now())
                   ON CONFLICT (profile_id) DO NOTHING
                   RETURNING profile_id""",
                (
                    profile_id,
                    request["profile_kind"],
                    request["schema_registry_id"],
                    schema_version,
                    json.dumps(
                        {
                            "record_type": "synthetic",
                            "created_via": (
                                "confirmed_profile_service_evaluation_fixture"
                                if fixture_command is not None
                                else "confirmed_profile_service"
                            ),
                        }
                    ),
                ),
            ).fetchone()
            if inserted is None:
                return {"status": "profile_exists"}, "conflict"
            if reference is not None:
                reference_digest = _profile_reference_digest(reference)
                assert reference_digest is not None and profile_scope_sha256 is not None
                connection.execute(
                    """INSERT INTO mcp_saas.profile_access_references
                       (reference_sha256, profile_id, scope_sha256, purpose_id,
                        expires_at)
                       VALUES (%s, %s, %s, %s, now() + interval '12 hours')""",
                    (
                        reference_digest,
                        profile_id,
                        profile_scope_sha256,
                        f"{request['schema_registry_id']}@{schema_version}",
                    ),
                )
        for field in resolved:
            field_provenance = field.get("stored_provenance") or {
                "origin": "user_confirmed",
                "confirmation_status": "confirmed",
                "schema_registry_id": field["schema_registry_id"],
            }
            provenance = json.dumps(field_provenance, allow_nan=False)
            source_id = (
                "profile.derived"
                if field_provenance["origin"] == "derived"
                else "profile.user_confirmed"
            )
            values = (
                profile_id,
                field["attribute_id"],
                field["dimension_id"],
                field["value_type"],
                json.dumps(field["value"], allow_nan=False),
                source_id,
                field["schema_version"],
                provenance,
            )
            connection.execute(
                """INSERT INTO mcp_saas.profile_attribute_values
                   (profile_id, attribute_id, dimension_id, value_type, value,
                    source_id, schema_version, provenance, observed_at)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, now())
                   ON CONFLICT (profile_id, attribute_id) DO UPDATE SET
                     dimension_id = EXCLUDED.dimension_id,
                     value_type = EXCLUDED.value_type,
                     value = EXCLUDED.value,
                     source_id = EXCLUDED.source_id,
                     schema_version = EXCLUDED.schema_version,
                     provenance = EXCLUDED.provenance,
                     observed_at = now()""",
                values,
            )
            connection.execute(
                """INSERT INTO mcp_saas.profile_attribute_history
                   (profile_id, attribute_id, revision, dimension_id, value_type,
                    value, source_id, schema_version, provenance, observed_at)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, now())""",
                (profile_id, field["attribute_id"], revision, *values[2:]),
            )
        if fixture_command is not None:
            connection.execute(
                """INSERT INTO mcp_saas.schema_tag_assignments
                   (entity_type, entity_id, registry_id, schema_version, tag_id,
                    context_id, tag_value, context, provenance)
                   VALUES ('profile', %s, 'core-tag-registry', '1.0.0',
                           'core.evaluation.purpose', %s, %s::jsonb, %s::jsonb,
                           %s::jsonb)""",
                (
                    profile_id,
                    tag_assignment["context_id"],
                    json.dumps(tag_assignment["value"]),
                    json.dumps(tag_assignment["context"]),
                    json.dumps(tag_assignment["provenance"]),
                ),
            )
            connection.execute(
                """INSERT INTO mcp_saas.evaluation_fixture_members
                   (fixture_set_id, fixture_set_version, fixture_id, use_case_id,
                    profile_id, schema_registry_id, schema_version)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    fixture_command["fixture_set_id"],
                    fixture_command["fixture_set_version"],
                    fixture_command["fixture_id"],
                    fixture_command["use_case_id"],
                    profile_id,
                    request["schema_registry_id"],
                    schema_version,
                ),
            )
        result = _read_synthetic_profile(connection, profile_id)
        if result is not None and fixture_command is None:
            result.pop("profile_id", None)
            if reference is not None:
                result["profile_ref"] = reference
    return result, "ok"


def create_synthetic_profile(
    payload: object, *, profile_scope_sha256: str | None = None
) -> tuple[dict[str, object] | None, str]:
    """Create only a confirmed synthetic profile with active typed fields."""
    return _write_synthetic_profile(
        payload, updating=False, profile_scope_sha256=profile_scope_sha256
    )


def update_synthetic_profile(
    payload: object, *, profile_ref: str, profile_scope_sha256: str
) -> tuple[dict[str, object] | None, str]:
    """Correct fields only when the stored profile revision matches the request."""
    return _write_synthetic_profile(
        payload,
        updating=True,
        profile_ref=profile_ref,
        profile_scope_sha256=profile_scope_sha256,
    )


def save_scoped_synthetic_profile_interest(
    payload: object, *, profile_ref: str, profile_scope_sha256: str
) -> tuple[dict[str, object] | None, str]:
    """Save one confirmed external lead under the current scoped profile revision."""
    try:
        active_policy = read_active_source_policy()
    except Exception:
        return None, "source_policy_unavailable"
    interest, validation_status = validate_saved_interest(payload, active_policy)
    if interest is None:
        return {"status": validation_status}, validation_status

    reference_digest = _profile_reference_digest(profile_ref)
    if reference_digest is None or not PROFILE_SCOPE_SHA256_PATTERN.fullmatch(
        profile_scope_sha256
    ):
        return {"status": "profile_not_found_in_scope"}, "profile_not_found_in_scope"

    with connect_db() as connection:
        access = connection.execute(
            """SELECT profile_id, purpose_id, expires_at <= now(), revoked_at
               FROM mcp_saas.profile_access_references
               WHERE reference_sha256 = %s AND scope_sha256 = %s""",
            (reference_digest, profile_scope_sha256),
        ).fetchone()
        if access is None:
            return {
                "status": "profile_not_found_in_scope"
            }, "profile_not_found_in_scope"
        if access[3] is not None or access[2]:
            return {"status": "profile_reference_expired"}, "profile_reference_expired"
        profile_id = str(access[0])
        expected_purpose = str(access[1])
        profile = connection.execute(
            """SELECT schema_registry_id, schema_version, revision, provenance
               FROM mcp_saas.synthetic_profiles WHERE profile_id = %s FOR UPDATE""",
            (profile_id,),
        ).fetchone()
        if (
            profile is None
            or expected_purpose != f"{profile[0]}@{profile[1]}"
            or _json_object(profile[3]) is None
            or _json_object(profile[3]).get("record_type") != "synthetic"
        ):
            return {
                "status": "profile_not_found_in_scope"
            }, "profile_not_found_in_scope"
        current_revision = int(profile[2])
        if current_revision != interest["expected_revision"]:
            return (
                {"status": "revision_conflict", "current_revision": current_revision},
                "revision_conflict",
            )
        next_revision = current_revision + 1
        connection.execute(
            """UPDATE mcp_saas.synthetic_profiles
               SET revision = %s, updated_at = now() WHERE profile_id = %s""",
            (next_revision, profile_id),
        )
        connection.execute(
            """INSERT INTO mcp_saas.profile_saved_interests
                 (profile_id, lead_id, source_id, source_entry_id,
                  canonical_source_url, policy_manifest_id,
                  policy_manifest_version, policy_content_sha256, retrieved_at,
                  selected, candidate_ref, candidate_id,
                  candidate_identity_basis, source_record_id, source_version,
                  source_content_sha256, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, now())
               ON CONFLICT (profile_id, lead_id) DO UPDATE SET
                 source_id = EXCLUDED.source_id,
                 source_entry_id = EXCLUDED.source_entry_id,
                 canonical_source_url = EXCLUDED.canonical_source_url,
                 policy_manifest_id = EXCLUDED.policy_manifest_id,
                 policy_manifest_version = EXCLUDED.policy_manifest_version,
                 policy_content_sha256 = EXCLUDED.policy_content_sha256,
                 retrieved_at = EXCLUDED.retrieved_at,
                 selected = EXCLUDED.selected,
                 candidate_ref = EXCLUDED.candidate_ref,
                 candidate_id = EXCLUDED.candidate_id,
                 candidate_identity_basis = EXCLUDED.candidate_identity_basis,
                 source_record_id = EXCLUDED.source_record_id,
                 source_version = EXCLUDED.source_version,
                 source_content_sha256 = EXCLUDED.source_content_sha256,
                 updated_at = now()""",
            (
                profile_id,
                interest["lead_id"],
                interest["source_id"],
                interest["source_entry_id"],
                interest["canonical_source_url"],
                interest["policy_id"],
                interest["policy_version"],
                interest["policy_content_sha256"],
                interest["retrieved_at_utc"],
                interest["selected"],
                interest["candidate_ref"],
                interest["candidate_id"],
                interest["candidate_identity_basis"],
                interest["source_record_id"],
                interest["source_version"],
                interest["source_content_sha256"],
            ),
        )
        connection.execute(
            """INSERT INTO mcp_saas.profile_saved_interest_history
                 (profile_id, lead_id, profile_revision, source_id,
                  source_entry_id, canonical_source_url, policy_manifest_id,
                  policy_manifest_version, policy_content_sha256, retrieved_at,
                  selected, authorization_scope_sha256, candidate_ref,
                  candidate_id, candidate_identity_basis, source_record_id,
                  source_version, source_content_sha256)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       %s, %s, %s, %s, %s, %s)""",
            (
                profile_id,
                interest["lead_id"],
                next_revision,
                interest["source_id"],
                interest["source_entry_id"],
                interest["canonical_source_url"],
                interest["policy_id"],
                interest["policy_version"],
                interest["policy_content_sha256"],
                interest["retrieved_at_utc"],
                interest["selected"],
                profile_scope_sha256,
                interest["candidate_ref"],
                interest["candidate_id"],
                interest["candidate_identity_basis"],
                interest["source_record_id"],
                interest["source_version"],
                interest["source_content_sha256"],
            ),
        )
        row = connection.execute(
            """SELECT lead_id, source_id, source_entry_id,
                      canonical_source_url, policy_manifest_id,
                      policy_manifest_version, policy_content_sha256,
                      retrieved_at, selected, updated_at, candidate_ref,
                      candidate_id, candidate_identity_basis, source_record_id,
                      source_version, source_content_sha256
               FROM mcp_saas.profile_saved_interests
               WHERE profile_id = %s AND lead_id = %s""",
            (profile_id, interest["lead_id"]),
        ).fetchone()
        if row is None:
            raise RuntimeError("Saved profile interest readback is missing.")
        saved_interest = {
            "lead_id": row[0],
            "source_id": row[1],
            "source_entry_id": row[2],
            "canonical_source_url": row[3],
            "policy": {
                "manifest_id": row[4],
                "manifest_version": row[5],
                "content_sha256": row[6].strip(),
            },
            "retrieved_at_utc": (
                row[7].isoformat() if hasattr(row[7], "isoformat") else row[7]
            ),
            "selected": row[8],
            "updated_at_utc": (
                row[9].isoformat() if hasattr(row[9], "isoformat") else row[9]
            ),
            "candidate_ref": row[10],
            "candidate_id": row[11],
            "candidate_identity_basis": row[12],
            "source_record_id": row[13],
            "source_version": row[14],
            "source_content_sha256": (row[15].strip() if row[15] is not None else None),
        }
    return (
        {
            "status": "available",
            "revision": next_revision,
            "saved_interest": saved_interest,
        },
        "ok",
    )


def register_template_package(payload: object) -> tuple[dict[str, object], bool]:
    """Create an immutable pending package record; exact replay is idempotent."""
    definition = validate_template_package(payload)
    identity = (definition["template_id"], definition["template_version"])
    with connect_db() as conn:
        inserted = conn.execute(
            """INSERT INTO mcp_saas.pattern_template_packages
               (template_id, template_version, definition)
               VALUES (%s, %s, %s::jsonb)
               ON CONFLICT (template_id, template_version) DO NOTHING
               RETURNING lifecycle_state, definition""",
            (*identity, json.dumps(definition, sort_keys=True, separators=(",", ":"))),
        ).fetchone()
        row = (
            inserted
            or conn.execute(
                """SELECT lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s""",
                identity,
            ).fetchone()
        )
    if row is None:
        raise RuntimeError("Template package registration could not be read back.")
    if row[1] != definition:
        raise TemplatePackageConflict(
            "Template version is already registered with different package content."
        )
    return (
        {
            "template_id": definition["template_id"],
            "template_version": definition["template_version"],
            "lifecycle_state": row[0],
            "definition": row[1],
        },
        inserted is not None,
    )


def read_template_package(
    template_id: str, template_version: str
) -> dict[str, object] | None:
    """Read a complete persisted package descriptor and current lifecycle state."""
    with connect_db() as conn:
        row = conn.execute(
            """SELECT lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s""",
            (template_id, template_version),
        ).fetchone()
    if row is None:
        return None
    return {
        "template_id": template_id,
        "template_version": template_version,
        "lifecycle_state": row[0],
        "definition": row[1],
    }


def register_source_policy_manifest(
    payload: object,
) -> tuple[dict[str, object], bool]:
    """Persist an immutable, review-pending source-policy manifest by digest."""
    manifest, digest = validate_manifest_registration(payload)
    manifest_id = str(manifest["manifest_id"])
    version = str(manifest["manifest_version"])
    candidate_id = str(manifest["candidate_id"])
    encoded = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    with connect_db() as conn:
        inserted = conn.execute(
            """INSERT INTO mcp_saas.source_policy_manifests
               (manifest_id, manifest_version, candidate_id, content_sha256, manifest)
               VALUES (%s, %s, %s, %s, %s::jsonb)
               ON CONFLICT (manifest_id, manifest_version) DO NOTHING
               RETURNING content_sha256, manifest, created_at""",
            (manifest_id, version, candidate_id, digest, encoded),
        ).fetchone()
        row = (
            inserted
            or conn.execute(
                """SELECT content_sha256, manifest, created_at
               FROM mcp_saas.source_policy_manifests
               WHERE manifest_id = %s AND manifest_version = %s""",
                (manifest_id, version),
            ).fetchone()
        )
    if row is None:
        raise RuntimeError(
            "Source policy manifest registration could not be read back."
        )
    if row[0].strip() != digest or row[1] != manifest:
        raise SourcePolicyManifestConflict(
            "Manifest identity is already registered with different content."
        )
    created_at = row[2].isoformat() if row[2] else None
    return (
        {
            "status": "stored",
            "manifest_id": manifest_id,
            "manifest_version": version,
            "candidate_id": candidate_id,
            "content_sha256": digest,
            "review_status": manifest["review_status"],
            "created_at": created_at,
            "manifest": row[1],
        },
        inserted is not None,
    )


def read_source_policy_manifest(
    manifest_id: str, manifest_version: str
) -> dict[str, object] | None:
    """Read and integrity-check one immutable source-policy manifest version."""
    with connect_db() as conn:
        row = conn.execute(
            """SELECT candidate_id, content_sha256, manifest, created_at
               FROM mcp_saas.source_policy_manifests
               WHERE manifest_id = %s AND manifest_version = %s""",
            (manifest_id, manifest_version),
        ).fetchone()
        decision_row = conn.execute(
            """SELECT decision_id, content_sha256, decision, rationale,
                      authorization_capability, decided_at, mcp_session_sha256,
                      mcp_request_sha256, mcp_client_identity_status,
                      operator_credential_label
               FROM mcp_saas.source_policy_review_decisions
               WHERE manifest_id = %s AND manifest_version = %s""",
            (manifest_id, manifest_version),
        ).fetchone()
    if row is None:
        return None
    manifest = validate_source_policy_manifest(row[2])
    digest = source_policy_manifest_sha256(manifest)
    if (
        manifest["manifest_id"] != manifest_id
        or manifest["manifest_version"] != manifest_version
        or manifest["candidate_id"] != row[0]
        or row[1].strip() != digest
    ):
        raise RuntimeError("Persisted source policy manifest failed its digest check.")
    review_decision = None
    if decision_row is not None:
        if decision_row[1].strip() != digest:
            raise RuntimeError(
                "Persisted source policy decision digest does not match."
            )
        review_decision = {
            "decision_id": str(decision_row[0]),
            "content_sha256": digest,
            "decision": decision_row[2],
            "rationale": decision_row[3],
            "authorization_capability": decision_row[4],
            "decided_at": decision_row[5].isoformat() if decision_row[5] else None,
            "operator_audit_context": _operator_audit_event_context(decision_row, 6),
        }
    return {
        "status": "available",
        "manifest_id": manifest_id,
        "manifest_version": manifest_version,
        "candidate_id": row[0],
        "content_sha256": digest,
        "review_status": manifest["review_status"],
        "created_at": row[3].isoformat() if row[3] else None,
        "manifest": manifest,
        "review_decision": review_decision,
    }


def register_program_candidate_record(
    payload: object,
) -> tuple[dict[str, object], bool]:
    """Persist a pending candidate and an immutable operator registration receipt."""
    if not isinstance(payload, dict) or set(payload) != {
        "candidate",
        "expected_content_sha256",
        "operator_audit_context",
    }:
        raise InvalidProgramCandidate("Registration fields do not match the contract.")
    try:
        audit_context = validate_operator_audit_context(
            payload["operator_audit_context"]
        )
    except ValueError as exc:
        raise InvalidProgramCandidate("Registration audit context is invalid.") from exc
    if audit_context is None:
        raise InvalidProgramCandidate("Registration audit context is required.")
    candidate, digest = validate_program_candidate_registration(
        {
            "candidate": payload["candidate"],
            "expected_content_sha256": payload["expected_content_sha256"],
        }
    )
    candidate_id = str(candidate["candidate_id"])
    encoded = json.dumps(
        candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    event_id = uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"mcp-saas-program-candidate-registration:{candidate_id}:{digest}",
    )
    changed_fields = sorted(f"/candidate/{field}" for field in candidate)
    with connect_db() as conn:
        inserted = conn.execute(
            """INSERT INTO mcp_saas.program_candidate_records
                 (candidate_id, content_sha256, candidate)
               VALUES (%s, %s, %s::jsonb)
               ON CONFLICT DO NOTHING
               RETURNING content_sha256, candidate, created_at""",
            (candidate_id, digest, encoded),
        ).fetchone()
        row = (
            inserted
            or conn.execute(
                """SELECT content_sha256, candidate, created_at
               FROM mcp_saas.program_candidate_records WHERE candidate_id = %s""",
                (candidate_id,),
            ).fetchone()
        )
        if row is not None and row[0].strip() == digest and row[1] == candidate:
            if inserted is not None:
                conn.execute(
                    """INSERT INTO mcp_saas.program_candidate_registration_events
                       (registration_event_id, candidate_id, content_sha256, action,
                        before_state_sha256, after_state_sha256, changed_fields,
                        authorization_capability, mcp_session_sha256,
                        mcp_request_sha256, mcp_client_identity_status,
                        operator_credential_label)
                       VALUES (%s, %s, %s, 'register', NULL, %s, %s::jsonb,
                               'registry_writer', %s, %s, %s, %s)""",
                    (
                        event_id,
                        candidate_id,
                        digest,
                        digest,
                        json.dumps(changed_fields),
                        audit_context["mcp_session_sha256"],
                        audit_context["mcp_request_sha256"],
                        audit_context["mcp_client_identity_status"],
                        audit_context["operator_credential_label"],
                    ),
                )
            event_row = conn.execute(
                """SELECT registration_event_id, action, before_state_sha256,
                          after_state_sha256, changed_fields,
                          authorization_capability, recorded_at,
                          mcp_session_sha256, mcp_request_sha256,
                          mcp_client_identity_status, operator_credential_label
                   FROM mcp_saas.program_candidate_registration_events
                   WHERE candidate_id = %s AND content_sha256 = %s""",
                (candidate_id, digest),
            ).fetchone()
            if inserted is not None and event_row is None:
                raise RuntimeError(
                    "Program candidate registration receipt is missing on readback."
                )
        else:
            event_row = None
    if row is None:
        raise RuntimeError("Program candidate registration readback is missing.")
    if row[0].strip() != digest or row[1] != candidate:
        raise ProgramCandidateConflict(
            "Candidate identity is already registered with different content."
        )
    registration_audit = _program_candidate_registration_audit(event_row)
    _validate_program_candidate_registration_audit(
        registration_audit, candidate, digest, required=inserted is not None
    )
    return (
        {
            "status": "registered",
            "candidate_id": candidate_id,
            "content_sha256": digest,
            "review_status": "review_required",
            "catalog_status": "not_published",
            "created_at": row[2].isoformat() if row[2] else None,
            "candidate": row[1],
            "registration_audit": registration_audit,
        },
        inserted is not None,
    )


def read_program_candidate_record(candidate_id: str) -> dict[str, object] | None:
    """Read one pending candidate and verify its stored canonical content digest."""
    with connect_db() as conn:
        row = conn.execute(
            """SELECT content_sha256, candidate, created_at
               FROM mcp_saas.program_candidate_records WHERE candidate_id = %s""",
            (candidate_id,),
        ).fetchone()
        registration_event_row = conn.execute(
            """SELECT registration_event_id, action, before_state_sha256,
                      after_state_sha256, changed_fields, authorization_capability,
                      recorded_at, mcp_session_sha256, mcp_request_sha256,
                      mcp_client_identity_status, operator_credential_label
               FROM mcp_saas.program_candidate_registration_events
               WHERE candidate_id = %s""",
            (candidate_id,),
        ).fetchone()
        decision_row = conn.execute(
            """SELECT decision_id, content_sha256, decision, rationale,
                      authorization_capability, decided_at, mcp_session_sha256,
                      mcp_request_sha256, mcp_client_identity_status,
                      operator_credential_label, transaction_id,
                      approval_reference
               FROM mcp_saas.program_candidate_review_decisions
               WHERE candidate_id = %s""",
            (candidate_id,),
        ).fetchone()
        publication_row = conn.execute(
            """SELECT publication.publication_key
               FROM mcp_saas.program_candidate_publications AS publication
               WHERE publication.candidate_id = %s
                 AND publication.candidate_content_sha256 = %s
               ORDER BY publication.created_at DESC
               LIMIT 1""",
            (candidate_id, row[0].strip() if row is not None else ""),
        ).fetchone()
    if row is None:
        return None
    candidate = validate_program_candidate(row[1])
    digest = program_candidate_sha256(candidate)
    if candidate["candidate_id"] != candidate_id or row[0].strip() != digest:
        raise RuntimeError("Persisted program candidate failed its digest check.")
    registration_audit = _program_candidate_registration_audit(registration_event_row)
    _validate_program_candidate_registration_audit(
        registration_audit, candidate, digest
    )
    review_decision = None
    if decision_row is not None:
        if decision_row[1].strip() != digest:
            raise RuntimeError("Program candidate decision digest does not match.")
        review_decision = {
            "decision_id": str(decision_row[0]),
            "content_sha256": digest,
            "decision": decision_row[2],
            "rationale": decision_row[3],
            "authorization_capability": decision_row[4],
            "decided_at": decision_row[5].isoformat() if decision_row[5] else None,
            "operator_audit_context": _operator_audit_event_context(decision_row, 6),
            "transaction_id": str(decision_row[10]),
            "approval_reference": decision_row[11],
        }
    return {
        "status": "available",
        "candidate_id": candidate_id,
        "content_sha256": digest,
        "review_status": (
            review_decision["decision"] if review_decision else "review_required"
        ),
        "catalog_status": "published" if publication_row else "not_published",
        "publication_keys": [publication_row[0].strip()] if publication_row else [],
        "created_at": row[2].isoformat() if row[2] else None,
        "candidate": candidate,
        "registration_audit": registration_audit,
        "review_decision": review_decision,
    }


def record_program_candidate_decision(
    candidate_id: str, payload: object
) -> tuple[dict[str, object], bool]:
    """Append one immutable decision for the exact registered candidate digest."""
    (
        digest,
        decision,
        rationale,
        transaction_id,
        approval_reference,
    ) = validate_program_candidate_decision(payload, candidate_id)
    audit_context = validate_operator_audit_context(
        payload.get("operator_audit_context") if isinstance(payload, dict) else None
    )
    decision_id = uuid.uuid4()
    transaction_id = (
        uuid.UUID(transaction_id)
        if transaction_id
        else uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"mcp-saas-program-candidate-decision:{candidate_id}:{digest}:{decision}:{rationale}",
        )
    )
    authorization_capability = "registry_writer"
    with connect_db() as conn:
        candidate = conn.execute(
            """SELECT content_sha256, candidate
               FROM mcp_saas.program_candidate_records WHERE candidate_id = %s""",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise ProgramCandidateNotFound("Program candidate is not registered.")
        content = validate_program_candidate(candidate[1])
        actual_digest = program_candidate_sha256(content)
        if (
            candidate[0].strip() != actual_digest
            or actual_digest != digest
            or content["candidate_id"] != candidate_id
        ):
            raise ProgramCandidateDecisionConflict(
                "Decision digest does not match the registered candidate."
            )
        inserted = conn.execute(
            """INSERT INTO mcp_saas.program_candidate_review_decisions
               (decision_id, candidate_id, content_sha256, decision, rationale,
                authorization_capability, mcp_session_sha256, mcp_request_sha256,
                mcp_client_identity_status, operator_credential_label,
                transaction_id, approval_reference)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (candidate_id) DO NOTHING
               RETURNING decision_id, content_sha256, decision, rationale,
                         authorization_capability, decided_at, mcp_session_sha256,
                         mcp_request_sha256, mcp_client_identity_status,
                         operator_credential_label, transaction_id,
                         approval_reference""",
            (
                decision_id,
                candidate_id,
                digest,
                decision,
                rationale,
                authorization_capability,
                *_operator_audit_sql_values(audit_context),
                transaction_id,
                approval_reference,
            ),
        ).fetchone()
        row = (
            inserted
            or conn.execute(
                """SELECT decision_id, content_sha256, decision, rationale,
                      authorization_capability, decided_at, mcp_session_sha256,
                      mcp_request_sha256, mcp_client_identity_status,
                      operator_credential_label, transaction_id,
                      approval_reference
               FROM mcp_saas.program_candidate_review_decisions
               WHERE candidate_id = %s""",
                (candidate_id,),
            ).fetchone()
        )
    if row is None:
        raise ProgramCandidateDecisionConflict(
            "Decision transaction ID is already bound to another candidate."
        )
    if (
        row[1].strip() != digest
        or row[2] != decision
        or row[3] != rationale
        or row[4] != authorization_capability
        or row[10] != transaction_id
        or row[11] != approval_reference
    ):
        raise ProgramCandidateDecisionConflict(
            "Candidate already has a different immutable decision."
        )
    return (
        {
            "status": "recorded",
            "decision_id": str(row[0]),
            "candidate_id": candidate_id,
            "content_sha256": digest,
            "decision": row[2],
            "rationale": row[3],
            "authorization_capability": row[4],
            "decided_at": row[5].isoformat() if row[5] else None,
            "operator_audit_context": _operator_audit_event_context(row, 6),
            "transaction_id": str(row[10]),
            "approval_reference": row[11],
            "catalog_status": "not_published",
        },
        inserted is not None,
    )


def publish_program_candidate(
    candidate_id: str, payload: object
) -> tuple[dict[str, object], bool]:
    """Project an approved candidate into an immutable shared listing record."""
    digest, dataset_id = validate_program_candidate_publication_request(
        payload, candidate_id
    )
    with connect_db() as conn:
        candidate_row = conn.execute(
            """SELECT candidate.content_sha256, candidate.candidate,
                      decision.content_sha256, decision.decision,
                      dataset.dataset_id, dataset.source_id, dataset.title,
                      dataset.schema_version, dataset.namespace_ids, dataset.metadata,
                      source.source_name, source.access_status
               FROM mcp_saas.program_candidate_records AS candidate
               JOIN mcp_saas.program_candidate_review_decisions AS decision
                 ON decision.candidate_id = candidate.candidate_id
               JOIN mcp_saas.public_datasets AS dataset
                 ON dataset.dataset_id = %s
               JOIN mcp_saas.public_data_sources AS source
                 ON source.source_id = dataset.source_id
               WHERE candidate.candidate_id = %s""",
            (dataset_id, candidate_id),
        ).fetchone()
        if candidate_row is None:
            raise ProgramCandidateNotFound(
                "Approved candidate or target dataset is missing."
            )
        candidate = validate_program_candidate(candidate_row[1])
        actual_digest = program_candidate_sha256(candidate)
        if (
            candidate_row[0].strip() != actual_digest
            or actual_digest != digest
            or candidate_row[2].strip() != actual_digest
            or candidate_row[3] != "approved"
        ):
            raise ProgramCandidatePublicationConflict(
                "Only an exactly approved candidate digest can be published.",
                reason_code="candidate_digest_or_approval_mismatch",
            )
        if candidate["source_lineage"]["source_id"] != candidate_row[5]:
            raise ProgramCandidatePublicationConflict(
                "Candidate source does not match the target dataset source.",
                reason_code="candidate_source_dataset_mismatch",
            )
        metadata = _json_object(candidate_row[9])
        if (
            candidate_row[11] != "available"
            or not isinstance(metadata, dict)
            or metadata.get("catalog_namespace") != "programs"
        ):
            raise ProgramCandidatePublicationConflict(
                "Target must be an available shared programs dataset.",
                reason_code="target_dataset_not_publishable",
            )
        dataset = {
            "dataset_id": candidate_row[4],
            "source_id": candidate_row[5],
            "title": candidate_row[6],
            "schema_version": candidate_row[7],
            "namespace_ids": candidate_row[8],
            "metadata": metadata,
        }
        dataset_contract = (
            candidate_row[4],
            candidate_row[5],
            candidate_row[6],
            candidate_row[7],
            candidate_row[8],
            metadata,
        )
        registered_attributes = _registered_dataset_attributes(conn, dataset_contract)

    policy_state = read_active_public_search_policy()
    policy_bundle = policy_state.get("policy_bundle")
    policy_reference = policy_state.get("active_reference")
    policy_entry = approved_policy_entry_for_url(
        str(candidate["source_lineage"]["source_url"]),
        policy_bundle.get("entries") if isinstance(policy_bundle, dict) else None,
        str(candidate["source_lineage"]["source_id"]),
    )
    if (
        policy_state.get("status") != "active"
        or policy_entry is None
        or not isinstance(policy_reference, dict)
    ):
        raise ProgramCandidatePublicationConflict(
            "Candidate source is outside the active digest-approved source policy.",
            reason_code="source_policy_not_active_or_approved",
        )
    publication_key, source_record_id, record = build_public_listing_record(
        candidate,
        dataset=dataset,
        policy_entry=policy_entry,
        policy_reference=policy_reference,
        policy_entries=(
            policy_bundle.get("entries") if isinstance(policy_bundle, dict) else None
        ),
    )
    if registered_attributes is None or not _record_matches_dataset_schema(
        dataset_contract, record, str(dataset["source_id"]), registered_attributes
    ):
        raise InvalidProgramCandidatePublication(
            "Projected listing does not match the active target dataset schema."
        )

    with connect_public_catalog_ingest_db() as conn:
        existing = conn.execute(
            """SELECT content_hash FROM mcp_saas.public_dataset_records
               WHERE dataset_id = %s AND source_record_id = %s""",
            (dataset_id, source_record_id),
        ).fetchone()
        if existing is None:
            conn.execute(
                """INSERT INTO mcp_saas.public_dataset_records
                   (dataset_id, source_record_id, record_type, canonical_attributes,
                    semantic_context, source_metadata, source_url, content_hash,
                    schema_version, observed_at)
                   VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                           %s, %s, %s, %s)""",
                (
                    dataset_id,
                    source_record_id,
                    record["record_type"],
                    json.dumps(record["canonical_attributes"]),
                    json.dumps(record["semantic_context"]),
                    json.dumps(record["source_metadata"]),
                    record["source_url"],
                    record["content_hash"],
                    record["schema_version"],
                    record["observed_at"],
                ),
            )
        elif existing[0] != record["content_hash"]:
            raise ProgramCandidatePublicationConflict(
                "Candidate publication record identity already has different content.",
                reason_code="existing_record_content_mismatch",
            )

    with connect_db() as conn:
        inserted = conn.execute(
            """INSERT INTO mcp_saas.program_candidate_publications
               (publication_key, candidate_id, candidate_content_sha256,
                dataset_id, source_record_id, record_content_sha256)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (dataset_id, source_record_id) DO NOTHING
               RETURNING publication_key, candidate_id,
                         candidate_content_sha256, dataset_id, source_record_id,
                         record_content_sha256, created_at""",
            (
                publication_key,
                candidate_id,
                digest,
                dataset_id,
                source_record_id,
                record["content_hash"],
            ),
        ).fetchone()
        row = (
            inserted
            or conn.execute(
                """SELECT publication_key, candidate_id, candidate_content_sha256,
                      dataset_id, source_record_id, record_content_sha256, created_at
               FROM mcp_saas.program_candidate_publications
               WHERE dataset_id = %s AND source_record_id = %s""",
                (dataset_id, source_record_id),
            ).fetchone()
        )
    if row is None or (
        row[0].strip() != publication_key
        or row[1] != candidate_id
        or row[2].strip() != digest
        or row[3] != dataset_id
        or row[4] != source_record_id
        or row[5].strip() != record["content_hash"]
    ):
        raise ProgramCandidatePublicationConflict(
            "Candidate publication readback did not match the immutable version.",
            reason_code="publication_readback_mismatch",
        )
    state = read_program_candidate_publication_state(publication_key)
    return (
        {
            "status": "published_inactive"
            if state["status"] == "inactive"
            else "published",
            "publication_key": publication_key,
            "candidate_id": candidate_id,
            "content_sha256": digest,
            "dataset_id": dataset_id,
            "source_record_id": source_record_id,
            "record_content_sha256": record["content_hash"],
            "active_reference": state["active_reference"],
        },
        inserted is not None,
    )


def _program_candidate_publication_event(row: tuple) -> dict[str, object]:
    expected = None
    if row[4] is not None:
        expected = {"candidate_id": row[4], "content_sha256": row[5].strip()}
    target = None
    if row[6] is not None:
        target = {"candidate_id": row[6], "content_sha256": row[7].strip()}
    return {
        "generation": row[0],
        "request_id": str(row[1]),
        "publication_key": row[2].strip(),
        "action": row[3],
        "expected_current": expected,
        "target": target,
        "rationale": row[8],
        "authorization_capability": row[9],
        "recorded_at": row[10].isoformat() if row[10] else None,
        "operator_audit_context": _operator_audit_event_context(row, 11),
    }


def _operator_audit_event_context(
    row: tuple, offset: int
) -> dict[str, str | None] | None:
    """Return only the fixed, privacy-safe operator correlation fields."""
    if len(row) < offset + 4:
        return None
    session_hash, request_hash, client_identity_status, credential_label = row[
        offset : offset + 4
    ]
    if credential_label is None:
        return None
    return {
        "mcp_session_sha256": session_hash.strip() if session_hash else None,
        "mcp_request_sha256": request_hash.strip() if request_hash else None,
        "mcp_client_identity_status": client_identity_status,
        "operator_credential_label": credential_label,
    }


def _program_candidate_registration_audit(
    row: tuple | None,
) -> dict[str, object] | None:
    """Expose one immutable registration receipt without candidate field values."""
    if row is None:
        return None
    recorded_at = row[6]
    return {
        "registration_event_id": str(row[0]),
        "action": row[1],
        "before_state_sha256": row[2].strip() if row[2] else None,
        "after_state_sha256": row[3].strip() if row[3] else None,
        "changed_fields": row[4],
        "authorization_capability": row[5],
        "recorded_at": recorded_at.isoformat() if recorded_at else None,
        "operator_audit_context": _operator_audit_event_context(row, 7),
    }


def _validate_program_candidate_registration_audit(
    audit: dict[str, object] | None,
    candidate: dict[str, object],
    digest: str,
    *,
    required: bool = False,
) -> None:
    """Fail closed on missing or inconsistent registration attribution."""
    if audit is None:
        if required:
            raise RuntimeError("Program candidate registration receipt is missing.")
        return
    expected_fields = sorted(f"/candidate/{field}" for field in candidate)
    try:
        audit_context = validate_operator_audit_context(
            audit.get("operator_audit_context")
        )
    except ValueError as exc:
        raise RuntimeError("Program candidate registration audit is invalid.") from exc
    if (
        audit.get("action") != "register"
        or audit.get("before_state_sha256") is not None
        or audit.get("after_state_sha256") != digest
        or audit.get("changed_fields") != expected_fields
        or audit.get("authorization_capability") != "registry_writer"
        or audit_context is None
        or not isinstance(audit.get("recorded_at"), str)
    ):
        raise RuntimeError("Program candidate registration audit does not match.")


def _operator_audit_sql_values(
    audit_context: dict[str, str | None] | None,
) -> tuple[str | None, str | None, str | None, str | None]:
    if audit_context is None:
        return (None, None, None, None)
    return (
        audit_context.get("mcp_session_sha256"),
        audit_context.get("mcp_request_sha256"),
        audit_context.get("mcp_client_identity_status"),
        audit_context.get("operator_credential_label"),
    )


def read_program_candidate_publication_state(
    publication_key: str,
) -> dict[str, object]:
    """Read active pointer, all immutable versions, and the latest audit event."""
    if not re.fullmatch(r"[0-9a-f]{64}", publication_key):
        raise ValueError("Publication identity is invalid.")
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT publication.candidate_id,
                      publication.candidate_content_sha256,
                      publication.dataset_id, publication.source_record_id,
                      publication.record_content_sha256, publication.created_at,
                      candidate.content_sha256, candidate.candidate,
                      decision.content_sha256, decision.decision,
                      record.content_hash, record.source_metadata
               FROM mcp_saas.program_candidate_publications AS publication
               JOIN mcp_saas.program_candidate_records AS candidate
                 ON candidate.candidate_id = publication.candidate_id
               LEFT JOIN mcp_saas.program_candidate_review_decisions AS decision
                 ON decision.candidate_id = candidate.candidate_id
               LEFT JOIN mcp_saas.public_dataset_records AS record
                 ON record.dataset_id = publication.dataset_id
                AND record.source_record_id = publication.source_record_id
               WHERE publication.publication_key = %s
               ORDER BY publication.created_at, publication.candidate_id""",
            (publication_key,),
        ).fetchall()
        event_row = conn.execute(
            """SELECT generation, request_id, publication_key, action,
                      expected_candidate_id, expected_candidate_content_sha256,
                      target_candidate_id, target_candidate_content_sha256,
                      rationale, authorization_capability, recorded_at,
                      mcp_session_sha256, mcp_request_sha256,
                      mcp_client_identity_status, operator_credential_label
               FROM mcp_saas.program_candidate_publication_events
               WHERE publication_key = %s
               ORDER BY generation DESC LIMIT 1""",
            (publication_key,),
        ).fetchone()
    if not rows:
        return {"status": "not_found", "publication_key": publication_key}
    event = _program_candidate_publication_event(event_row) if event_row else None
    active_reference = (
        event["target"]
        if event and event["action"] in {"activate", "rollback"}
        else None
    )
    versions: list[dict[str, object]] = []
    for row in rows:
        candidate = validate_program_candidate(row[7])
        candidate_digest = program_candidate_sha256(candidate)
        stored_candidate_digest = row[6].strip()
        publication_digest = row[1].strip()
        decision_digest = row[8].strip() if row[8] is not None else None
        if (
            candidate["candidate_id"] != row[0]
            or candidate_digest != stored_candidate_digest
            or candidate_digest != publication_digest
            or (decision_digest is not None and decision_digest != candidate_digest)
            or row[10] is None
            or row[10] != row[4].strip()
        ):
            raise RuntimeError("Program publication failed its candidate digest check.")
        source_metadata = _json_object(row[11])
        if (
            source_metadata.get("program_candidate_id") != row[0]
            or source_metadata.get("program_candidate_sha256") != publication_digest
            or source_metadata.get("program_candidate_publication_key")
            != publication_key
        ):
            raise RuntimeError("Program publication record failed its integrity check.")
        versions.append(
            {
                "candidate_id": row[0],
                "content_sha256": publication_digest,
                "review_status": (
                    row[9] if decision_digest is not None else "review_required"
                ),
                "dataset_id": row[2],
                "source_record_id": row[3],
                "record_content_sha256": row[4].strip(),
                "created_at": row[5].isoformat() if row[5] else None,
            }
        )
    if active_reference and not any(
        version["candidate_id"] == active_reference["candidate_id"]
        and version["content_sha256"] == active_reference["content_sha256"]
        and version["review_status"] == "approved"
        for version in versions
    ):
        raise RuntimeError("Active program publication is not exactly approved.")
    return {
        "status": "active" if active_reference else "inactive",
        "publication_key": publication_key,
        "active_reference": active_reference,
        "versions": versions,
        "last_event": event,
    }


def record_program_candidate_publication_lifecycle(
    publication_key: str, payload: object
) -> tuple[dict[str, object], bool]:
    """Append an exact-digest activation/deactivation/rollback event."""
    request = validate_program_candidate_publication_lifecycle(payload, publication_key)
    authorization_capability = "registry_writer"
    with connect_db() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"program-candidate-publication:{publication_key}",),
        )
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("program-candidate-publication-generation",),
        )
        event_query = """SELECT generation, request_id, publication_key, action,
                              expected_candidate_id, expected_candidate_content_sha256,
                              target_candidate_id, target_candidate_content_sha256,
                              rationale, authorization_capability, recorded_at,
                              mcp_session_sha256, mcp_request_sha256,
                              mcp_client_identity_status, operator_credential_label
                       FROM mcp_saas.program_candidate_publication_events """
        existing_row = conn.execute(
            event_query + "WHERE request_id = %s", (request["request_id"],)
        ).fetchone()
        if existing_row is not None:
            existing = _program_candidate_publication_event(existing_row)
            if existing["publication_key"] != publication_key or any(
                existing[field] != request[field]
                for field in ("action", "expected_current", "target", "rationale")
            ):
                raise ProgramCandidatePublicationConflict(
                    "Lifecycle request ID was reused with different content."
                )
            created = False
            event = existing
        else:
            current_row = conn.execute(
                event_query
                + "WHERE publication_key = %s ORDER BY generation DESC LIMIT 1",
                (publication_key,),
            ).fetchone()
            current_event = (
                _program_candidate_publication_event(current_row)
                if current_row
                else None
            )
            current_reference = (
                current_event["target"]
                if current_event and current_event["action"] in {"activate", "rollback"}
                else None
            )
            if current_reference != request["expected_current"]:
                raise ProgramCandidatePublicationConflict(
                    "Expected active publication version is stale."
                )
            target = request["target"]
            if target is not None:
                approved = conn.execute(
                    """SELECT candidate.content_sha256, candidate.candidate,
                              decision.content_sha256, decision.decision
                       FROM mcp_saas.program_candidate_publications AS publication
                       JOIN mcp_saas.program_candidate_records AS candidate
                         ON candidate.candidate_id = publication.candidate_id
                       LEFT JOIN mcp_saas.program_candidate_review_decisions AS decision
                         ON decision.candidate_id = candidate.candidate_id
                       WHERE publication.publication_key = %s
                         AND publication.candidate_id = %s
                         AND publication.candidate_content_sha256 = %s""",
                    (publication_key, target["candidate_id"], target["content_sha256"]),
                ).fetchone()
                if approved is None:
                    raise ProgramCandidatePublicationConflict(
                        "Target publication version is not registered."
                    )
                candidate = validate_program_candidate(approved[1])
                actual_digest = program_candidate_sha256(candidate)
                if (
                    approved[0].strip() != actual_digest
                    or approved[2] is None
                    or approved[2].strip() != actual_digest
                    or approved[3] != "approved"
                    or actual_digest != target["content_sha256"]
                ):
                    raise ProgramCandidatePublicationConflict(
                        "Only an exactly approved candidate digest can be active."
                    )
                if request["action"] == "rollback":
                    previous = conn.execute(
                        """SELECT 1
                           FROM mcp_saas.program_candidate_publication_events
                           WHERE publication_key = %s
                             AND action IN ('activate', 'rollback')
                             AND target_candidate_id = %s
                             AND target_candidate_content_sha256 = %s
                           LIMIT 1""",
                        (
                            publication_key,
                            target["candidate_id"],
                            target["content_sha256"],
                        ),
                    ).fetchone()
                    if previous is None:
                        raise ProgramCandidatePublicationConflict(
                            "Rollback target has no prior active-version record."
                        )
            generation = conn.execute(
                """SELECT COALESCE(MAX(generation), 0) + 1
                   FROM mcp_saas.program_candidate_publication_events"""
            ).fetchone()[0]
            audit_context = request.get("operator_audit_context") or {}
            inserted = conn.execute(
                """INSERT INTO mcp_saas.program_candidate_publication_events
                   (generation, request_id, publication_key, action,
                    expected_candidate_id, expected_candidate_content_sha256,
                    target_candidate_id, target_candidate_content_sha256,
                    rationale, authorization_capability, mcp_session_sha256,
                    mcp_request_sha256, mcp_client_identity_status,
                    operator_credential_label)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING generation, request_id, publication_key, action,
                    expected_candidate_id, expected_candidate_content_sha256,
                    target_candidate_id, target_candidate_content_sha256,
                    rationale, authorization_capability, recorded_at,
                    mcp_session_sha256, mcp_request_sha256,
                    mcp_client_identity_status, operator_credential_label""",
                (
                    generation,
                    request["request_id"],
                    publication_key,
                    request["action"],
                    request["expected_current"]["candidate_id"]
                    if request["expected_current"]
                    else None,
                    request["expected_current"]["content_sha256"]
                    if request["expected_current"]
                    else None,
                    target["candidate_id"] if target else None,
                    target["content_sha256"] if target else None,
                    request["rationale"],
                    authorization_capability,
                    audit_context.get("mcp_session_sha256"),
                    audit_context.get("mcp_request_sha256"),
                    audit_context.get("mcp_client_identity_status"),
                    audit_context.get("operator_credential_label"),
                ),
            ).fetchone()
            event = _program_candidate_publication_event(inserted)
            created = True
    state = read_program_candidate_publication_state(publication_key)
    return (
        {
            "status": "recorded" if created else "replayed",
            "event": event,
            "active_state": state,
        },
        created,
    )


def record_source_policy_decision(
    manifest_id: str, manifest_version: str, payload: object
) -> tuple[dict[str, object], bool]:
    """Append one immutable review decision for the exact registered digest."""
    digest, decision, rationale = validate_source_policy_decision(
        payload, manifest_id, manifest_version
    )
    audit_context = validate_operator_audit_context(
        payload.get("operator_audit_context") if isinstance(payload, dict) else None
    )
    decision_id = uuid.uuid4()
    # The Persistence API authenticates a service capability, not an individual
    # human login. Core must enforce operator authorization before forwarding.
    authorization_capability = "registry_writer"
    with connect_db() as conn:
        candidate = conn.execute(
            """SELECT content_sha256, manifest
               FROM mcp_saas.source_policy_manifests
               WHERE manifest_id = %s AND manifest_version = %s""",
            (manifest_id, manifest_version),
        ).fetchone()
        if candidate is None:
            raise SourcePolicyManifestNotFound(
                "Source policy candidate is not registered."
            )
        manifest = validate_source_policy_manifest(candidate[1])
        actual_digest = source_policy_manifest_sha256(manifest)
        if (
            candidate[0].strip() != actual_digest
            or actual_digest != digest
            or manifest["manifest_id"] != manifest_id
            or manifest["manifest_version"] != manifest_version
        ):
            raise SourcePolicyDecisionConflict(
                "Decision digest does not match the registered candidate."
            )
        inserted = conn.execute(
            """INSERT INTO mcp_saas.source_policy_review_decisions
               (decision_id, manifest_id, manifest_version, content_sha256,
                decision, rationale, authorization_capability, mcp_session_sha256,
                mcp_request_sha256, mcp_client_identity_status,
                operator_credential_label)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (manifest_id, manifest_version) DO NOTHING
               RETURNING decision_id, content_sha256, decision, rationale,
                         authorization_capability, decided_at, mcp_session_sha256,
                         mcp_request_sha256, mcp_client_identity_status,
                         operator_credential_label""",
            (
                decision_id,
                manifest_id,
                manifest_version,
                digest,
                decision,
                rationale,
                authorization_capability,
                *_operator_audit_sql_values(audit_context),
            ),
        ).fetchone()
        row = (
            inserted
            or conn.execute(
                """SELECT decision_id, content_sha256, decision, rationale,
                      authorization_capability, decided_at, mcp_session_sha256,
                      mcp_request_sha256, mcp_client_identity_status,
                      operator_credential_label
               FROM mcp_saas.source_policy_review_decisions
               WHERE manifest_id = %s AND manifest_version = %s""",
                (manifest_id, manifest_version),
            ).fetchone()
        )
    if row is None:
        raise RuntimeError("Source policy decision could not be read back.")
    if (
        row[1].strip() != digest
        or row[2] != decision
        or row[3] != rationale
        or row[4] != authorization_capability
    ):
        raise SourcePolicyDecisionConflict(
            "Candidate already has a different immutable decision."
        )
    return (
        {
            "status": "recorded",
            "decision_id": str(row[0]),
            "manifest_id": manifest_id,
            "manifest_version": manifest_version,
            "content_sha256": digest,
            "decision": row[2],
            "rationale": row[3],
            "authorization_capability": row[4],
            "decided_at": row[5].isoformat() if row[5] else None,
            "operator_audit_context": _operator_audit_event_context(row, 6),
        },
        inserted is not None,
    )


def _source_policy_activation_event(row: tuple) -> dict[str, object]:
    """Shape one immutable lifecycle row for API readback."""
    expected_current = None
    if row[3] is not None:
        expected_current = {
            "manifest_id": row[3],
            "manifest_version": row[4],
            "content_sha256": row[5].strip(),
        }
    target = None
    if row[6] is not None:
        target = {
            "manifest_id": row[6],
            "manifest_version": row[7],
            "content_sha256": row[8].strip(),
        }
    return {
        "generation": row[0],
        "request_id": str(row[1]),
        "action": row[2],
        "expected_current": expected_current,
        "target": target,
        "rationale": row[9],
        "authorization_capability": row[10],
        "recorded_at": row[11].isoformat() if row[11] else None,
        "operator_audit_context": _operator_audit_event_context(row, 12),
    }


def _read_latest_source_policy_activation_event(conn) -> dict[str, object] | None:
    row = conn.execute(
        """SELECT generation, request_id, action, expected_manifest_id,
                  expected_manifest_version, expected_content_sha256,
                  target_manifest_id, target_manifest_version,
                  target_content_sha256, rationale, authorization_capability,
                  recorded_at, mcp_session_sha256, mcp_request_sha256,
                  mcp_client_identity_status, operator_credential_label
           FROM mcp_saas.source_policy_activation_events
           ORDER BY generation DESC LIMIT 1"""
    ).fetchone()
    return _source_policy_activation_event(row) if row else None


def _active_reference_from_event(
    event: dict[str, object] | None,
) -> dict[str, str] | None:
    if event is None or event["action"] == "deactivate":
        return None
    target = event["target"]
    return target if isinstance(target, dict) else None


def record_source_policy_activation(
    payload: object,
) -> tuple[dict[str, object], bool]:
    """Append an exact-digest lifecycle event with compare-and-set semantics."""
    request = validate_source_policy_activation(payload)
    request_id = request["request_id"]
    action = request["action"]
    expected_current = request["expected_current"]
    target = request["target"]
    rationale = request["rationale"]
    authorization_capability = "registry_writer"

    with connect_db() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("source-policy-activation",),
        )
        existing_row = conn.execute(
            """SELECT generation, request_id, action, expected_manifest_id,
                      expected_manifest_version, expected_content_sha256,
                      target_manifest_id, target_manifest_version,
                      target_content_sha256, rationale, authorization_capability,
                      recorded_at, mcp_session_sha256, mcp_request_sha256,
                      mcp_client_identity_status, operator_credential_label
               FROM mcp_saas.source_policy_activation_events
               WHERE request_id = %s""",
            (request_id,),
        ).fetchone()
        if existing_row is not None:
            event = _source_policy_activation_event(existing_row)
            if (
                event["action"] != action
                or event["expected_current"] != expected_current
                or event["target"] != target
                or event["rationale"] != rationale
            ):
                raise SourcePolicyActivationConflict(
                    "Lifecycle request ID was reused with different content."
                )
            created = False
        else:
            current_event = _read_latest_source_policy_activation_event(conn)
            current_reference = _active_reference_from_event(current_event)
            if current_reference != expected_current:
                raise SourcePolicyActivationConflict(
                    "Expected active source-policy version is stale."
                )

            if target is not None:
                candidate = conn.execute(
                    """SELECT m.candidate_id, m.content_sha256, m.manifest,
                              d.content_sha256, d.decision
                       FROM mcp_saas.source_policy_manifests AS m
                       LEFT JOIN mcp_saas.source_policy_review_decisions AS d
                         ON d.manifest_id = m.manifest_id
                        AND d.manifest_version = m.manifest_version
                       WHERE m.manifest_id = %s AND m.manifest_version = %s""",
                    (target["manifest_id"], target["manifest_version"]),
                ).fetchone()
                if candidate is None:
                    raise SourcePolicyManifestNotFound(
                        "Source policy candidate is not registered."
                    )
                manifest = validate_source_policy_manifest(candidate[2])
                actual_digest = source_policy_manifest_sha256(manifest)
                if (
                    candidate[1].strip() != actual_digest
                    or actual_digest != target["content_sha256"]
                    or manifest["manifest_id"] != target["manifest_id"]
                    or manifest["manifest_version"] != target["manifest_version"]
                ):
                    raise SourcePolicyActivationConflict(
                        "Target digest does not match the immutable manifest."
                    )
                if (
                    candidate[3] is None
                    or candidate[3].strip() != actual_digest
                    or candidate[4] != "approved"
                ):
                    raise SourcePolicyActivationConflict(
                        "Only an exactly approved source-policy digest can be active."
                    )

            if action == "rollback":
                previous = conn.execute(
                    """SELECT 1
                       FROM mcp_saas.source_policy_activation_events
                       WHERE action IN ('activate', 'rollback')
                         AND target_manifest_id = %s
                         AND target_manifest_version = %s
                         AND target_content_sha256 = %s
                       LIMIT 1""",
                    (
                        target["manifest_id"],
                        target["manifest_version"],
                        target["content_sha256"],
                    ),
                ).fetchone()
                if previous is None:
                    raise SourcePolicyActivationConflict(
                        "Rollback target has no prior active-version record."
                    )

            generation = conn.execute(
                """SELECT COALESCE(MAX(generation), 0) + 1
                   FROM mcp_saas.source_policy_activation_events"""
            ).fetchone()[0]
            audit_context = request.get("operator_audit_context") or {}
            inserted = conn.execute(
                """INSERT INTO mcp_saas.source_policy_activation_events
                   (generation, request_id, action,
                    expected_manifest_id, expected_manifest_version,
                    expected_content_sha256, target_manifest_id,
                    target_manifest_version, target_content_sha256,
                    rationale, authorization_capability, mcp_session_sha256,
                    mcp_request_sha256, mcp_client_identity_status,
                    operator_credential_label)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING generation, request_id, action,
                    expected_manifest_id, expected_manifest_version,
                    expected_content_sha256, target_manifest_id,
                    target_manifest_version, target_content_sha256,
                    rationale, authorization_capability, recorded_at,
                    mcp_session_sha256, mcp_request_sha256,
                    mcp_client_identity_status, operator_credential_label""",
                (
                    generation,
                    request_id,
                    action,
                    expected_current["manifest_id"] if expected_current else None,
                    expected_current["manifest_version"] if expected_current else None,
                    expected_current["content_sha256"] if expected_current else None,
                    target["manifest_id"] if target else None,
                    target["manifest_version"] if target else None,
                    target["content_sha256"] if target else None,
                    rationale,
                    authorization_capability,
                    audit_context.get("mcp_session_sha256"),
                    audit_context.get("mcp_request_sha256"),
                    audit_context.get("mcp_client_identity_status"),
                    audit_context.get("operator_credential_label"),
                ),
            ).fetchone()
            event = _source_policy_activation_event(inserted)
            created = True

    active_state = read_active_source_policy()
    return (
        {
            "status": "recorded" if created else "replayed",
            "event": event,
            "active_state": active_state,
        },
        created,
    )


def read_active_source_policy() -> dict[str, object]:
    """Read the active version reconstructed from the append-only event stream."""
    with connect_db() as conn:
        event = _read_latest_source_policy_activation_event(conn)
    active_reference = _active_reference_from_event(event)
    if active_reference is None:
        return {
            "status": "inactive",
            "active_manifest": None,
            "last_event": event,
        }

    manifest = read_source_policy_manifest(
        active_reference["manifest_id"], active_reference["manifest_version"]
    )
    if (
        manifest is None
        or manifest["content_sha256"] != active_reference["content_sha256"]
        or not isinstance(manifest.get("review_decision"), dict)
        or manifest["review_decision"].get("decision") != "approved"
        or manifest["review_decision"].get("content_sha256")
        != active_reference["content_sha256"]
    ):
        raise RuntimeError("Active source policy failed its approval or digest check.")
    return {
        "status": "active",
        "active_manifest": manifest,
        "last_event": event,
    }


def read_active_public_search_policy() -> dict[str, object]:
    """Project only an active approved search-policy bundle for catalog readers."""
    state = read_active_source_policy()
    if state.get("status") != "active":
        return {"status": "inactive", "active_reference": None}
    manifest_record = state.get("active_manifest")
    if not isinstance(manifest_record, dict):
        raise RuntimeError("Active source policy readback is invalid.")
    reference = {
        "manifest_id": manifest_record.get("manifest_id"),
        "manifest_version": manifest_record.get("manifest_version"),
        "content_sha256": manifest_record.get("content_sha256"),
    }
    manifest = manifest_record.get("manifest")
    if not isinstance(manifest, dict):
        raise RuntimeError("Active source policy manifest payload is invalid.")
    if manifest.get("manifest_kind") != "public_search_policy_bundle":
        return {"status": "unsupported_active_manifest", "active_reference": reference}
    if not isinstance(manifest.get("policy_bundle"), dict):
        raise RuntimeError("Active public-search policy bundle is invalid.")
    return {
        "status": "active",
        "active_reference": reference,
        "policy_bundle": manifest["policy_bundle"],
    }


def list_source_policy_activation_events(
    limit: int, before_generation: int | None = None
) -> dict[str, object]:
    """Read a bounded newest-first page of immutable source-policy lifecycle events."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("Activation history page size must be from 1 to 100.")
    if before_generation is not None and (
        isinstance(before_generation, bool)
        or not isinstance(before_generation, int)
        or before_generation < 1
    ):
        raise ValueError("Activation history cursor is invalid.")
    with connect_db() as conn:
        if before_generation is None:
            rows = conn.execute(
                """SELECT generation, request_id, action, expected_manifest_id,
                          expected_manifest_version, expected_content_sha256,
                          target_manifest_id, target_manifest_version,
                          target_content_sha256, rationale, authorization_capability,
                          recorded_at, mcp_session_sha256, mcp_request_sha256,
                          mcp_client_identity_status, operator_credential_label
                   FROM mcp_saas.source_policy_activation_events
                   ORDER BY generation DESC LIMIT %s""",
                (limit + 1,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT generation, request_id, action, expected_manifest_id,
                          expected_manifest_version, expected_content_sha256,
                          target_manifest_id, target_manifest_version,
                          target_content_sha256, rationale, authorization_capability,
                          recorded_at, mcp_session_sha256, mcp_request_sha256,
                          mcp_client_identity_status, operator_credential_label
                   FROM mcp_saas.source_policy_activation_events
                   WHERE generation < %s
                   ORDER BY generation DESC LIMIT %s""",
                (before_generation, limit + 1),
            ).fetchall()
    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "status": "available",
        "events": [_source_policy_activation_event(row) for row in page],
        "next_before_generation": page[-1][0] if has_more and page else None,
    }


def validate_requirements_package(payload: object) -> dict[str, object] | None:
    """Validate legacy binder packages and profile-specific report packages."""
    required_v1 = {
        "contract_version",
        "package_id",
        "tenant_id",
        "cluster_id",
        "pattern_version",
        "program_id",
        "catalog_version",
        "required_documents",
        "forms",
        "deadlines",
        "procedural_steps",
        "unavailable_detail_kinds",
        "source_references",
        "source_health",
        "source_limitations",
        "created_at",
        "official_eligibility_determination",
        "official_application_submitted",
    }
    if not isinstance(payload, dict):
        return None
    contract_version = payload.get("contract_version")
    if contract_version == "eligibility-requirements-package-v1":
        required = required_v1
    elif contract_version == "eligibility-requirements-package-v2":
        required = required_v1 | {"program_title", "profile_snapshot", "evaluation"}
    else:
        return None
    if set(payload) != required:
        return None
    if (
        payload.get("official_eligibility_determination") is not False
        or payload.get("official_application_submitted") is not False
    ):
        return None
    try:
        package_id = str(uuid.UUID(str(payload["package_id"])))
    except (ValueError, TypeError, AttributeError):
        return None
    if any(
        not isinstance(payload.get(key), str) or not payload[key].strip()
        for key in (
            "tenant_id",
            "cluster_id",
            "pattern_version",
            "program_id",
            "catalog_version",
            "created_at",
        )
    ):
        return None
    if (
        any(
            ID_PATTERN.fullmatch(str(payload[key])) is None
            for key in ("tenant_id", "cluster_id")
        )
        or PROGRAM_ID_PATTERN.fullmatch(str(payload["program_id"])) is None
        or VERSION_PATTERN.fullmatch(str(payload["pattern_version"])) is None
    ):
        return None
    if any(
        not isinstance(payload.get(key), list)
        for key in (
            "required_documents",
            "forms",
            "deadlines",
            "procedural_steps",
            "unavailable_detail_kinds",
            "source_references",
            "source_health",
            "source_limitations",
        )
    ):
        return None
    if contract_version == "eligibility-requirements-package-v2" and (
        not isinstance(payload.get("profile_snapshot"), dict)
        or not isinstance(payload.get("evaluation"), dict)
        or not isinstance(payload.get("program_title"), str)
        or not payload["program_title"].strip()
    ):
        return None
    if not payload["source_references"]:
        return None
    if not payload["source_health"] or any(
        not isinstance(item, dict) or item.get("status") != "current"
        for item in payload["source_health"]
    ):
        return None
    detail_kinds = {
        "required_documents": "required_document",
        "forms": "form",
        "deadlines": "deadline",
        "procedural_steps": "application_step",
    }
    if any(
        not isinstance(item, dict)
        or set(item) != {"kind", "text", "source_id"}
        or item.get("kind") != kind
        or not all(
            isinstance(item.get(field), str) and item[field]
            for field in ("text", "source_id")
        )
        for field, kind in detail_kinds.items()
        for item in payload[field]
    ):
        return None
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("source_id"), str)
        or not item["source_id"]
        for item in payload["source_references"]
    ):
        return None

    try:
        created_at = datetime.fromisoformat(str(payload["created_at"]))
    except ValueError:
        return None
    if created_at.utcoffset() is None:
        return None
    normalized = dict(payload)
    normalized["package_id"] = package_id
    return normalized


def write_requirements_package(
    payload: object,
) -> tuple[dict[str, object] | None, bool]:
    """Insert immutable sandbox package; exact replay returns original receipt."""
    package = validate_requirements_package(payload)
    if package is None:
        raise ValueError("Invalid requirements package.")
    canonical = dict(package)
    canonical.pop("created_at", None)
    digest = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with connect_db() as conn:
        tenant = conn.execute(
            """SELECT lifecycle_state FROM mcp_saas.tenant_registry
               WHERE tenant_id=%s AND pattern_id=%s AND pattern_version=%s""",
            (package["tenant_id"], package["cluster_id"], package["pattern_version"]),
        ).fetchone()
        if tenant is None or tenant[0] != "active":
            raise RequirementsPackageOutOfScope(
                "Requirements package tenant is not active for this pattern version."
            )
        inserted = conn.execute(
            """INSERT INTO mcp_saas.eligibility_requirements_packages
               (package_id, tenant_id, definition, content_sha256)
               VALUES (%s, %s, %s::jsonb, %s)
               ON CONFLICT (package_id) DO NOTHING
               RETURNING definition""",
            (package["package_id"], package["tenant_id"], json.dumps(package), digest),
        ).fetchone()
        row = (
            inserted
            or conn.execute(
                """SELECT definition, content_sha256
               FROM mcp_saas.eligibility_requirements_packages WHERE package_id=%s""",
                (package["package_id"],),
            ).fetchone()
        )
    if row is None or (not inserted and row[1] != digest):
        raise TemplatePackageConflict(
            "Requirements package identity conflicts with stored content."
        )
    return row[0], inserted is not None


PACKAGE_DETAIL_FIELDS = {
    "application_step": "procedural_steps",
    "form": "forms",
    "required_document": "required_documents",
    "deadline": "deadlines",
}


def validate_requirements_package_enrichment(
    payload: object,
) -> dict[str, object] | None:
    """Validate one source-cited, profile-scoped package detail enrichment."""
    if not isinstance(payload, dict) or set(payload) != {
        "tenant_id",
        "profile_id",
        "expected_revision",
        "details",
    }:
        return None
    if (
        not isinstance(payload["tenant_id"], str)
        or ID_PATTERN.fullmatch(payload["tenant_id"]) is None
        or not isinstance(payload["profile_id"], str)
        or PROFILE_ID_PATTERN.fullmatch(payload["profile_id"]) is None
        or isinstance(payload["expected_revision"], bool)
        or not isinstance(payload["expected_revision"], int)
        or payload["expected_revision"] < 1
        or not isinstance(payload["details"], list)
        or not 1 <= len(payload["details"]) <= 100
    ):
        return None
    details: list[dict[str, object]] = []
    allowed_citation_fields = {
        "source_url",
        "source_record_id",
        "retrieved_at",
        "source_id",
        "source_version",
        "source_content_sha256",
        "source_visible_text_sha256",
        "locator",
        "excerpt",
    }
    required_citation_fields = {
        "source_url",
        "source_record_id",
        "retrieved_at",
        "source_id",
        "source_version",
        "source_content_sha256",
        "locator",
        "excerpt",
    }
    for detail in payload["details"]:
        if not isinstance(detail, dict) or set(detail) != {"kind", "text", "citation"}:
            return None
        citation = detail["citation"]
        if (
            not isinstance(detail["kind"], str)
            or detail["kind"] not in PACKAGE_DETAIL_FIELDS
            or not isinstance(detail["text"], str)
            or not detail["text"].strip()
            or not isinstance(citation, dict)
            or not required_citation_fields.issubset(citation)
            or set(citation) - allowed_citation_fields
            or not all(
                isinstance(citation.get(key), str) and citation[key].strip()
                for key in (
                    "source_url",
                    "source_record_id",
                    "source_id",
                    "source_version",
                    "source_content_sha256",
                    "locator",
                    "excerpt",
                )
            )
            or re.fullmatch(r"[a-fA-F0-9]{64}", citation["source_content_sha256"])
            is None
        ):
            return None
        try:
            retrieved_at = datetime.fromisoformat(
                str(citation["retrieved_at"]).replace("Z", "+00:00")
            )
        except ValueError:
            return None
        if retrieved_at.utcoffset() is None:
            return None
        details.append(
            {
                "kind": detail["kind"],
                "text": detail["text"].strip(),
                "citation": dict(citation),
            }
        )
    return {**payload, "details": details}


def _package_enrichment_digest(details: list[dict[str, object]]) -> str:
    canonical = json.dumps(details, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _package_with_enrichment(
    package: dict[str, object], details: list[dict[str, object]]
) -> dict[str, object]:
    """Add cited detail records while retaining the complete original report."""
    enriched = json.loads(json.dumps(package))
    existing_citations = enriched.get("evaluation", {}).get(
        "requirements_detail_citations", []
    )
    if not isinstance(existing_citations, list):
        raise ValueError("Invalid package detail citation history.")
    citation_keys = {
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in existing_citations
        if isinstance(item, dict)
    }
    source_references = enriched.get("source_references", [])
    added_citations: list[dict[str, object]] = []
    for detail in details:
        supplied_citation = detail["citation"]
        assert isinstance(supplied_citation, dict)
        matching_sources = [
            source
            for source in source_references
            if isinstance(source, dict)
            and source.get("url") == supplied_citation.get("source_url")
        ]
        if len(matching_sources) != 1:
            raise ValueError(
                "Package detail URL must match exactly one saved source reference."
            )
        source = matching_sources[0]
        canonical_source_id = source.get("source_id")
        if not isinstance(canonical_source_id, str) or not canonical_source_id.strip():
            raise ValueError("Package source reference has no canonical source ID.")
        # A fresh source read may use a generic provider ID and a new snapshot.
        # The exact saved URL anchors it to this package; retain the package's
        # canonical source ID while keeping the new observation in the citation.
        citation = {**supplied_citation, "source_id": canonical_source_id}
        output = PACKAGE_DETAIL_FIELDS[str(detail["kind"])]
        source_detail = {
            "kind": detail["kind"],
            "text": detail["text"],
            "source_id": canonical_source_id,
        }
        existing_details = enriched.get(output)
        if not isinstance(existing_details, list):
            raise ValueError("Package detail collection is invalid.")
        if source_detail not in existing_details:
            existing_details.append(source_detail)
        citation_record = {
            "kind": detail["kind"],
            "text": detail["text"],
            "citation": citation,
        }
        citation_key = json.dumps(
            citation_record, sort_keys=True, separators=(",", ":")
        )
        if citation_key not in citation_keys:
            added_citations.append(citation_record)
            citation_keys.add(citation_key)
        unavailable = enriched.get("unavailable_detail_kinds", [])
        if isinstance(unavailable, list) and detail["kind"] in unavailable:
            enriched["unavailable_detail_kinds"] = [
                kind for kind in unavailable if kind != detail["kind"]
            ]
    enriched["evaluation"]["requirements_detail_citations"] = [
        *existing_citations,
        *added_citations,
    ]
    return enriched


def enrich_requirements_package(
    package_id: str, payload: object
) -> tuple[dict[str, object], int, str, bool]:
    """Append a source-backed package revision using a locked revision check."""
    request = validate_requirements_package_enrichment(payload)
    try:
        normalized_id = str(uuid.UUID(package_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Invalid requirements package ID.") from exc
    if request is None:
        raise ValueError("Invalid requirements package enrichment.")
    details = request["details"]
    assert isinstance(details, list)
    details_digest = _package_enrichment_digest(details)
    with connect_db() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"eligibility-requirements-package:{normalized_id}",),
        )
        base_row = conn.execute(
            """SELECT definition FROM mcp_saas.eligibility_requirements_packages
               WHERE package_id=%s AND tenant_id=%s
                 AND definition->'profile_snapshot'->>'profile_id'=%s
            """,
            (normalized_id, request["tenant_id"], request["profile_id"]),
        ).fetchone()
        if base_row is None:
            raise RequirementsPackageOutOfScope("Package is outside the profile scope.")
        revision_row = conn.execute(
            """SELECT revision, definition, details_sha256
               FROM mcp_saas.eligibility_requirements_package_revisions
               WHERE package_id=%s ORDER BY revision DESC LIMIT 1""",
            (normalized_id,),
        ).fetchone()
        current_revision = int(revision_row[0]) if revision_row else 1
        current_package = revision_row[1] if revision_row else base_row[0]
        if revision_row and revision_row[2].strip() == details_digest:
            return current_package, current_revision, details_digest, False
        if current_revision != request["expected_revision"]:
            raise RequirementsPackageRevisionConflict(current_revision)
        if not isinstance(current_package, dict) or not isinstance(
            current_package.get("evaluation"), dict
        ):
            raise ValueError("Only versioned report packages can be enriched.")
        enriched = _package_with_enrichment(current_package, details)
        if enriched == current_package:
            return current_package, current_revision, details_digest, False
        next_revision = current_revision + 1
        conn.execute(
            """INSERT INTO mcp_saas.eligibility_requirements_package_revisions
               (package_id, revision, definition, details_sha256)
               VALUES (%s, %s, %s::jsonb, %s)""",
            (normalized_id, next_revision, json.dumps(enriched), details_digest),
        )
    return enriched, next_revision, details_digest, True


def read_requirements_package(package_id: str) -> dict[str, object] | None:
    """Read one package within its stable receipt identity."""
    with connect_db() as conn:
        row = conn.execute(
            """SELECT COALESCE(revision.definition, package.definition)
               FROM mcp_saas.eligibility_requirements_packages AS package
               LEFT JOIN LATERAL (
                   SELECT definition
                   FROM mcp_saas.eligibility_requirements_package_revisions
                   WHERE package_id = package.package_id
                   ORDER BY revision DESC LIMIT 1
               ) AS revision ON TRUE
               WHERE package.package_id=%s""",
            (package_id,),
        ).fetchone()
    return row[0] if row else None


def read_requirements_package_state(
    package_id: str,
    tenant_id: str | None = None,
    profile_id: str | None = None,
) -> tuple[dict[str, object], int] | None:
    """Read one package and its revision from the same database snapshot."""
    scope_sql = ""
    parameters: tuple[object, ...] = (package_id,)
    if tenant_id is not None and profile_id is not None:
        scope_sql = (
            " AND package.tenant_id=%s "
            "AND package.definition->'profile_snapshot'->>'profile_id'=%s"
        )
        parameters += (tenant_id, profile_id)
    with connect_db() as conn:
        row = conn.execute(
            """SELECT COALESCE(revision.definition, package.definition),
                      COALESCE(revision.revision, 1)
               FROM mcp_saas.eligibility_requirements_packages AS package
               LEFT JOIN LATERAL (
                   SELECT revision, definition
                   FROM mcp_saas.eligibility_requirements_package_revisions
                   WHERE package_id=package.package_id
                   ORDER BY revision DESC LIMIT 1
               ) AS revision ON TRUE
               WHERE package.package_id=%s"""
            + scope_sql,
            parameters,
        ).fetchone()
    return (row[0], int(row[1])) if row else None


def read_scoped_requirements_package(
    package_id: str, tenant_id: str, profile_id: str
) -> dict[str, object] | None:
    """Read a package only when its tenant and synthetic profile match."""
    with connect_db() as conn:
        row = conn.execute(
            """SELECT COALESCE(revision.definition, package.definition)
               FROM mcp_saas.eligibility_requirements_packages AS package
               LEFT JOIN LATERAL (
                   SELECT definition
                   FROM mcp_saas.eligibility_requirements_package_revisions
                   WHERE package_id = package.package_id
                   ORDER BY revision DESC LIMIT 1
               ) AS revision ON TRUE
               WHERE package.package_id=%s AND package.tenant_id=%s
                 AND package.definition->'profile_snapshot'->>'profile_id'=%s""",
            (package_id, tenant_id, profile_id),
        ).fetchone()
    return row[0] if row else None


def list_requirements_packages(
    tenant_id: str,
    profile_id: str,
    limit: int = 25,
    cursor: str | None = None,
) -> dict[str, object]:
    """Return a bounded page of package summaries for one synthetic profile."""
    limit = _bounded_inventory_limit(limit)
    key = _decode_inventory_cursor(cursor, "eligibility-packages-v1", 2)
    where = "WHERE tenant_id=%s AND definition->'profile_snapshot'->>'profile_id'=%s"
    parameters: tuple[object, ...] = (tenant_id, profile_id)
    if key is not None:
        try:
            datetime.fromisoformat(key[0])
            uuid.UUID(key[1])
        except ValueError as exc:
            raise ValueError("invalid_inventory_cursor") from exc
        where += " AND (created_at, package_id) < (%s::timestamptz, %s::uuid)"
        parameters = (*parameters, key[0], key[1])
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT package_id, definition->>'program_id',
                      definition->>'program_title', created_at
               FROM mcp_saas.eligibility_requirements_packages """
            + where
            + " ORDER BY created_at DESC, package_id DESC LIMIT %s",
            (*parameters, limit + 1),
        ).fetchall()
    page = rows[:limit]
    truncated = len(rows) > limit
    next_cursor = None
    if truncated and page:
        last = page[-1]
        created_at = (
            last[3].isoformat() if hasattr(last[3], "isoformat") else str(last[3])
        )
        next_cursor = _encode_inventory_cursor(
            "eligibility-packages-v1", (created_at, str(last[0]))
        )
    return {
        "status": "available",
        "packages": [
            {
                "package_id": str(row[0]),
                "program_id": row[1],
                "program_title": row[2],
                "created_at": row[3].isoformat()
                if hasattr(row[3], "isoformat")
                else row[3],
            }
            for row in page
        ],
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


def list_template_packages(
    limit: int = 25, cursor: str | None = None
) -> dict[str, object]:
    """Return one stable, endpoint-free page of registered templates."""
    limit = _bounded_inventory_limit(limit)
    key = _decode_inventory_cursor(cursor, "templates-v1", 2)
    with connect_db() as conn:
        where = ""
        parameters: tuple[object, ...] = (limit + 1,)
        if key is not None:
            where = "WHERE (template_id, template_version) > (%s, %s)"
            parameters = (*key, limit + 1)
        rows = conn.execute(
            """SELECT template_id, template_version, lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
            """
            + where
            + " ORDER BY template_id, template_version LIMIT %s",
            parameters,
        ).fetchall()
    page = rows[:limit]
    return {
        "templates": [
            {
                "template_id": row[0],
                "template_version": row[1],
                "lifecycle_state": row[2],
                "definition": row[3],
            }
            for row in page
        ],
        "truncated": len(rows) > limit,
        "next_cursor": (
            _encode_inventory_cursor("templates-v1", tuple(page[-1][:2]))
            if len(rows) > limit and page
            else None
        ),
    }


def _tenant_route_id(tenant_id: str) -> str:
    """Return the stable tenant identity used to address its active route set."""
    return tenant_id


def _tenant_summary(
    tenant_id: str,
    template_id: str,
    template_version: str,
    lifecycle_state: str,
    tenant_context: dict[str, object],
    definition: dict[str, object],
    bindings: list[dict[str, object]],
) -> dict[str, object]:
    operations = definition.get("operations", [])
    operation_ids = {
        operation.get("operation_id")
        for operation in operations
        if isinstance(operation, dict)
    }
    return {
        "tenant_id": tenant_id,
        "template_id": template_id,
        "template_version": template_version,
        "lifecycle_state": lifecycle_state,
        "jurisdictions": tenant_context.get("jurisdictions", []),
        "operation_ids": sorted(
            route["operation_id"]
            for route in bindings
            if route.get("operation_id") in operation_ids
        ),
        "catalog_bindings": [
            {
                "dataset_id": binding["dataset_id"],
                "source_id": binding["source_id"],
                "catalog_namespace": binding["catalog_namespace"],
                "catalog_version": binding["catalog_version"],
            }
            for binding in bindings
            if "catalog_namespace" in binding
        ],
        "route_id": _tenant_route_id(tenant_id),
    }


def _tenant_record(
    conn, tenant_id: str, template_id: str, template_version: str
) -> dict[str, object] | None:
    row = conn.execute(
        """SELECT tenant.lifecycle_state, tenant.tenant_context,
                  tenant.tenant_spec_sha256, package.definition
           FROM mcp_saas.tenant_registry AS tenant
           JOIN mcp_saas.pattern_template_packages AS package
             ON package.template_id = tenant.pattern_id
            AND package.template_version = tenant.pattern_version
           WHERE tenant.tenant_id = %s AND tenant.pattern_id = %s
             AND tenant.pattern_version = %s""",
        (tenant_id, template_id, template_version),
    ).fetchone()
    if row is None:
        return None
    definition = row[3]
    if isinstance(definition, str):
        definition = json.loads(definition)
    bindings = conn.execute(
        """SELECT binding.dataset_id, binding.binding_context
           FROM mcp_saas.pattern_catalog_bindings AS binding
           WHERE binding.tenant_id = %s AND binding.cluster_id = %s
             AND binding.pattern_version = %s
           ORDER BY binding.catalog_namespace, binding.dataset_id""",
        (tenant_id, template_id, template_version),
    ).fetchall()
    normalized_bindings = []
    for dataset_id, context in bindings:
        if isinstance(context, str):
            context = json.loads(context)
        if not isinstance(context, dict):
            continue
        normalized_bindings.append(
            {
                "dataset_id": dataset_id,
                "source_id": context.get("source_id"),
                "catalog_namespace": context.get("catalog_namespace"),
                "catalog_version": context.get("catalog_version"),
            }
        )
    route_rows = conn.execute(
        """SELECT DISTINCT operation_id FROM mcp_saas.core_route_metadata
           WHERE tenant_id = %s AND template_id = %s AND route_version = %s
           ORDER BY operation_id""",
        (tenant_id, template_id, template_version),
    ).fetchall()
    route_bindings = [{"operation_id": item[0]} for item in route_rows]
    summary = _tenant_summary(
        tenant_id,
        template_id,
        template_version,
        row[0],
        row[1] if isinstance(row[1], dict) else {},
        definition,
        normalized_bindings + route_bindings,
    )
    return {**summary, "tenant_spec_sha256": row[2]}


def _canonical_json(value: object) -> str:
    """Serialize JSON-compatible registry values for exact structural checks."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_value(value: object) -> object:
    """Normalize PostgreSQL JSON values returned as either decoded data or text."""
    return json.loads(value) if isinstance(value, str) else value


def _is_jurisdiction_only_expansion(
    conn,
    spec: dict[str, object],
    existing: tuple[object, ...],
    operation_map: dict[str, dict[str, object]],
) -> bool:
    """Allow an active tenant's jurisdiction expansion without changing its bindings."""
    lifecycle_state, _digest, raw_context, endpoint = existing
    tenant_context = _json_value(raw_context)
    if (
        lifecycle_state != "active"
        or endpoint != spec["endpoint"]
        or not isinstance(tenant_context, dict)
        or set(tenant_context) != {"jurisdictions"}
    ):
        return False

    current_jurisdictions = tenant_context.get("jurisdictions")
    proposed_jurisdictions = spec["jurisdictions"]
    if not isinstance(current_jurisdictions, list) or not isinstance(
        proposed_jurisdictions, list
    ):
        return False
    current_keys = {
        (item.get("level"), item.get("code"))
        for item in current_jurisdictions
        if isinstance(item, dict)
    }
    proposed_keys = {
        (item.get("level"), item.get("code"))
        for item in proposed_jurisdictions
        if isinstance(item, dict)
    }
    if not current_keys or not current_keys < proposed_keys:
        return False

    binding_rows = conn.execute(
        """SELECT dataset_id, binding_context
           FROM mcp_saas.pattern_catalog_bindings
           WHERE tenant_id = %s AND cluster_id = %s AND pattern_version = %s""",
        (spec["tenant_id"], spec["template_id"], spec["template_version"]),
    ).fetchall()
    current_bindings = [
        {"dataset_id": dataset_id, "binding_context": _json_value(context)}
        for dataset_id, context in binding_rows
    ]
    proposed_bindings = spec["catalog_bindings"]
    if sorted(map(_canonical_json, current_bindings)) != sorted(
        map(_canonical_json, proposed_bindings)
    ):
        return False

    route_rows = conn.execute(
        """SELECT operation_id, endpoint, mcp_tool_name, protocol,
                  lifecycle_state, template_id, tenant_id
           FROM mcp_saas.core_route_metadata
           WHERE tenant_id = %s AND route_version = %s""",
        (
            spec["tenant_id"],
            spec["template_version"],
        ),
    ).fetchall()
    current_routes = {
        (route[0], route[1], route[2], route[3], route[5], route[6])
        for route in route_rows
        if route[4] == "active"
    }
    proposed_routes = {
        (
            operation_id,
            spec["endpoint"],
            operation_map[operation_id]["mcp_tool_name"],
            "mcp",
            spec["template_id"],
            spec["tenant_id"],
        )
        for operation_id in spec["operation_ids"]
    }
    return bool(current_routes) and current_routes == proposed_routes


def read_tenant_package(
    tenant_id: str, template_id: str, template_version: str
) -> dict[str, object] | None:
    """Read a privacy-safe tenant record without its private endpoint."""
    with connect_db() as conn:
        return _tenant_record(conn, tenant_id, template_id, template_version)


def read_bound_source_metadata(
    tenant_id: str,
    template_id: str,
    template_version: str,
    dataset_id: str,
    catalog_namespace: str,
) -> dict[str, object] | None:
    """Read allowlisted public source metadata for one exact tenant binding."""
    with connect_db() as conn:
        row = conn.execute(
            """SELECT source.source_id, source.source_name, source.source_url,
                      source.access_status, dataset.dataset_id, dataset.title,
                      dataset.landing_page, dataset.schema_version,
                      dataset.namespace_ids, source.updated_at, dataset.updated_at
               FROM mcp_saas.pattern_catalog_bindings AS binding
               JOIN mcp_saas.tenant_registry AS tenant
                 ON tenant.tenant_id = binding.tenant_id
                AND tenant.pattern_id = binding.cluster_id
                AND tenant.pattern_version = binding.pattern_version
               JOIN mcp_saas.public_datasets AS dataset
                 ON dataset.dataset_id = binding.dataset_id
               JOIN mcp_saas.public_data_sources AS source
                 ON source.source_id = dataset.source_id
               WHERE binding.tenant_id = %s AND binding.cluster_id = %s
                 AND binding.pattern_version = %s AND binding.dataset_id = %s
                 AND binding.catalog_namespace = %s
               ORDER BY source.source_id LIMIT 2""",
            (
                tenant_id,
                template_id,
                template_version,
                dataset_id,
                catalog_namespace,
            ),
        ).fetchall()
    if not row:
        return None
    if len(row) != 1:
        raise RuntimeError("Bound source metadata is ambiguous")
    item = row[0]
    return {
        "status": "available",
        "metadata_status": "available",
        "source_health": "not_assessed",
        "scope": {
            "tenant_id": tenant_id,
            "template_id": template_id,
            "template_version": template_version,
            "dataset_id": dataset_id,
            "catalog_namespace": catalog_namespace,
        },
        "source": {
            "source_id": item[0],
            "name": item[1],
            "url": item[2],
            "access_status": item[3],
            "updated_at": item[9].isoformat() if item[9] else None,
        },
        "dataset": {
            "dataset_id": item[4],
            "title": item[5],
            "landing_page": item[6],
            "schema_version": item[7],
            "namespaces": item[8] or [],
            "updated_at": item[10].isoformat() if item[10] else None,
        },
        "records_included": False,
    }


def list_tenant_packages(
    limit: int = 25, cursor: str | None = None
) -> dict[str, object]:
    """Return one stable, endpoint-free page of tenant registrations."""
    limit = _bounded_inventory_limit(limit)
    key = _decode_inventory_cursor(cursor, "tenants-v1", 3)
    with connect_db() as conn:
        where = ""
        parameters: tuple[object, ...] = (limit + 1,)
        if key is not None:
            where = "WHERE (tenant_id, pattern_id, pattern_version) > (%s, %s, %s)"
            parameters = (*key, limit + 1)
        rows = conn.execute(
            """SELECT tenant_id, pattern_id, pattern_version
               FROM mcp_saas.tenant_registry
            """
            + where
            + " ORDER BY tenant_id, pattern_id, pattern_version LIMIT %s",
            parameters,
        ).fetchall()
        page = rows[:limit]
        tenants = [_tenant_record(conn, row[0], row[1], row[2]) for row in page]
    return {
        "tenants": [item for item in tenants if item],
        "truncated": len(rows) > limit,
        "next_cursor": (
            _encode_inventory_cursor("tenants-v1", tuple(page[-1]))
            if len(rows) > limit and page
            else None
        ),
    }


def _validate_tenant_package_in_connection(
    conn, payload: object, *, lock_existing: bool = False
) -> tuple[dict[str, object], str, dict[str, object] | None]:
    """Check tenant references against the active template and public registries."""
    spec = validate_tenant_package(payload)
    canonical = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    package = conn.execute(
        """SELECT lifecycle_state, definition
           FROM mcp_saas.pattern_template_packages
           WHERE template_id = %s AND template_version = %s
           FOR SHARE""",
        (spec["template_id"], spec["template_version"]),
    ).fetchone()
    if package is None or package[0] != "active":
        raise InvalidTenantPackage("Selected template is not active.")
    definition = package[1]
    if isinstance(definition, str):
        definition = json.loads(definition)
    normalized_template = validate_template_package(definition)
    operation_map = {
        item["operation_id"]: item for item in normalized_template["operations"]
    }
    if not set(spec["operation_ids"]) <= operation_map.keys():
        raise InvalidTenantPackage(
            "Tenant selected an operation not declared by its template."
        )
    allowed_namespaces = set(normalized_template["catalog_namespaces"])
    for binding in spec["catalog_bindings"]:
        context = binding["binding_context"]
        if context["catalog_namespace"] not in allowed_namespaces:
            raise InvalidTenantPackage(
                "Catalog namespace is not declared by the template."
            )
        dataset = conn.execute(
            """SELECT source.source_id
               FROM mcp_saas.public_datasets AS dataset
               JOIN mcp_saas.public_data_sources AS source
                 ON source.source_id = dataset.source_id
               WHERE dataset.dataset_id = %s
                 AND source.access_status = 'available'""",
            (binding["dataset_id"],),
        ).fetchone()
        if dataset is None or dataset[0] != context["source_id"]:
            raise InvalidTenantPackage(
                "Catalog binding source is missing or unavailable."
            )
    existing_query = (
        "SELECT lifecycle_state, tenant_spec_sha256, tenant_context, mcp_endpoint "
        "FROM mcp_saas.tenant_registry "
        "WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s"
    )
    if lock_existing:
        existing_query += " FOR UPDATE"
    existing = conn.execute(
        existing_query,
        (spec["tenant_id"], spec["template_id"], spec["template_version"]),
    ).fetchone()
    if (
        existing is not None
        and existing[1] != digest
        and not (_is_jurisdiction_only_expansion(conn, spec, existing, operation_map))
    ):
        raise TemplatePackageConflict(
            "Tenant identity conflicts with registered content; only a "
            "jurisdiction-only expansion is supported."
        )
    safe_summary = _tenant_summary(
        spec["tenant_id"],
        spec["template_id"],
        spec["template_version"],
        existing[0] if existing else "pending",
        {"jurisdictions": spec["jurisdictions"]},
        normalized_template,
        [
            {
                "dataset_id": binding["dataset_id"],
                "source_id": binding["binding_context"]["source_id"],
                "catalog_namespace": binding["binding_context"]["catalog_namespace"],
                "catalog_version": binding["binding_context"]["catalog_version"],
            }
            for binding in spec["catalog_bindings"]
        ]
        + [{"operation_id": operation_id} for operation_id in spec["operation_ids"]],
    )
    if existing is not None:
        safe_summary["lifecycle_state"] = existing[0]
    return spec, digest, safe_summary


def validate_tenant_package_in_registry(payload: object) -> dict[str, object]:
    """Validate a proposed tenant package without persisting it."""
    with connect_db() as conn:
        spec, digest, summary = _validate_tenant_package_in_connection(conn, payload)
    del spec
    return {"status": "valid", "tenant_spec_sha256": digest, "tenant": summary}


def register_tenant_package(payload: object) -> dict[str, object]:
    """Register a tenant or stage an exact-digest-approved jurisdiction expansion."""
    with connect_db() as conn:
        identity_spec = validate_tenant_package(payload)
        identity = "|".join(
            (
                identity_spec["tenant_id"],
                identity_spec["template_id"],
                identity_spec["template_version"],
            )
        )
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (identity,)
        )
        spec, digest, summary = _validate_tenant_package_in_connection(
            conn, identity_spec, lock_existing=True
        )
        existing = conn.execute(
            """SELECT lifecycle_state, tenant_spec_sha256, tenant_context
               FROM mcp_saas.tenant_registry
               WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s
               FOR UPDATE""",
            (spec["tenant_id"], spec["template_id"], spec["template_version"]),
        ).fetchone()
        if existing is not None and existing[1] != digest:
            previous_context = _json_value(existing[2])
            next_context = {"jurisdictions": spec["jurisdictions"]}
            conn.execute(
                """INSERT INTO mcp_saas.tenant_registry_jurisdiction_revisions
                   (tenant_id, pattern_id, pattern_version, previous_spec_sha256,
                    revised_spec_sha256, previous_context, revised_context)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                   ON CONFLICT (tenant_id, pattern_id, pattern_version,
                                revised_spec_sha256) DO NOTHING""",
                (
                    spec["tenant_id"],
                    spec["template_id"],
                    spec["template_version"],
                    existing[1],
                    digest,
                    _canonical_json(previous_context),
                    _canonical_json(next_context),
                ),
            )
            revision_update = conn.execute(
                """UPDATE mcp_saas.tenant_registry
                   SET tenant_spec_sha256 = %s, tenant_context = %s::jsonb,
                       lifecycle_state = 'pending', updated_at = now()
                   WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s
                     AND tenant_spec_sha256 = %s""",
                (
                    digest,
                    _canonical_json(next_context),
                    spec["tenant_id"],
                    spec["template_id"],
                    spec["template_version"],
                    existing[1],
                ),
            )
            if revision_update.rowcount != 1:
                raise TemplatePackageConflict(
                    "Tenant changed while its jurisdiction revision was staged."
                )
            route_update = conn.execute(
                """UPDATE mcp_saas.core_route_metadata
                   SET lifecycle_state = 'inactive', updated_at = now()
                   WHERE route_id = %s AND tenant_id = %s AND template_id = %s
                     AND route_version = %s AND lifecycle_state = 'active'""",
                (
                    spec["tenant_id"],
                    spec["tenant_id"],
                    spec["template_id"],
                    spec["template_version"],
                ),
            )
            if route_update.rowcount != len(spec["operation_ids"]):
                raise TemplatePackageConflict(
                    "Tenant routes changed while its jurisdiction revision was staged."
                )
            revised = _tenant_record(
                conn,
                str(spec["tenant_id"]),
                str(spec["template_id"]),
                str(spec["template_version"]),
            )
            return {
                "status": "revised",
                "tenant_spec_sha256": digest,
                "tenant": revised,
            }
        if (
            summary["lifecycle_state"] != "pending"
            or conn.execute(
                """SELECT 1 FROM mcp_saas.tenant_registry
               WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s""",
                (spec["tenant_id"], spec["template_id"], spec["template_version"]),
            ).fetchone()
        ):
            return {
                "status": "registered",
                "tenant_spec_sha256": digest,
                "tenant": summary,
            }
        conn.execute(
            """INSERT INTO mcp_saas.tenant_registry
               (manifest_id, tenant_id, pattern_id, pattern_version, mcp_endpoint,
                lifecycle_state, git_revision, tenant_spec_sha256, tenant_context)
                VALUES (%s, %s, %s, %s, %s, 'pending', 'api-managed', %s, %s::jsonb)""",
            (
                "api-managed",
                spec["tenant_id"],
                spec["template_id"],
                spec["template_version"],
                spec["endpoint"],
                digest,
                json.dumps({"jurisdictions": spec["jurisdictions"]}),
            ),
        )
        for binding in spec["catalog_bindings"]:
            context = binding["binding_context"]
            conn.execute(
                """INSERT INTO mcp_saas.pattern_catalog_bindings
                   (dataset_id, cluster_id, pattern_version, tenant_id,
                    catalog_namespace, binding_context)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                   ON CONFLICT (dataset_id, cluster_id, tenant_id,
                                (COALESCE(pattern_version, '')),
                                (COALESCE(catalog_namespace, '')))
                   DO UPDATE SET binding_context = EXCLUDED.binding_context""",
                (
                    binding["dataset_id"],
                    spec["template_id"],
                    spec["template_version"],
                    spec["tenant_id"],
                    context["catalog_namespace"],
                    json.dumps(context, sort_keys=True, separators=(",", ":")),
                ),
            )
        package = conn.execute(
            """SELECT definition FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s""",
            (spec["template_id"], spec["template_version"]),
        ).fetchone()
        definition = package[0]
        if isinstance(definition, str):
            definition = json.loads(definition)
        operation_map = {
            item["operation_id"]: item for item in definition["operations"]
        }
        route_id = _tenant_route_id(spec["tenant_id"])
        for operation_id in spec["operation_ids"]:
            operation = operation_map[operation_id]
            conn.execute(
                """INSERT INTO mcp_saas.core_route_metadata
                   (route_id, route_version, operation_id, endpoint, mcp_tool_name,
                    protocol, lifecycle_state, template_id, tenant_id)
                   VALUES (%s, %s, %s, %s, %s, 'mcp', 'inactive', %s, %s)
                   ON CONFLICT (route_id, route_version, operation_id) DO NOTHING""",
                (
                    route_id,
                    spec["template_version"],
                    operation_id,
                    spec["endpoint"],
                    operation["mcp_tool_name"],
                    spec["template_id"],
                    spec["tenant_id"],
                ),
            )
            route = conn.execute(
                """SELECT endpoint, mcp_tool_name, protocol, lifecycle_state,
                          template_id, tenant_id
                   FROM mcp_saas.core_route_metadata
                   WHERE route_id = %s AND route_version = %s AND operation_id = %s""",
                (route_id, spec["template_version"], operation_id),
            ).fetchone()
            if route != (
                spec["endpoint"],
                operation["mcp_tool_name"],
                "mcp",
                "inactive",
                spec["template_id"],
                spec["tenant_id"],
            ):
                raise TemplatePackageConflict(
                    "Tenant route identity conflicts with registered content."
                )
    return {"status": "registered", "tenant_spec_sha256": digest, "tenant": summary}


def activate_tenant_package(
    tenant_id: str,
    template_id: str,
    template_version: str,
    approved_digest: str,
) -> dict[str, object] | None:
    """Activate one tenant version and retire its previously active version."""
    with connect_db() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (tenant_id,)
        )
        row = conn.execute(
            """SELECT tenant.lifecycle_state, tenant.tenant_spec_sha256
               FROM mcp_saas.tenant_registry AS tenant
               JOIN mcp_saas.pattern_template_packages AS package
                 ON package.template_id = tenant.pattern_id
                AND package.template_version = tenant.pattern_version
                AND package.lifecycle_state = 'active'
               WHERE tenant.tenant_id = %s AND tenant.pattern_id = %s
                 AND tenant.pattern_version = %s FOR UPDATE""",
            (tenant_id, template_id, template_version),
        ).fetchone()
        if (
            row is None
            or row[1] != approved_digest
            or row[0] not in {"pending", "active", "inactive"}
        ):
            return None
        conn.execute(
            """UPDATE mcp_saas.tenant_registry
               SET lifecycle_state = 'inactive', updated_at = now()
               WHERE tenant_id = %s AND lifecycle_state = 'active'
                 AND NOT (pattern_id = %s AND pattern_version = %s)""",
            (tenant_id, template_id, template_version),
        )
        conn.execute(
            """UPDATE mcp_saas.core_route_metadata
               SET lifecycle_state = 'inactive', updated_at = now()
               WHERE tenant_id = %s AND lifecycle_state = 'active'
                 AND NOT (template_id = %s AND route_version = %s
                          AND route_id = %s)""",
            (tenant_id, template_id, template_version, tenant_id),
        )
        # Copy any legacy tenant.template route rows to the canonical tenant ID.
        # Older rows remain inactive in the registry as rollback lineage.
        conn.execute(
            """INSERT INTO mcp_saas.core_route_metadata
               (route_id, route_version, operation_id, endpoint, mcp_tool_name,
                protocol, lifecycle_state, template_id, tenant_id)
               SELECT tenant_id, route_version, operation_id, endpoint,
                      mcp_tool_name, protocol, 'active', template_id, tenant_id
               FROM mcp_saas.core_route_metadata
               WHERE tenant_id = %s AND template_id = %s
                 AND route_version = %s AND route_id <> tenant_id
               ON CONFLICT (route_id, route_version, operation_id) DO UPDATE
               SET lifecycle_state = 'active', updated_at = now()
               WHERE mcp_saas.core_route_metadata.tenant_id = EXCLUDED.tenant_id
                 AND mcp_saas.core_route_metadata.template_id = EXCLUDED.template_id""",
            (tenant_id, template_id, template_version),
        )
        conn.execute(
            """UPDATE mcp_saas.core_route_metadata
               SET lifecycle_state = 'inactive', updated_at = now()
               WHERE tenant_id = %s AND template_id = %s
                 AND route_version = %s AND route_id <> %s""",
            (tenant_id, template_id, template_version, tenant_id),
        )
        conn.execute(
            """UPDATE mcp_saas.tenant_registry
               SET lifecycle_state = 'active', updated_at = now()
               WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s""",
            (tenant_id, template_id, template_version),
        )
        conn.execute(
            """UPDATE mcp_saas.core_route_metadata
               SET lifecycle_state = 'active', updated_at = now()
               WHERE route_id = %s AND route_version = %s AND tenant_id = %s
                 AND template_id = %s""",
            (tenant_id, template_version, tenant_id, template_id),
        )
        return _tenant_record(conn, tenant_id, template_id, template_version)


def deactivate_tenant_package(
    tenant_id: str,
    template_id: str,
    template_version: str,
    approved_digest: str,
) -> dict[str, object] | None:
    """Deactivate one exact tenant spec and only its tenant-scoped routes."""
    with connect_db() as conn:
        row = conn.execute(
            """SELECT lifecycle_state, tenant_spec_sha256
               FROM mcp_saas.tenant_registry
               WHERE tenant_id = %s AND pattern_id = %s
                 AND pattern_version = %s FOR UPDATE""",
            (tenant_id, template_id, template_version),
        ).fetchone()
        if (
            row is None
            or row[1] != approved_digest
            or row[0] not in {"active", "inactive"}
        ):
            return None
        if row[0] == "active":
            conn.execute(
                """UPDATE mcp_saas.tenant_registry
                   SET lifecycle_state = 'inactive', updated_at = now()
                   WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s""",
                (tenant_id, template_id, template_version),
            )
            conn.execute(
                """UPDATE mcp_saas.core_route_metadata
                   SET lifecycle_state = 'inactive', updated_at = now()
                   WHERE tenant_id = %s AND template_id = %s
                     AND route_version = %s""",
                (tenant_id, template_id, template_version),
            )
        return _tenant_record(conn, tenant_id, template_id, template_version)


def _tenant_lifecycle_state_sha256(
    tenant_id: str, template_id: str, version: str, state: str, spec_sha256: str
) -> str:
    canonical = json.dumps(
        {
            "tenant_id": tenant_id,
            "template_id": template_id,
            "template_version": version,
            "lifecycle_state": state,
            "tenant_spec_sha256": spec_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _tenant_lifecycle_event(row: tuple) -> dict[str, object]:
    return {
        "transaction_id": str(row[0]),
        "tenant_id": row[1],
        "template_id": row[2],
        "template_version": row[3],
        "action": row[4],
        "expected_before_sha256": row[5].strip(),
        "approval_reference": row[6],
        "before_state": row[7],
        "after_state": row[8],
        "outcome": row[9],
        "tenant_spec_sha256": row[10].strip(),
        "before_state_sha256": row[11].strip(),
        "after_state_sha256": row[12].strip(),
        "authorization_capability": row[13],
        "recorded_at": row[14].isoformat() if row[14] else None,
        "operator_audit_context": {
            "mcp_session_sha256": row[15].strip() if row[15] else None,
            "mcp_request_sha256": row[16].strip() if row[16] else None,
            "mcp_client_identity_status": row[17],
            "operator_credential_label": row[18],
        },
    }


def set_tenant_package_state_governed(
    tenant_id: str,
    template_id: str,
    template_version: str,
    *,
    action: str,
    approved_tenant_spec_sha256: str,
    expected_before_sha256: str,
    transaction_id: str,
    approval_reference: str,
    operator_audit_context: object,
) -> dict[str, object] | None:
    """Apply one exact tenant lifecycle transition with CAS and audit receipt."""
    transaction_id = str(uuid.UUID(transaction_id))
    reference = (
        approval_reference.strip() if isinstance(approval_reference, str) else ""
    )
    audit = validate_operator_audit_context(operator_audit_context)
    if (
        action not in {"activate", "deactivate"}
        or re.fullmatch(r"[0-9a-f]{64}", approved_tenant_spec_sha256) is None
        or re.fullmatch(r"[0-9a-f]{64}", expected_before_sha256) is None
        or not reference
        or len(reference) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in reference)
        or audit is None
    ):
        raise ValueError("Tenant lifecycle approval fields are invalid.")

    columns = """transaction_id, tenant_id, template_id, template_version,
        action, expected_before_sha256, approval_reference, before_state,
        after_state, outcome, tenant_spec_sha256, before_state_sha256,
        after_state_sha256,
        authorization_capability, recorded_at, mcp_session_sha256,
        mcp_request_sha256, mcp_client_identity_status, operator_credential_label"""
    with connect_db() as conn:
        lock = int.from_bytes(
            hashlib.sha256(tenant_id.encode()).digest()[:8], "big", signed=True
        )
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock,))
        current = conn.execute(
            """SELECT lifecycle_state, tenant_spec_sha256
               FROM mcp_saas.tenant_registry
               WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s
               FOR UPDATE""",
            (tenant_id, template_id, template_version),
        ).fetchone()
        if current is None:
            return None
        existing = conn.execute(
            f"SELECT {columns} FROM mcp_saas.tenant_lifecycle_events "
            "WHERE transaction_id = %s",
            (transaction_id,),
        ).fetchone()
        if existing is not None:
            event = _tenant_lifecycle_event(existing)
            if any(
                event[key] != expected
                for key, expected in (
                    ("tenant_id", tenant_id),
                    ("template_id", template_id),
                    ("template_version", template_version),
                    ("action", action),
                    ("expected_before_sha256", expected_before_sha256),
                    ("approval_reference", reference),
                    ("tenant_spec_sha256", approved_tenant_spec_sha256),
                )
            ):
                raise TenantLifecycleConflict(
                    "Tenant lifecycle transaction conflicts with its receipt.",
                    reason="transaction_conflict",
                )
            readback = _tenant_record(conn, tenant_id, template_id, template_version)
            if readback is None:
                return None
            return {
                "status": "idempotent_replay",
                "event": event,
                "readback": {
                    **readback,
                    "lifecycle_state_sha256": _tenant_lifecycle_state_sha256(
                        tenant_id,
                        template_id,
                        template_version,
                        str(readback["lifecycle_state"]),
                        str(readback["tenant_spec_sha256"]),
                    ),
                },
            }

        before, spec_digest = current
        if spec_digest != approved_tenant_spec_sha256:
            raise TenantLifecycleConflict(
                "Tenant specification digest changed.",
                reason="approval_digest_mismatch",
            )
        before_digest = _tenant_lifecycle_state_sha256(
            tenant_id, template_id, template_version, before, spec_digest
        )
        if not hmac.compare_digest(before_digest, expected_before_sha256):
            raise TenantLifecycleConflict(
                "Tenant lifecycle before-state is stale.", reason="stale_before_state"
            )
        after = "active" if action == "activate" else "inactive"
        if action == "activate":
            if before not in {"pending", "inactive"}:
                raise TenantLifecycleConflict(
                    "Tenant is not pending or inactive.",
                    reason="invalid_lifecycle_state",
                )
            package = conn.execute(
                """SELECT lifecycle_state FROM mcp_saas.pattern_template_packages
                   WHERE template_id = %s AND template_version = %s""",
                (template_id, template_version),
            ).fetchone()
            if package is None or package[0] != "active":
                raise TenantLifecycleConflict(
                    "Tenant template is not active.", reason="template_not_active"
                )
            conn.execute(
                """UPDATE mcp_saas.tenant_registry
                   SET lifecycle_state = 'inactive', updated_at = now()
                   WHERE tenant_id = %s AND lifecycle_state = 'active'
                     AND NOT (pattern_id = %s AND pattern_version = %s)""",
                (tenant_id, template_id, template_version),
            )
            conn.execute(
                """UPDATE mcp_saas.core_route_metadata
                   SET lifecycle_state = 'inactive', updated_at = now()
                   WHERE tenant_id = %s AND lifecycle_state = 'active'
                     AND NOT (template_id = %s AND route_version = %s
                              AND route_id = %s)""",
                (tenant_id, template_id, template_version, tenant_id),
            )
            conn.execute(
                """INSERT INTO mcp_saas.core_route_metadata
                   (route_id, route_version, operation_id, endpoint, mcp_tool_name,
                    protocol, lifecycle_state, template_id, tenant_id)
                   SELECT tenant_id, route_version, operation_id, endpoint,
                          mcp_tool_name, protocol, 'active', template_id, tenant_id
                   FROM mcp_saas.core_route_metadata
                   WHERE tenant_id = %s AND template_id = %s
                     AND route_version = %s AND route_id <> tenant_id
                   ON CONFLICT (route_id, route_version, operation_id) DO UPDATE
                   SET lifecycle_state = 'active', updated_at = now()
                   WHERE mcp_saas.core_route_metadata.tenant_id = EXCLUDED.tenant_id
                     AND mcp_saas.core_route_metadata.template_id =
                         EXCLUDED.template_id""",
                (tenant_id, template_id, template_version),
            )
            conn.execute(
                """UPDATE mcp_saas.core_route_metadata
                   SET lifecycle_state = 'inactive', updated_at = now()
                   WHERE tenant_id = %s AND template_id = %s AND route_version = %s
                     AND route_id <> %s""",
                (tenant_id, template_id, template_version, tenant_id),
            )
            conn.execute(
                """UPDATE mcp_saas.tenant_registry
                   SET lifecycle_state = 'active', updated_at = now()
                   WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s""",
                (tenant_id, template_id, template_version),
            )
            conn.execute(
                """UPDATE mcp_saas.core_route_metadata
                   SET lifecycle_state = 'active', updated_at = now()
                   WHERE route_id = %s AND route_version = %s AND tenant_id = %s
                     AND template_id = %s""",
                (tenant_id, template_version, tenant_id, template_id),
            )
        else:
            if before != "active":
                raise TenantLifecycleConflict(
                    "Tenant is not active.", reason="invalid_lifecycle_state"
                )
            conn.execute(
                """UPDATE mcp_saas.tenant_registry
                   SET lifecycle_state = 'inactive', updated_at = now()
                   WHERE tenant_id = %s AND pattern_id = %s AND pattern_version = %s""",
                (tenant_id, template_id, template_version),
            )
            conn.execute(
                """UPDATE mcp_saas.core_route_metadata
                   SET lifecycle_state = 'inactive', updated_at = now()
                   WHERE tenant_id = %s AND template_id = %s AND route_version = %s""",
                (tenant_id, template_id, template_version),
            )
        after_digest = _tenant_lifecycle_state_sha256(
            tenant_id, template_id, template_version, after, spec_digest
        )
        conn.execute(
            """INSERT INTO mcp_saas.tenant_lifecycle_events (
                   transaction_id, tenant_id, template_id, template_version, action,
                   expected_before_sha256, approval_reference, before_state,
                   after_state, outcome, tenant_spec_sha256, before_state_sha256,
                   after_state_sha256, authorization_capability,
                   mcp_session_sha256, mcp_request_sha256,
                   mcp_client_identity_status, operator_credential_label
               ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'applied',
                         %s, %s, %s, 'tenant_registry_writer', %s, %s, %s, %s)""",
            (
                transaction_id,
                tenant_id,
                template_id,
                template_version,
                action,
                expected_before_sha256,
                reference,
                before,
                after,
                spec_digest,
                before_digest,
                after_digest,
                audit["mcp_session_sha256"],
                audit["mcp_request_sha256"],
                audit["mcp_client_identity_status"],
                audit["operator_credential_label"],
            ),
        )
        receipt = conn.execute(
            f"SELECT {columns} FROM mcp_saas.tenant_lifecycle_events "
            "WHERE transaction_id = %s",
            (transaction_id,),
        ).fetchone()
        readback = _tenant_record(conn, tenant_id, template_id, template_version)
        if (
            receipt is None
            or readback is None
            or readback["lifecycle_state"] != after
            or _tenant_lifecycle_state_sha256(
                tenant_id,
                template_id,
                template_version,
                str(readback["lifecycle_state"]),
                str(readback["tenant_spec_sha256"]),
            )
            != after_digest
        ):
            raise TenantLifecycleConflict(
                "Tenant lifecycle readback mismatched.", reason="readback_mismatch"
            )
        return {
            "status": "recorded",
            "event": _tenant_lifecycle_event(receipt),
            "readback": {**readback, "lifecycle_state_sha256": after_digest},
        }


class TemplateLifecycleConflict(RuntimeError):
    """A template lifecycle transition conflicts with current or approved state."""

    def __init__(self, message: str, *, reason: str = "lifecycle_conflict") -> None:
        super().__init__(message)
        self.reason = reason


def set_template_package_state(
    template_id: str, template_version: str, target_state: str
) -> dict[str, object] | None:
    """Activate a package for test/bootstrap setup; deactivation must be governed."""
    if target_state != "active":
        raise ValueError(
            "Template deactivation requires the governed lifecycle operation."
        )
    with connect_db() as conn:
        row = conn.execute(
            """SELECT lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s
               FOR UPDATE""",
            (template_id, template_version),
        ).fetchone()
        if row is None or row[0] not in {"pending", "active"}:
            return None
        validate_template_package(row[1])
        if row[0] == "pending":
            conn.execute(
                """UPDATE mcp_saas.pattern_template_packages
                   SET lifecycle_state = 'active', updated_at = now()
                   WHERE template_id = %s AND template_version = %s
                     AND lifecycle_state = 'pending'""",
                (template_id, template_version),
            )
    return {
        "template_id": template_id,
        "template_version": template_version,
        "lifecycle_state": "active",
        "definition": row[1],
    }


def _template_lifecycle_state_sha256(
    template_id: str,
    template_version: str,
    lifecycle_state: str,
    descriptor_sha256: str,
) -> str:
    canonical = json.dumps(
        {
            "template_id": template_id,
            "template_version": template_version,
            "lifecycle_state": lifecycle_state,
            "descriptor_sha256": descriptor_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _template_lifecycle_event(row: tuple) -> dict[str, object]:
    return {
        "transaction_id": str(row[0]),
        "template_id": row[1],
        "template_version": row[2],
        "action": row[3],
        "expected_before_sha256": row[4].strip(),
        "approval_reference": row[5],
        "before_state": row[6],
        "after_state": row[7],
        "descriptor_sha256": row[8].strip(),
        "before_state_sha256": row[9].strip(),
        "after_state_sha256": row[10].strip(),
        "authorization_capability": row[11],
        "recorded_at": row[12].isoformat() if row[12] else None,
        "operator_audit_context": {
            "mcp_session_sha256": row[13].strip() if row[13] else None,
            "mcp_request_sha256": row[14].strip() if row[14] else None,
            "mcp_client_identity_status": row[15],
            "operator_credential_label": row[16],
        },
    }


def activate_template_package_governed(
    template_id: str,
    template_version: str,
    *,
    expected_before_sha256: str,
    transaction_id: str,
    approval_reference: str,
    operator_audit_context: object,
) -> dict[str, object] | None:
    """Activate a pending package with CAS, an immutable receipt, and exact replay."""
    normalized_transaction_id = str(uuid.UUID(transaction_id))
    normalized_approval_reference = (
        approval_reference.strip() if isinstance(approval_reference, str) else ""
    )
    if (
        not normalized_approval_reference
        or len(normalized_approval_reference) > 512
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in normalized_approval_reference
        )
        or not re.fullmatch(r"[0-9a-f]{64}", expected_before_sha256)
    ):
        raise ValueError("Template activation approval fields are invalid.")
    audit_context = validate_operator_audit_context(operator_audit_context)
    if audit_context is None:
        raise ValueError("Template activation audit context is required.")

    event_columns = """transaction_id, template_id, template_version, action,
        expected_before_sha256, approval_reference, before_state, after_state,
        descriptor_sha256, before_state_sha256, after_state_sha256,
        authorization_capability, recorded_at, mcp_session_sha256,
        mcp_request_sha256, mcp_client_identity_status, operator_credential_label"""
    with connect_db() as conn:
        lock_key = int.from_bytes(
            uuid.UUID(normalized_transaction_id).bytes[:8], "big", signed=True
        )
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
        current = conn.execute(
            """SELECT lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s
               FOR UPDATE""",
            (template_id, template_version),
        ).fetchone()
        if current is None:
            return None

        existing = conn.execute(
            f"SELECT {event_columns} FROM mcp_saas.pattern_template_lifecycle_events "
            "WHERE transaction_id = %s",
            (normalized_transaction_id,),
        ).fetchone()
        if existing is not None:
            event = _template_lifecycle_event(existing)
            if (
                event["template_id"] != template_id
                or event["template_version"] != template_version
                or event["action"] != "activate"
                or event["expected_before_sha256"] != expected_before_sha256
                or event["approval_reference"] != normalized_approval_reference
            ):
                raise TemplateLifecycleConflict(
                    "Template activation transaction conflicts with its receipt."
                )
            descriptor = current[1]
            descriptor_sha256 = hashlib.sha256(
                json.dumps(descriptor, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            return {
                "status": "idempotent_replay",
                "event": event,
                "readback": {
                    "template_id": template_id,
                    "template_version": template_version,
                    "lifecycle_state": current[0],
                    "descriptor_sha256": descriptor_sha256,
                    "lifecycle_state_sha256": _template_lifecycle_state_sha256(
                        template_id, template_version, current[0], descriptor_sha256
                    ),
                },
            }

        descriptor_sha256 = hashlib.sha256(
            json.dumps(current[1], sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        before_state_sha256 = _template_lifecycle_state_sha256(
            template_id, template_version, current[0], descriptor_sha256
        )
        if not hmac.compare_digest(before_state_sha256, expected_before_sha256):
            raise TemplateLifecycleConflict("Template before-state is stale.")
        if current[0] != "pending":
            raise TemplateLifecycleConflict("Template is no longer pending.")
        validate_template_package(current[1])
        after_state_sha256 = _template_lifecycle_state_sha256(
            template_id, template_version, "active", descriptor_sha256
        )
        conn.execute(
            """UPDATE mcp_saas.pattern_template_packages
               SET lifecycle_state = 'active', updated_at = now()
               WHERE template_id = %s AND template_version = %s
                 AND lifecycle_state = 'pending'""",
            (template_id, template_version),
        )
        conn.execute(
            """INSERT INTO mcp_saas.pattern_template_lifecycle_events (
                   transaction_id, template_id, template_version, action,
                   expected_before_sha256, approval_reference, before_state,
                   after_state, descriptor_sha256, before_state_sha256,
                   after_state_sha256, authorization_capability,
                   mcp_session_sha256, mcp_request_sha256,
                   mcp_client_identity_status, operator_credential_label
               ) VALUES (%s, %s, %s, 'activate', %s, %s, 'pending', 'active',
                         %s, %s, %s, 'template_registry_writer',
                         %s, %s, %s, %s)""",
            (
                normalized_transaction_id,
                template_id,
                template_version,
                expected_before_sha256,
                normalized_approval_reference,
                descriptor_sha256,
                before_state_sha256,
                after_state_sha256,
                audit_context["mcp_session_sha256"],
                audit_context["mcp_request_sha256"],
                audit_context["mcp_client_identity_status"],
                audit_context["operator_credential_label"],
            ),
        )
        receipt_row = conn.execute(
            f"SELECT {event_columns} FROM mcp_saas.pattern_template_lifecycle_events "
            "WHERE transaction_id = %s",
            (normalized_transaction_id,),
        ).fetchone()
        readback = conn.execute(
            """SELECT lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s""",
            (template_id, template_version),
        ).fetchone()
        if (
            receipt_row is None
            or readback is None
            or readback[0] != "active"
            or _template_lifecycle_state_sha256(
                template_id, template_version, readback[0], descriptor_sha256
            )
            != after_state_sha256
        ):
            raise TemplateLifecycleConflict("Template activation readback mismatched.")
        return {
            "status": "recorded",
            "event": _template_lifecycle_event(receipt_row),
            "readback": {
                "template_id": template_id,
                "template_version": template_version,
                "lifecycle_state": readback[0],
                "descriptor_sha256": descriptor_sha256,
                "lifecycle_state_sha256": after_state_sha256,
            },
        }


def deactivate_template_package_governed(
    template_id: str,
    template_version: str,
    *,
    expected_before_sha256: str,
    transaction_id: str,
    approval_reference: str,
    operator_audit_context: object,
) -> dict[str, object] | None:
    """Deactivate a package and its shared routes without cascading to tenants."""
    normalized_transaction_id = str(uuid.UUID(transaction_id))
    normalized_approval_reference = (
        approval_reference.strip() if isinstance(approval_reference, str) else ""
    )
    if (
        not normalized_approval_reference
        or len(normalized_approval_reference) > 512
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in normalized_approval_reference
        )
        or not re.fullmatch(r"[0-9a-f]{64}", expected_before_sha256)
    ):
        raise ValueError("Template deactivation approval fields are invalid.")
    audit_context = validate_operator_audit_context(operator_audit_context)
    if audit_context is None:
        raise ValueError("Template deactivation audit context is required.")

    event_columns = """transaction_id, template_id, template_version, action,
        expected_before_sha256, approval_reference, before_state, after_state,
        descriptor_sha256, before_state_sha256, after_state_sha256,
        authorization_capability, recorded_at, mcp_session_sha256,
        mcp_request_sha256, mcp_client_identity_status, operator_credential_label"""
    with connect_db() as conn:
        lock_key = int.from_bytes(
            uuid.UUID(normalized_transaction_id).bytes[:8], "big", signed=True
        )
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))
        current = conn.execute(
            """SELECT lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s
               FOR UPDATE""",
            (template_id, template_version),
        ).fetchone()
        if current is None:
            return None

        existing = conn.execute(
            f"SELECT {event_columns} FROM mcp_saas.pattern_template_lifecycle_events "
            "WHERE transaction_id = %s",
            (normalized_transaction_id,),
        ).fetchone()
        if existing is not None:
            event = _template_lifecycle_event(existing)
            if (
                event["template_id"] != template_id
                or event["template_version"] != template_version
                or event["action"] != "deactivate"
                or event["expected_before_sha256"] != expected_before_sha256
                or event["approval_reference"] != normalized_approval_reference
            ):
                raise TemplateLifecycleConflict(
                    "Template deactivation transaction conflicts with its receipt.",
                    reason="transaction_conflict",
                )
            descriptor_sha256 = hashlib.sha256(
                json.dumps(current[1], sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()
            state_sha256 = _template_lifecycle_state_sha256(
                template_id, template_version, current[0], descriptor_sha256
            )
            return {
                "status": "idempotent_replay",
                "event": event,
                "readback": {
                    "template_id": template_id,
                    "template_version": template_version,
                    "lifecycle_state": current[0],
                    "descriptor_sha256": descriptor_sha256,
                    "lifecycle_state_sha256": state_sha256,
                },
            }

        descriptor_sha256 = hashlib.sha256(
            json.dumps(current[1], sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        before_state_sha256 = _template_lifecycle_state_sha256(
            template_id, template_version, current[0], descriptor_sha256
        )
        if not hmac.compare_digest(before_state_sha256, expected_before_sha256):
            raise TemplateLifecycleConflict(
                "Template before-state is stale.", reason="stale_before_state"
            )
        if current[0] != "active":
            raise TemplateLifecycleConflict(
                "Template is no longer active.", reason="template_not_active"
            )
        dependent = conn.execute(
            """SELECT 1 FROM mcp_saas.tenant_registry
               WHERE pattern_id = %s AND pattern_version = %s
                 AND lifecycle_state = 'active'
               LIMIT 1""",
            (template_id, template_version),
        ).fetchone()
        if dependent is not None:
            raise TemplateLifecycleConflict(
                "Active tenants must be migrated or deactivated separately.",
                reason="active_tenant_dependency",
            )

        after_state_sha256 = _template_lifecycle_state_sha256(
            template_id, template_version, "inactive", descriptor_sha256
        )
        conn.execute(
            """UPDATE mcp_saas.pattern_template_packages
               SET lifecycle_state = 'inactive', updated_at = now()
               WHERE template_id = %s AND template_version = %s
                 AND lifecycle_state = 'active'""",
            (template_id, template_version),
        )
        conn.execute(
            """UPDATE mcp_saas.core_route_metadata
               SET lifecycle_state = 'inactive', updated_at = now()
               WHERE template_id = %s AND route_version = %s
                 AND tenant_id IS NULL AND lifecycle_state = 'active'""",
            (template_id, template_version),
        )
        conn.execute(
            """INSERT INTO mcp_saas.pattern_template_lifecycle_events (
                   transaction_id, template_id, template_version, action,
                   expected_before_sha256, approval_reference, before_state,
                   after_state, descriptor_sha256, before_state_sha256,
                   after_state_sha256, authorization_capability,
                   mcp_session_sha256, mcp_request_sha256,
                   mcp_client_identity_status, operator_credential_label
               ) VALUES (%s, %s, %s, 'deactivate', %s, %s, 'active', 'inactive',
                         %s, %s, %s, 'template_registry_writer',
                         %s, %s, %s, %s)""",
            (
                normalized_transaction_id,
                template_id,
                template_version,
                expected_before_sha256,
                normalized_approval_reference,
                descriptor_sha256,
                before_state_sha256,
                after_state_sha256,
                audit_context["mcp_session_sha256"],
                audit_context["mcp_request_sha256"],
                audit_context["mcp_client_identity_status"],
                audit_context["operator_credential_label"],
            ),
        )
        receipt_row = conn.execute(
            f"SELECT {event_columns} FROM mcp_saas.pattern_template_lifecycle_events "
            "WHERE transaction_id = %s",
            (normalized_transaction_id,),
        ).fetchone()
        readback = conn.execute(
            """SELECT lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s""",
            (template_id, template_version),
        ).fetchone()
        if (
            receipt_row is None
            or readback is None
            or readback[0] != "inactive"
            or _template_lifecycle_state_sha256(
                template_id, template_version, readback[0], descriptor_sha256
            )
            != after_state_sha256
        ):
            raise TemplateLifecycleConflict(
                "Template deactivation readback mismatched."
            )
        return {
            "status": "recorded",
            "event": _template_lifecycle_event(receipt_row),
            "readback": {
                "template_id": template_id,
                "template_version": template_version,
                "lifecycle_state": readback[0],
                "descriptor_sha256": descriptor_sha256,
                "lifecycle_state_sha256": after_state_sha256,
            },
        }


def bind_template_operation(
    template_id: str,
    template_version: str,
    operation_id: str,
    endpoint: str,
) -> tuple[dict[str, str], bool]:
    """Bind a declared package operation to one private handler for Core routing."""
    with connect_db() as conn:
        package = conn.execute(
            """SELECT lifecycle_state, definition
               FROM mcp_saas.pattern_template_packages
               WHERE template_id = %s AND template_version = %s
               FOR UPDATE""",
            (template_id, template_version),
        ).fetchone()
        if package is None or package[0] != "active":
            raise TemplatePackageNotFound("Active template package was not found.")
        validate_template_package(package[1])
        operation = next(
            (
                item
                for item in package[1]["operations"]
                if item["operation_id"] == operation_id
            ),
            None,
        )
        if operation is None:
            raise TemplatePackageNotFound("Template operation was not declared.")
        # Serialize updates to this stable operation pointer across package versions.
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
            (template_id, operation_id),
        )
        conn.execute(
            """UPDATE mcp_saas.core_route_metadata
               SET lifecycle_state = 'inactive', updated_at = now()
               WHERE route_id = %s AND operation_id = %s
                 AND tenant_id IS NULL AND lifecycle_state = 'active'
                 AND route_version <> %s""",
            (template_id, operation_id, template_version),
        )
        inserted = conn.execute(
            """INSERT INTO mcp_saas.core_route_metadata
               (route_id, route_version, operation_id, endpoint, mcp_tool_name,
                protocol, lifecycle_state, template_id)
               VALUES (%s, %s, %s, %s, %s, 'mcp', 'active', %s)
               ON CONFLICT (route_id, route_version, operation_id) DO NOTHING
               RETURNING endpoint, mcp_tool_name, protocol, lifecycle_state,
                         template_id, tenant_id""",
            (
                template_id,
                template_version,
                operation_id,
                endpoint,
                operation["mcp_tool_name"],
                template_id,
            ),
        ).fetchone()
        route = (
            inserted
            or conn.execute(
                """SELECT endpoint, mcp_tool_name, protocol, lifecycle_state,
                          template_id, tenant_id
               FROM mcp_saas.core_route_metadata
               WHERE route_id = %s AND route_version = %s AND operation_id = %s""",
                (template_id, template_version, operation_id),
            ).fetchone()
        )
        if route is None:
            raise RuntimeError("Core route binding could not be read back.")
        if route[:3] != (endpoint, operation["mcp_tool_name"], "mcp"):
            raise TemplatePackageConflict(
                "Template operation already has a different Core route binding."
            )
        if route[4:] != (template_id, None):
            raise TemplatePackageConflict(
                "Template route identity conflicts with registered context."
            )
        if route[3:] == ("inactive",):
            conn.execute(
                """UPDATE mcp_saas.core_route_metadata
                   SET lifecycle_state = 'active', updated_at = now()
                   WHERE route_id = %s AND route_version = %s AND operation_id = %s""",
                (template_id, template_version, operation_id),
            )
    return (
        {
            "route_id": template_id,
            "route_version": template_version,
            "operation_id": operation_id,
            "endpoint": endpoint,
            "mcp_tool_name": operation["mcp_tool_name"],
            "protocol": "mcp",
        },
        inserted is not None,
    )


def read_tenant_programs(
    tenant_id: str, cluster_id: str, pattern_version: str
) -> list[dict[str, object]]:
    """Read one active tenant catalog through the bounded catalog operation."""
    with connect_db() as conn:
        rows = conn.execute(
            """SELECT catalog.program_id, catalog.title, catalog.source_id,
                      catalog.jurisdiction, catalog.catalog_version,
                      catalog.schema_version, catalog.source_url,
                      catalog.attributes, catalog.provenance
               FROM mcp_saas.program_catalog AS catalog
               JOIN mcp_saas.tenant_registry AS tenant
                 ON tenant.tenant_id = catalog.tenant_id
                AND tenant.pattern_id = catalog.cluster_id
                AND tenant.lifecycle_state = 'active'
               WHERE tenant.tenant_id = %s
                 AND tenant.pattern_id = %s
                 AND tenant.pattern_version = %s
                 AND catalog.tenant_id = %s
                 AND catalog.cluster_id = %s
               ORDER BY catalog.program_id""",
            (tenant_id, cluster_id, pattern_version, tenant_id, cluster_id),
        ).fetchall()
    return [
        {
            "program_id": row[0],
            "title": row[1],
            "source_id": row[2],
            "jurisdiction": row[3],
            "catalog_version": row[4],
            "schema_version": row[5],
            "source_url": row[6],
            "attributes": row[7],
            "provenance": row[8],
        }
        for row in rows
    ]


def valid_tenant_endpoint(payload: object) -> tuple[str, str, str, str] | None:
    """Validate a private tenant endpoint update without accepting extra fields."""
    fields = {"tenant_id", "pattern_id", "pattern_version", "endpoint"}
    if not isinstance(payload, dict) or set(payload) != fields:
        return None
    if not all(isinstance(value, str) for value in payload.values()):
        return None
    tenant_id = payload["tenant_id"]
    pattern_id = payload["pattern_id"]
    pattern_version = payload["pattern_version"]
    endpoint = payload["endpoint"]
    if not ID_PATTERN.fullmatch(tenant_id) or not ID_PATTERN.fullmatch(pattern_id):
        return None
    if not VERSION_PATTERN.fullmatch(pattern_version):
        return None
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or not parsed.hostname.endswith(".svc.cluster.local")
        or parsed.path != "/mcp"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return None
    return tenant_id, pattern_id, pattern_version, endpoint


class Handler(BaseHTTPRequestHandler):
    """HTTP handler for the Persistence Access API."""

    server_version = "MCP-SaaS-Data-API/0.1"

    def log_message(self, fmt: str, *args: object) -> None:
        """Log the method and path only, never a query string or body."""
        print(f"data-api {self.command} {urlsplit(self.path).path}", flush=True)

    def send_json(self, status: int, value: object) -> None:
        """Send a JSON response with the given status."""
        if getattr(self, "_timing_operation", None) == "public_dataset_records":
            started_at = getattr(self, "_timing_started_at", None)
            if isinstance(started_at, float):
                correlation_id = (
                    getattr(self, "_timing_correlation_id", None) or "unavailable"
                )
                print(
                    "data_api_timing operation=public_dataset_records "
                    f"correlation_id={correlation_id} "
                    f"status={status} "
                    f"elapsed_ms={(time.perf_counter() - started_at) * 1000:.1f}",
                    flush=True,
                )
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_correlation_header(self) -> str | None:
        """Return the caller's correlation ID only when it is a valid UUID."""
        value = self.headers.get("X-MCP-Correlation-Id", "")
        try:
            return str(uuid.UUID(value)) if value else None
        except ValueError:
            return None

    def authorized(self, token_file: Path = TOKEN_FILE) -> bool:
        """Return whether the request carries the expected bearer token."""
        try:
            token = read_secret(token_file)
        except OSError:
            return False
        return bool(token) and hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + token
        )

    def do_GET(self) -> None:
        """Route GET requests to their read operations."""
        path = urlsplit(self.path).path
        public_catalog_request = re.fullmatch(
            r"/v1/public-datasets/[a-z][a-z0-9-]{0,63}/records", path
        )
        if public_catalog_request:
            self._timing_operation = "public_dataset_records"
            self._timing_started_at = time.perf_counter()
            try:
                self._timing_correlation_id = str(
                    uuid.UUID(self.headers.get("X-MCP-Correlation-Id", ""))
                )
            except ValueError:
                self._timing_correlation_id = None
        if path == "/healthz":
            try:
                with connect_db() as conn:
                    conn.execute("SELECT 1 FROM mcp_saas.core_route_metadata LIMIT 0")
                self.send_json(200, {"status": "ok"})
            except Exception:
                self.send_json(503, {"status": "unavailable"})
            return
        if path == "/v1/eligibility/requirements-packages":
            if not self.authorized(REQUIREMENTS_PACKAGE_WRITE_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                parameters = parse_qs(
                    urlsplit(self.path).query,
                    keep_blank_values=True,
                    strict_parsing=True,
                )
                if (
                    set(parameters) - {"tenant_id", "profile_id", "limit", "cursor"}
                    or any(len(values) != 1 for values in parameters.values())
                    or not {"tenant_id", "profile_id"}.issubset(parameters)
                ):
                    raise ValueError("invalid_package_list_query")
                tenant_id = parameters["tenant_id"][0]
                profile_id = parameters["profile_id"][0]
                raw_limit = parameters.get("limit", ["25"])[0]
                if (
                    ID_PATTERN.fullmatch(tenant_id) is None
                    or PROFILE_ID_PATTERN.fullmatch(profile_id) is None
                    or re.fullmatch(r"[0-9]{1,3}", raw_limit) is None
                ):
                    raise ValueError("invalid_package_list_query")
                limit = _bounded_inventory_limit(int(raw_limit))
                cursor = parameters.get("cursor", [None])[0]
                if cursor == "":
                    raise ValueError("invalid_inventory_cursor")
                result = list_requirements_packages(
                    tenant_id, profile_id, limit=limit, cursor=cursor
                )
                self.send_json(200, result)
            except ValueError:
                self.send_json(400, {"status": "invalid_requirements_package_query"})
            except Exception:
                self.send_json(
                    503, {"status": "requirements_package_store_unavailable"}
                )
            return
        package_match = re.fullmatch(
            r"/v1/eligibility/requirements-packages/([0-9a-f-]{36})", path
        )
        if package_match:
            if not self.authorized(REQUIREMENTS_PACKAGE_WRITE_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                package_id = str(uuid.UUID(package_match.group(1)))
                query = parse_qs(
                    urlsplit(self.path).query,
                    keep_blank_values=True,
                    strict_parsing=True,
                )
                if query:
                    if set(query) != {"tenant_id", "profile_id"} or any(
                        len(values) != 1 for values in query.values()
                    ):
                        raise ValueError("invalid_requirements_package_scope")
                    tenant_id = query["tenant_id"][0]
                    profile_id = query["profile_id"][0]
                    if (
                        ID_PATTERN.fullmatch(tenant_id) is None
                        or PROFILE_ID_PATTERN.fullmatch(profile_id) is None
                    ):
                        raise ValueError("invalid_requirements_package_scope")
                    package_state = read_requirements_package_state(
                        package_id, tenant_id, profile_id
                    )
                else:
                    package_state = read_requirements_package_state(package_id)
                if package_state is None:
                    self.send_json(404, {"status": "package_not_found"})
                else:
                    package, revision = package_state
                    self.send_json(
                        200,
                        {
                            "status": "ok",
                            "package": package,
                            "revision": revision,
                        },
                    )
            except ValueError:
                self.send_json(400, {"status": "invalid_requirements_package_scope"})
            except Exception:
                self.send_json(503, {"status": "requirements_package_unavailable"})
            return
        fixture_list_match = re.fullmatch(
            r"/v1/evaluation/fixture-sets/([a-z][a-z0-9-]{0,63})/"
            r"([0-9]+\.[0-9]+\.[0-9]+)/fixtures",
            path,
        )
        if fixture_list_match:
            if not self.authorized(PROFILE_FIXTURE_READ_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                result = list_evaluation_fixture_members(*fixture_list_match.groups())
                if result is None:
                    self.send_json(404, {"status": "fixture_set_not_found"})
                else:
                    self.send_json(200, result)
            except Exception:
                self.send_json(503, {"status": "evaluation_fixtures_unavailable"})
            return
        fixture_read_match = re.fullmatch(
            r"/v1/evaluation/fixture-sets/([a-z][a-z0-9-]{0,63})/"
            r"([0-9]+\.[0-9]+\.[0-9]+)/fixtures/"
            r"(fixture-[a-z0-9]{4,32})",
            path,
        )
        if fixture_read_match:
            if not self.authorized(PROFILE_FIXTURE_READ_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                result = read_evaluation_fixture_member(*fixture_read_match.groups())
                if result is None:
                    self.send_json(404, {"status": "fixture_not_found"})
                else:
                    self.send_json(200, result)
            except Exception:
                self.send_json(503, {"status": "evaluation_fixture_unavailable"})
            return
        if path == "/v1/profile-sessions/current":
            if not self.authorized(PROFILE_READ_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            profile_ref = self.headers.get("X-Profile-Reference", "")
            profile_scope = self.headers.get("X-Profile-Scope", "")
            if not profile_ref or not profile_scope:
                self.send_json(400, {"status": "profile_scope_required"})
                return
            try:
                profile = read_scoped_synthetic_profile(
                    profile_ref,
                    profile_scope,
                    self._read_correlation_header(),
                )
                if profile.get("status") == "profile_reference_expired":
                    self.send_json(410, profile)
                elif profile.get("status") == "profile_not_found_in_scope":
                    self.send_json(404, profile)
                else:
                    self.send_json(200, profile)
            except Exception:
                self.send_json(503, {"status": "profile_store_unavailable"})
            return
        profile_schema_match = re.fullmatch(
            r"/v1/profile-schemas/([a-z][a-z0-9-]{0,63})/"
            r"([a-z0-9][a-z0-9._-]{0,63})",
            path,
        )
        if profile_schema_match and profile_schema_match.group(2) != "active":
            if not self.authorized(PROFILE_READ_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                schema = read_profile_schema(*profile_schema_match.groups())
                if schema is None:
                    self.send_json(404, {"status": "schema_not_found"})
                else:
                    self.send_json(200, schema)
            except Exception:
                self.send_json(503, {"status": "profile_schema_unavailable"})
            return
        active_profile_schema_match = re.fullmatch(
            r"/v1/profile-schemas/([a-z][a-z0-9-]{0,63})/active", path
        )
        if active_profile_schema_match:
            if not self.authorized(CATALOG_READ_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                schemas = list_active_profile_schemas(
                    active_profile_schema_match.group(1)
                )
                self.send_json(
                    200,
                    {
                        "status": "ok",
                        "registry_id": active_profile_schema_match.group(1),
                        "schemas": schemas,
                    },
                )
            except Exception:
                self.send_json(503, {"status": "profile_schema_unavailable"})
            return
        scoped_criteria_ruleset_match = re.fullmatch(
            r"/v1/eligibility/clusters/([a-z][a-z0-9-]{0,63})/"
            r"criteria-rulesets/([a-z0-9][a-z0-9._-]{1,99})",
            path,
        )
        legacy_criteria_ruleset_match = re.fullmatch(
            r"/v1/eligibility/criteria-rulesets/([a-z0-9][a-z0-9._-]{1,99})",
            path,
        )
        if scoped_criteria_ruleset_match or legacy_criteria_ruleset_match:
            cluster_id, program_id = (
                (
                    scoped_criteria_ruleset_match.group(1),
                    scoped_criteria_ruleset_match.group(2),
                )
                if scoped_criteria_ruleset_match
                else ("eligibility", legacy_criteria_ruleset_match.group(1))
            )
            reader_token_file = criteria_ruleset_reader_token_file(cluster_id)
            if reader_token_file is None or not self.authorized(reader_token_file):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                with connect_public_catalog_ingest_db() as conn:
                    result = read_active_criteria_ruleset(conn, cluster_id, program_id)
                if result is None:
                    self.send_json(
                        404,
                        {
                            "status": "criteria_ruleset_not_found",
                            "cluster_id": cluster_id,
                            "program_id": program_id,
                        },
                    )
                else:
                    self.send_json(200, result)
            except InvalidCriteriaRuleset:
                self.send_json(400, {"status": "invalid_program_id"})
            except Exception:
                self.send_json(503, {"status": "criteria_ruleset_read_unavailable"})
            return
        if path == "/v1/public-catalog/inventory":
            if not self.authorized(CATALOG_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                limit, cursor = _parse_public_catalog_inventory_query(self.path)
                result = list_public_catalog_inventory(limit=limit, cursor=cursor)
                self.send_json(200, result)
            except ValueError:
                self.send_json(400, {"error": "invalid_public_catalog_inventory_page"})
            except Exception:
                self.send_json(503, {"error": "public_catalog_inventory_unavailable"})
            return
        public_catalog_receipt_match = re.fullmatch(
            r"/v1/public-catalog/registrations/receipts/([0-9a-f-]{36})", path
        )
        if public_catalog_receipt_match:
            if not self.authorized(PUBLIC_CATALOG_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                receipt = read_public_catalog_registration_receipt(
                    public_catalog_receipt_match.group(1)
                )
            except ValueError:
                self.send_json(400, {"error": "invalid_public_catalog_receipt_id"})
                return
            except Exception:
                self.send_json(503, {"error": "public_catalog_receipt_unavailable"})
                return
            if receipt is None:
                self.send_json(404, {"error": "public_catalog_receipt_not_found"})
            else:
                self.send_json(200, receipt)
            return
        public_catalog_match = re.fullmatch(
            r"/v1/public-datasets/([a-z][a-z0-9-]{0,63})/records", path
        )
        if public_catalog_match:
            if not self.authorized(CATALOG_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if set(query) - {"limit", "offset", "source_record_id"} or any(
                len(values) != 1 for values in query.values()
            ):
                self.send_json(400, {"error": "invalid_public_dataset_page"})
                return
            try:
                limit_text = query.get("limit", ["100"])[0]
                offset_text = query.get("offset", ["0"])[0]
                if not limit_text.isdecimal() or not offset_text.isdecimal():
                    raise ValueError("Page values must be decimal integers.")
                read_arguments = {
                    "limit": int(limit_text),
                    "offset": int(offset_text),
                }
                if self._timing_correlation_id is not None:
                    read_arguments["correlation_id"] = self._timing_correlation_id
                if "source_record_id" in query:
                    read_arguments["source_record_id"] = query["source_record_id"][0]
                result = read_public_dataset(
                    public_catalog_match.group(1), **read_arguments
                )
                if result is None:
                    self.send_json(404, {"error": "public_dataset_not_found"})
                else:
                    self.send_json(200, result)
            except ValueError:
                self.send_json(400, {"error": "invalid_public_dataset_page"})
            except Exception:
                self.send_json(503, {"error": "public_catalog_unavailable"})
            return
        tenant_catalog_match = re.fullmatch(
            r"/v1/tenants/([a-z][a-z0-9-]{0,63})/patterns/"
            r"([a-z][a-z0-9-]{0,63})/([0-9]+\.[0-9]+\.[0-9]+)/"
            r"public-datasets/([a-z][a-z0-9-]{0,63})/"
            r"(?:namespaces/([a-z][a-z0-9-]{0,63})/)?records",
            path,
        )
        if tenant_catalog_match:
            if not self.authorized(CATALOG_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                tenant_id, cluster_id, pattern_version, dataset_id, namespace = (
                    tenant_catalog_match.groups()
                )
                result = read_tenant_public_dataset(
                    tenant_id, cluster_id, pattern_version, dataset_id, namespace
                )
                if result and result.get("status") == "namespace_required":
                    self.send_json(409, {"error": "catalog_namespace_required"})
                elif result is None:
                    self.send_json(404, {"error": "tenant_dataset_binding_not_found"})
                else:
                    self.send_json(200, result)
            except Exception:
                self.send_json(503, {"error": "tenant_catalog_unavailable"})
            return
        if path == "/v1/pattern-templates":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                limit, cursor = _parse_inventory_query(self.path)
                self.send_json(200, list_template_packages(limit, cursor))
            except ValueError:
                self.send_json(400, {"error": "invalid_inventory_page"})
            except Exception:
                self.send_json(503, {"error": "template_registry_unavailable"})
            return
        if path == "/v1/tenants":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                limit, cursor = _parse_inventory_query(self.path)
                self.send_json(200, list_tenant_packages(limit, cursor))
            except ValueError:
                self.send_json(400, {"error": "invalid_inventory_page"})
            except Exception:
                self.send_json(503, {"error": "tenant_registry_unavailable"})
            return
        if path == "/v1/registry/source-policy-active":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                self.send_json(200, read_active_source_policy())
            except Exception:
                self.send_json(503, {"error": "source_policy_activation_unavailable"})
            return
        if path == "/v1/public-search-policy/active":
            if not self.authorized(CATALOG_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                self.send_json(200, read_active_public_search_policy())
            except Exception:
                self.send_json(503, {"error": "public_search_policy_unavailable"})
            return
        if path == "/v1/registry/source-policy-activation-events":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if set(query) - {"limit", "before_generation"} or any(
                    len(values) != 1 for values in query.values()
                ):
                    raise ValueError
                limit = int(query.get("limit", ["50"])[0])
                before_value = query.get("before_generation", [None])[0]
                before_generation = (
                    int(before_value) if before_value is not None else None
                )
                self.send_json(
                    200,
                    list_source_policy_activation_events(limit, before_generation),
                )
            except ValueError:
                self.send_json(400, {"error": "invalid_source_policy_activation_page"})
            except Exception:
                self.send_json(503, {"error": "source_policy_activation_unavailable"})
            return
        program_candidate_match = re.fullmatch(
            r"/v1/registry/program-candidates/(candidate-[0-9a-f]{32})", path
        )
        if program_candidate_match:
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = read_program_candidate_record(program_candidate_match.group(1))
            except Exception:
                self.send_json(503, {"error": "program_candidate_unavailable"})
                return
            if result is None:
                self.send_json(404, {"error": "program_candidate_not_found"})
            else:
                self.send_json(200, result)
            return
        program_candidate_publication_match = re.fullmatch(
            r"/v1/registry/program-candidate-publications/([0-9a-f]{64})", path
        )
        if program_candidate_publication_match:
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = read_program_candidate_publication_state(
                    program_candidate_publication_match.group(1)
                )
            except Exception:
                self.send_json(
                    503, {"error": "program_candidate_publication_unavailable"}
                )
                return
            if result.get("status") == "not_found":
                self.send_json(
                    404, {"error": "program_candidate_publication_not_found"}
                )
            else:
                self.send_json(200, result)
            return
        source_policy_manifest_match = re.fullmatch(
            r"/v1/registry/source-policy-manifests/"
            r"([a-z][a-z0-9.-]{0,127})/([0-9]+\.[0-9]+\.[0-9]+)",
            path,
        )
        if source_policy_manifest_match:
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                result = read_source_policy_manifest(
                    *source_policy_manifest_match.groups()
                )
            except Exception:
                self.send_json(503, {"error": "source_policy_manifest_unavailable"})
                return
            if result is None:
                self.send_json(404, {"error": "source_policy_manifest_not_found"})
            else:
                self.send_json(200, result)
            return
        source_metadata_match = re.fullmatch(
            r"/v1/tenants/([a-z][a-z0-9-]{0,63})/patterns/"
            r"([a-z][a-z0-9-]{0,63})/([0-9]+\.[0-9]+\.[0-9]+)/sources/"
            r"([a-z][a-z0-9.-]{0,127})/namespaces/"
            r"([a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63})*)",
            path,
        )
        if source_metadata_match:
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                metadata = read_bound_source_metadata(*source_metadata_match.groups())
                if metadata is None:
                    self.send_json(404, {"error": "bound_source_not_found"})
                else:
                    self.send_json(200, metadata)
            except Exception:
                self.send_json(503, {"error": "source_metadata_unavailable"})
            return
        tenant_match = re.fullmatch(
            r"/v1/tenants/([a-z][a-z0-9-]{0,63})/patterns/"
            r"([a-z][a-z0-9-]{0,63})/([0-9]+\.[0-9]+\.[0-9]+)",
            path,
        )
        if tenant_match:
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                tenant = read_tenant_package(*tenant_match.groups())
                if tenant is None:
                    self.send_json(404, {"error": "tenant_not_found"})
                else:
                    self.send_json(200, tenant)
            except Exception:
                self.send_json(503, {"error": "tenant_registry_unavailable"})
            return
        template_match = re.fullmatch(
            r"/v1/pattern-templates/([a-z][a-z0-9-]{0,63})/"
            r"([0-9]+\.[0-9]+\.[0-9]+)",
            path,
        )
        if template_match:
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                package = read_template_package(*template_match.groups())
                if package is None:
                    self.send_json(404, {"error": "template_not_found"})
                else:
                    self.send_json(200, package)
            except Exception:
                self.send_json(503, {"error": "template_registry_unavailable"})
            return
        if path == "/v1/core/routes":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                parameters = parse_qs(
                    urlsplit(self.path).query,
                    keep_blank_values=True,
                    strict_parsing=True,
                )
            except ValueError:
                self.send_json(400, {"error": "invalid_route_identity"})
                return
            if not parameters:
                try:
                    self.send_json(200, read_core_routes())
                except Exception:
                    self.send_json(503, {"error": "route_store_unavailable"})
                return
            if set(parameters) <= {"limit", "cursor"}:
                try:
                    limit, cursor = _parse_inventory_query(self.path)
                    self.send_json(200, read_core_routes(limit, cursor))
                except ValueError:
                    self.send_json(400, {"error": "invalid_inventory_page"})
                except Exception:
                    self.send_json(503, {"error": "route_store_unavailable"})
                return
            required = {"route_id", "operation_id"}
            # Tolerate the old internal Core caller during a staggered rollout, but
            # never use its version value to select or reject the active route.
            allowed = required | {"route_version"}
            if (
                not required.issubset(parameters)
                or not set(parameters) <= allowed
                or any(
                    len(values) != 1 or not values[0] for values in parameters.values()
                )
            ):
                self.send_json(400, {"error": "invalid_route_identity"})
                return
            try:
                route = read_core_route(
                    parameters["route_id"][0],
                    parameters["operation_id"][0],
                )
                if route is None:
                    self.send_json(404, {"error": "route_not_found"})
                else:
                    self.send_json(200, route)
            except Exception:
                self.send_json(503, {"error": "route_store_unavailable"})
            return
        if path == "/v1/ops/route-events":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                parameters = parse_qs(
                    urlsplit(self.path).query,
                    keep_blank_values=True,
                    strict_parsing=True,
                )
                if set(parameters) != {"from_utc", "to_utc"} or any(
                    len(values) != 1 for values in parameters.values()
                ):
                    raise ValueError
                report = read_core_route_report(
                    parameters["from_utc"][0], parameters["to_utc"][0]
                )
                if report is None:
                    self.send_json(400, {"error": "invalid_report_window"})
                else:
                    self.send_json(200, report)
            except ValueError:
                self.send_json(400, {"error": "invalid_report_window"})
            except Exception:
                self.send_json(503, {"error": "route_report_unavailable"})
            return
        if path == "/v1/pattern-catalog/records":
            if not self.authorized():
                self.send_json(401, {"error": "unauthorized"})
                return
            scope = configured_catalog_scope()
            if scope is None:
                self.send_json(503, {"error": "catalog_scope_unconfigured"})
                return
            try:
                self.send_json(200, read_pattern_catalog(scope))
            except Exception:
                self.send_json(503, {"error": "database_unavailable"})
            return
        pattern_catalog_match = re.fullmatch(
            r"/v1/patterns/([a-z][a-z0-9-]{0,63})/catalog-bindings/"
            r"([a-z][a-z0-9-]{0,63})",
            path,
        )
        if pattern_catalog_match:
            if not self.authorized(CATALOG_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                self.send_json(
                    200, read_pattern_cluster_bindings(*pattern_catalog_match.groups())
                )
            except Exception:
                self.send_json(503, {"error": "pattern_catalog_unavailable"})
            return
        registry_prefix = "/v1/registry/use-cases/"
        if path.startswith(registry_prefix):
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            parts = path[len(registry_prefix) :].split("/")
            if (
                len(parts) != 2
                or not USE_CASE_PATTERN.fullmatch(parts[0])
                or not VERSION_PATTERN.fullmatch(parts[1])
            ):
                self.send_json(400, {"error": "invalid_registry_identity"})
                return
            try:
                result = read_active_use_case(parts[0], parts[1])
                if result is None:
                    self.send_json(404, {"error": "not_found"})
                else:
                    self.send_json(200, result)
            except Exception:
                self.send_json(503, {"error": "registry_unavailable"})
            return
        catalog_match = re.fullmatch(
            r"/v1/tenants/([a-z][a-z0-9-]{0,63})/patterns/"
            r"([a-z][a-z0-9-]{0,63})/([0-9]+\.[0-9]+\.[0-9]+)/programs",
            path,
        )
        if catalog_match:
            if not self.authorized(CATALOG_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                programs = read_tenant_programs(*catalog_match.groups())
                self.send_json(
                    200,
                    {
                        "tenant_id": catalog_match.group(1),
                        "cluster_id": catalog_match.group(2),
                        "pattern_version": catalog_match.group(3),
                        "programs": programs,
                    },
                )
            except Exception:
                self.send_json(503, {"error": "catalog_unavailable"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        prefix = "/v1/probes/"
        if path.startswith(prefix):
            try:
                probe_id = str(uuid.UUID(path[len(prefix) :]))
            except (ValueError, TypeError, AttributeError):
                self.send_json(404, {"error": "not_found"})
                return
            try:
                with connect_db() as conn:
                    row = conn.execute(
                        """SELECT probe_id::text, payload, created_at
                        FROM mcp_saas.vm_data_api_probe_records WHERE probe_id=%s""",
                        (probe_id,),
                    ).fetchone()
                if row is None:
                    self.send_json(404, {"error": "not_found"})
                else:
                    self.send_json(
                        200,
                        {
                            "status": "ok",
                            "probe_id": row[0],
                            "payload": row[1],
                            "created_at": row[2].isoformat(),
                        },
                    )
            except Exception:
                self.send_json(503, {"error": "database_unavailable"})
            return
        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        """Route POST requests to their write and evaluation operations."""
        path = urlsplit(self.path).path
        if path == "/v1/registry/schema-projections":
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SCHEMA_PROJECTION_BODY:
                    raise ValueError("invalid_schema_projection")
                result = project_schema_registry(json.loads(self.rfile.read(size)))
            except (ValidationError, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_schema_projection"})
                return
            except Exception:
                self.send_json(503, {"error": "schema_projection_unavailable"})
                return
            self.send_json(200, result)
            return
        package_enrichment_match = re.fullmatch(
            r"/v1/eligibility/requirements-packages/([0-9a-f-]{36})/enrich",
            path,
        )
        if package_enrichment_match:
            if not self.authorized(REQUIREMENTS_PACKAGE_WRITE_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_REQUIREMENTS_PACKAGE_BODY:
                    raise ValueError
                package, revision, digest, changed = enrich_requirements_package(
                    package_enrichment_match.group(1),
                    json.loads(self.rfile.read(size)),
                )
            except RequirementsPackageRevisionConflict as exc:
                self.send_json(
                    409,
                    {
                        "status": "revision_conflict",
                        "current_revision": exc.current_revision,
                    },
                )
                return
            except RequirementsPackageOutOfScope:
                self.send_json(404, {"status": "package_not_found"})
                return
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"status": "invalid_package_enrichment"})
                return
            except Exception as exc:
                sqlstate = getattr(exc, "sqlstate", None) or getattr(
                    exc, "pgcode", None
                )
                if (
                    not isinstance(sqlstate, str)
                    or re.fullmatch(r"[0-9A-Z]{5}", sqlstate) is None
                ):
                    sqlstate = "unavailable"
                print(
                    "data_api_error operation=requirements_package_enrichment "
                    f"error_class={type(exc).__name__} sqlstate={sqlstate}",
                    flush=True,
                )
                self.send_json(
                    503, {"status": "requirements_package_store_unavailable"}
                )
                return
            self.send_json(
                200,
                {
                    "status": "enriched" if changed else "unchanged",
                    "package_id": package["package_id"],
                    "revision": revision,
                    "details_sha256": digest,
                    "package": package,
                },
            )
            return
        scoped_criteria_ruleset_write = re.fullmatch(
            r"/v1/eligibility/clusters/([a-z][a-z0-9-]{0,63})/"
            r"criteria-rulesets",
            path,
        )
        if path == "/v1/eligibility/criteria-rulesets" or scoped_criteria_ruleset_write:
            cluster_id = (
                scoped_criteria_ruleset_write.group(1)
                if scoped_criteria_ruleset_write
                else "eligibility"
            )
            writer_token_file = criteria_ruleset_writer_token_file(cluster_id)
            if writer_token_file is None or not self.authorized(writer_token_file):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_CRITERIA_RULESET_BODY:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError
                if payload.get("cluster_id", cluster_id) != cluster_id:
                    raise ValueError
                payload["cluster_id"] = cluster_id
                with connect_public_catalog_ingest_db() as conn:
                    result = register_criteria_ruleset(conn, payload)
            except InvalidCriteriaRuleset:
                self.send_json(400, {"status": "invalid_criteria_ruleset"})
                return
            except CriteriaRulesetConflict:
                self.send_json(409, {"status": "criteria_ruleset_conflict"})
                return
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"status": "invalid_criteria_ruleset"})
                return
            except Exception:
                self.send_json(503, {"status": "criteria_ruleset_write_unavailable"})
                return
            self.send_json(
                200 if result["status"] in {"duplicate", "replayed"} else 201,
                result,
            )
            return
        if path == "/v1/public-catalog/registrations/preview":
            if not self.authorized(PUBLIC_CATALOG_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SOURCE_REGISTRATION_BODY:
                    raise ValueError
                preview = preview_public_source_registration(
                    json.loads(self.rfile.read(size))
                )
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"error": "invalid_public_source_registration"})
                return
            except ValueError as exc:
                if str(exc) == "dataset_identity_conflict":
                    self.send_json(409, {"status": "dataset_identity_conflict"})
                elif str(exc) == "public_catalog_preview_too_large":
                    self.send_json(413, {"status": "preview_too_large"})
                else:
                    self.send_json(400, {"error": "invalid_public_source_registration"})
                return
            except Exception:
                self.send_json(503, {"error": "public_source_registry_unavailable"})
                return
            if preview is None:
                self.send_json(400, {"error": "invalid_public_source_registration"})
            else:
                self.send_json(200, preview)
            return
        if path == "/v1/public-catalog/registrations/governed":
            if not self.authorized(PUBLIC_CATALOG_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SOURCE_REGISTRATION_BODY + 4096:
                    raise ValueError
                result = apply_governed_public_source_registration(
                    json.loads(self.rfile.read(size))
                )
            except ValueError as exc:
                if str(exc) in {
                    "dataset_identity_conflict",
                    "public_catalog_proposal_conflict",
                    "public_catalog_transaction_conflict",
                }:
                    self.send_json(409, {"status": str(exc)})
                else:
                    self.send_json(400, {"error": "invalid_governed_registration"})
                return
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"error": "invalid_governed_registration"})
                return
            except Exception:
                self.send_json(503, {"error": "public_source_registry_unavailable"})
                return
            if result is None:
                self.send_json(400, {"error": "invalid_governed_registration"})
            elif result.get("status") == "stale_before_state":
                self.send_json(409, result)
            else:
                self.send_json(200, result)
            return
        if path in {
            "/v1/public-catalog/registrations/validate",
            "/v1/public-catalog/registrations",
        }:
            if not self.authorized(PUBLIC_CATALOG_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SOURCE_REGISTRATION_BODY:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                if path.endswith("/validate"):
                    result = validate_public_source_registration(payload)
                    if result is None:
                        self.send_json(
                            400, {"error": "invalid_public_source_registration"}
                        )
                    else:
                        self.send_json(200, result)
                else:
                    result = register_public_source(payload)
                    if result is None:
                        self.send_json(422, {"error": "source_schema_not_active"})
                    else:
                        self.send_json(
                            201 if result["status"] == "registered" else 200,
                            result,
                        )
            except ValueError as exc:
                if str(exc) == "dataset_identity_conflict":
                    self.send_json(409, {"error": "public_dataset_identity_conflict"})
                else:
                    self.send_json(400, {"error": "invalid_public_source_registration"})
            except (json.JSONDecodeError, UnicodeDecodeError):
                self.send_json(400, {"error": "invalid_public_source_registration"})
            except Exception:
                self.send_json(503, {"error": "public_source_registry_unavailable"})
            return
        if path == "/v1/registry/source-policy-activations":
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SOURCE_POLICY_ACTIVATION_BODY:
                    raise ValueError
                result, created = record_source_policy_activation(
                    json.loads(self.rfile.read(size))
                )
            except SourcePolicyManifestNotFound:
                self.send_json(404, {"error": "source_policy_manifest_not_found"})
                return
            except SourcePolicyActivationConflict:
                self.send_json(409, {"error": "source_policy_activation_conflict"})
                return
            except (InvalidSourcePolicyManifest, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_source_policy_activation"})
                return
            except Exception:
                self.send_json(503, {"error": "source_policy_activation_unavailable"})
                return
            self.send_json(201 if created else 200, result)
            return
        source_policy_decision_match = re.fullmatch(
            r"/v1/registry/source-policy-manifests/"
            r"([a-z][a-z0-9.-]{0,127})/([0-9]+\.[0-9]+\.[0-9]+)/decision",
            path,
        )
        if source_policy_decision_match:
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SOURCE_POLICY_MANIFEST_BODY:
                    raise ValueError
                result, created = record_source_policy_decision(
                    *source_policy_decision_match.groups(),
                    json.loads(self.rfile.read(size)),
                )
            except SourcePolicyManifestNotFound:
                self.send_json(404, {"error": "source_policy_manifest_not_found"})
                return
            except SourcePolicyDecisionConflict:
                self.send_json(409, {"error": "source_policy_decision_conflict"})
                return
            except (InvalidSourcePolicyManifest, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_source_policy_decision"})
                return
            except Exception:
                self.send_json(503, {"error": "source_policy_decision_unavailable"})
                return
            result["idempotent_replay"] = not created
            self.send_json(201 if created else 200, result)
            return
        program_candidate_decision_match = re.fullmatch(
            r"/v1/registry/program-candidates/"
            r"(candidate-[0-9a-f]{32})/decision",
            path,
        )
        if program_candidate_decision_match:
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_PROGRAM_CANDIDATE_BODY:
                    raise ValueError
                result, created = record_program_candidate_decision(
                    program_candidate_decision_match.group(1),
                    json.loads(self.rfile.read(size)),
                )
            except ProgramCandidateNotFound:
                self.send_json(404, {"error": "program_candidate_not_found"})
                return
            except ProgramCandidateDecisionConflict:
                self.send_json(409, {"error": "program_candidate_decision_conflict"})
                return
            except (InvalidProgramCandidate, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_program_candidate_decision"})
                return
            except Exception:
                self.send_json(503, {"error": "program_candidate_decision_unavailable"})
                return
            result["idempotent_replay"] = not created
            self.send_json(201 if created else 200, result)
            return
        program_candidate_publish_match = re.fullmatch(
            r"/v1/registry/program-candidates/"
            r"(candidate-[0-9a-f]{32})/publish",
            path,
        )
        if program_candidate_publish_match:
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ValueError
                result, created = publish_program_candidate(
                    program_candidate_publish_match.group(1),
                    json.loads(self.rfile.read(size)),
                )
            except ProgramCandidateNotFound:
                self.send_json(404, {"error": "program_candidate_or_dataset_not_found"})
                return
            except ProgramCandidatePublicationConflict as exc:
                self.send_json(
                    409,
                    {
                        "error": "program_candidate_publication_conflict",
                        "reason_code": exc.reason_code,
                    },
                )
                return
            except (
                InvalidProgramCandidatePublication,
                ValueError,
                json.JSONDecodeError,
            ):
                self.send_json(400, {"error": "invalid_program_candidate_publication"})
                return
            except Exception:
                self.send_json(
                    503, {"error": "program_candidate_publication_unavailable"}
                )
                return
            self.send_json(201 if created else 200, result)
            return
        program_candidate_publication_lifecycle_match = re.fullmatch(
            r"/v1/registry/program-candidate-publications/"
            r"([0-9a-f]{64})/lifecycle",
            path,
        )
        if program_candidate_publication_lifecycle_match:
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SOURCE_POLICY_ACTIVATION_BODY:
                    raise ValueError
                result, created = record_program_candidate_publication_lifecycle(
                    program_candidate_publication_lifecycle_match.group(1),
                    json.loads(self.rfile.read(size)),
                )
            except ProgramCandidateNotFound:
                self.send_json(
                    404, {"error": "program_candidate_publication_not_found"}
                )
                return
            except ProgramCandidatePublicationConflict:
                self.send_json(409, {"error": "program_candidate_publication_conflict"})
                return
            except (
                InvalidProgramCandidatePublication,
                ValueError,
                json.JSONDecodeError,
            ):
                self.send_json(
                    400, {"error": "invalid_program_candidate_publication_lifecycle"}
                )
                return
            except Exception:
                self.send_json(
                    503,
                    {"error": "program_candidate_publication_lifecycle_unavailable"},
                )
                return
            self.send_json(201 if created else 200, result)
            return
        if path == "/v1/registry/source-policy-manifests/validate":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SOURCE_POLICY_MANIFEST_BODY:
                    raise ValueError
                manifest = validate_source_policy_manifest(
                    json.loads(self.rfile.read(size))
                )
                digest = source_policy_manifest_sha256(manifest)
            except (InvalidSourcePolicyManifest, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_source_policy_manifest"})
                return
            self.send_json(
                200,
                {
                    "status": "valid",
                    "content_sha256": digest,
                    "review_status": manifest["review_status"],
                    "manifest": manifest,
                },
            )
            return
        if path == "/v1/registry/program-candidates/validate":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_PROGRAM_CANDIDATE_BODY:
                    raise ValueError
                candidate = validate_program_candidate(
                    json.loads(self.rfile.read(size))
                )
                digest = program_candidate_sha256(candidate)
            except (InvalidProgramCandidate, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_program_candidate"})
                return
            self.send_json(
                200,
                {
                    "status": "valid",
                    "candidate_id": candidate["candidate_id"],
                    "content_sha256": digest,
                    "review_status": "review_required",
                    "catalog_status": "not_published",
                    "candidate": candidate,
                },
            )
            return
        if path == "/v1/registry/program-candidates":
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_PROGRAM_CANDIDATE_BODY:
                    raise ValueError
                result, created = register_program_candidate_record(
                    json.loads(self.rfile.read(size))
                )
            except ProgramCandidateConflict:
                self.send_json(409, {"error": "immutable_program_candidate_conflict"})
                return
            except (InvalidProgramCandidate, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_program_candidate"})
                return
            except Exception:
                self.send_json(503, {"error": "program_candidate_store_unavailable"})
                return
            self.send_json(201 if created else 200, result)
            return
        if path == "/v1/registry/source-policy-manifests":
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_SOURCE_POLICY_MANIFEST_BODY:
                    raise ValueError
                result, created = register_source_policy_manifest(
                    json.loads(self.rfile.read(size))
                )
            except SourcePolicyManifestConflict:
                self.send_json(409, {"error": "immutable_source_policy_conflict"})
                return
            except (InvalidSourcePolicyManifest, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_source_policy_manifest"})
                return
            except Exception:
                self.send_json(
                    503, {"error": "source_policy_manifest_store_unavailable"}
                )
                return
            self.send_json(201 if created else 200, result)
            return
        if path == "/v1/eligibility/requirements-packages":
            if not self.authorized(REQUIREMENTS_PACKAGE_WRITE_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_REQUIREMENTS_PACKAGE_BODY:
                    raise ValueError
                package, created = write_requirements_package(
                    json.loads(self.rfile.read(size))
                )
            except TemplatePackageConflict:
                self.send_json(409, {"status": "immutable_package_conflict"})
                return
            except RequirementsPackageOutOfScope:
                self.send_json(409, {"status": "package_tenant_out_of_scope"})
                return
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"status": "invalid_requirements_package"})
                return
            except Exception:
                self.send_json(
                    503, {"status": "requirements_package_store_unavailable"}
                )
                return
            self.send_json(
                201 if created else 200,
                {
                    "status": "stored",
                    "package_id": package["package_id"],
                    "created": created,
                    "retrieval_path": (
                        f"/v1/eligibility/requirements-packages/{package['package_id']}"
                    ),
                },
            )
            return
        if path in {
            "/v1/profile-resume-claims",
            "/v1/profile-sessions/resume",
            "/v1/profile-sessions/current/resume-claims/revoke",
        }:
            if not self.authorized(PROFILE_WRITE_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_PROFILE_BODY:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                if (
                    not isinstance(payload, dict)
                    or payload.get("confirmed") is not True
                ):
                    self.send_json(400, {"status": "confirmation_required"})
                    return
                profile_scope = self.headers.get("X-Profile-Scope", "")
                if path == "/v1/profile-resume-claims":
                    if set(payload) != {"confirmed"}:
                        raise ValueError
                    profile_ref = self.headers.get("X-Profile-Reference", "")
                    if not profile_ref or not profile_scope:
                        self.send_json(400, {"status": "profile_scope_required"})
                        return
                    result = create_profile_resume_claim(profile_ref, profile_scope)
                    status_code = {
                        "available": 201,
                        "profile_not_found_in_scope": 404,
                    }.get(str(result.get("status")), 503)
                elif path == "/v1/profile-sessions/resume":
                    if set(payload) != {"confirmed", "resume_claim"}:
                        raise ValueError
                    claim = payload.get("resume_claim")
                    if not isinstance(claim, str) or not profile_scope:
                        raise ValueError
                    result = resume_synthetic_profile(claim, profile_scope)
                    status_code = {
                        "available": 201,
                        "profile_resume_unavailable": 404,
                    }.get(str(result.get("status")), 503)
                else:
                    if set(payload) != {"confirmed"}:
                        raise ValueError
                    profile_ref = self.headers.get("X-Profile-Reference", "")
                    if not profile_ref or not profile_scope:
                        self.send_json(400, {"status": "profile_scope_required"})
                        return
                    result = revoke_profile_resume_claims(profile_ref, profile_scope)
                    status_code = {
                        "available": 200,
                        "profile_not_found_in_scope": 404,
                    }.get(str(result.get("status")), 503)
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"status": "invalid_profile_resume_request"})
                return
            except Exception:
                self.send_json(503, {"status": "profile_resume_unavailable"})
                return
            self.send_json(status_code, result)
            return
        if path == "/v1/profiles":
            if not self.authorized(PROFILE_WRITE_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_PROFILE_BODY:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                is_fixture = isinstance(payload, dict) and "command" in payload
                profile_scope = self.headers.get("X-Profile-Scope")
                if not is_fixture and not profile_scope:
                    self.send_json(400, {"status": "profile_scope_required"})
                    return
                profile, result = create_synthetic_profile(
                    payload, profile_scope_sha256=profile_scope
                )
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"status": "invalid_profile"})
                return
            except Exception:
                self.send_json(503, {"status": "profile_store_unavailable"})
                return
            status_code = {
                "ok": 201,
                "invalid_profile": 400,
                "profile_scope_required": 400,
                "invalid_or_inactive_profile_schema": 422,
                "conflict": 409,
            }.get(result, 503)
            self.send_json(status_code, profile or {"status": result})
            return
        if path == "/v1/profile-sessions/current/interests":
            if not self.authorized(PROFILE_WRITE_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            profile_ref = self.headers.get("X-Profile-Reference", "")
            profile_scope = self.headers.get("X-Profile-Scope", "")
            if not profile_ref or not profile_scope:
                self.send_json(400, {"status": "profile_scope_required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_PROFILE_BODY:
                    raise ValueError
                profile, result = save_scoped_synthetic_profile_interest(
                    json.loads(self.rfile.read(size)),
                    profile_ref=profile_ref,
                    profile_scope_sha256=profile_scope,
                )
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"status": "invalid_interest"})
                return
            except Exception:
                self.send_json(503, {"status": "profile_interest_unavailable"})
                return
            status_code = {
                "ok": 201,
                "invalid_interest": 400,
                "profile_not_found_in_scope": 404,
                "profile_reference_expired": 410,
                "revision_conflict": 409,
                "source_policy_changed": 409,
                "source_not_approved": 422,
                "source_policy_unavailable": 503,
            }.get(result, 503)
            self.send_json(status_code, profile or {"status": result})
            return
        if path == "/v1/profile-sessions/current/revisions":
            if not self.authorized(PROFILE_WRITE_TOKEN_FILE):
                self.send_json(401, {"status": "unauthorized"})
                return
            profile_ref = self.headers.get("X-Profile-Reference", "")
            profile_scope = self.headers.get("X-Profile-Scope", "")
            if not profile_ref or not profile_scope:
                self.send_json(400, {"status": "profile_scope_required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_PROFILE_BODY:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict) or "profile_id" in payload:
                    self.send_json(400, {"status": "invalid_profile"})
                    return
                profile, result = update_synthetic_profile(
                    payload,
                    profile_ref=profile_ref,
                    profile_scope_sha256=profile_scope,
                )
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"status": "invalid_profile"})
                return
            except Exception:
                self.send_json(503, {"status": "profile_store_unavailable"})
                return
            status_code = {
                "ok": 200,
                "invalid_profile": 400,
                "profile_scope_required": 400,
                "profile_not_found_in_scope": 404,
                "profile_reference_expired": 410,
                "invalid_or_inactive_profile_schema": 422,
                "profile_not_found": 404,
                "profile_scope_rejected": 403,
                "profile_schema_migration_required": 409,
                "conflict": 409,
            }.get(result, 503)
            self.send_json(status_code, profile or {"status": result})
            return
        if path == "/v1/ops/route-events":
            if not self.authorized(ROUTE_TELEMETRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_ROUTE_EVENT_BODY:
                    raise ValueError
                event = valid_route_event(json.loads(self.rfile.read(size)))
                if event is None:
                    raise ValueError
                result = record_core_route_event(event)
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_route_event"})
                return
            except Exception:
                self.send_json(503, {"error": "route_event_write_unavailable"})
                return
            if result is None:
                self.send_json(400, {"error": "invalid_route_event"})
            else:
                self.send_json(201, result)
            return
        if path in {
            "/v1/registry/tenants/validate",
            "/v1/registry/tenants",
        }:
            token_file = (
                REGISTRY_READ_TOKEN_FILE
                if path.endswith("/validate")
                else TENANT_REGISTRY_WRITE_TOKEN_FILE
            )
            if not self.authorized(token_file):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ValueError
                spec = json.loads(self.rfile.read(size))
                if path.endswith("/validate"):
                    result = validate_tenant_package_in_registry(spec)
                else:
                    result = register_tenant_package(spec)
            except InvalidTenantPackage as exc:
                self.send_json(
                    409, {"error": "tenant_spec_not_compatible", "detail": str(exc)}
                )
                return
            except TemplatePackageConflict:
                self.send_json(409, {"error": "tenant_identity_conflict"})
                return
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_tenant_package"})
                return
            except Exception:
                self.send_json(503, {"error": "tenant_registry_unavailable"})
                return
            self.send_json(200, result)
            return
        tenant_activate_match = re.fullmatch(
            r"/v1/registry/tenants/([a-z][a-z0-9-]{0,63})/patterns/"
            r"([a-z][a-z0-9-]{0,63})/([0-9]+\.[0-9]+\.[0-9]+)/activate",
            path,
        )
        if tenant_activate_match:
            if not self.authorized(TENANT_REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ValueError
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict) or set(body) != {
                    "approved_tenant_spec_sha256",
                    "expected_before_sha256",
                    "transaction_id",
                    "approval_reference",
                    "operator_audit_context",
                }:
                    raise ValueError
                result = set_tenant_package_state_governed(
                    *tenant_activate_match.groups(),
                    action="activate",
                    **body,
                )
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_tenant_activation"})
                return
            except TenantLifecycleConflict as exc:
                self.send_json(409, {"error": exc.reason})
                return
            except Exception:
                self.send_json(503, {"error": "tenant_registry_unavailable"})
                return
            if result is None:
                self.send_json(409, {"error": "tenant_activation_rejected"})
            else:
                self.send_json(200, result)
            return
        tenant_deactivate_match = re.fullmatch(
            r"/v1/registry/tenants/([a-z][a-z0-9-]{0,63})/patterns/"
            r"([a-z][a-z0-9-]{0,63})/([0-9]+\.[0-9]+\.[0-9]+)/deactivate",
            path,
        )
        if tenant_deactivate_match:
            if not self.authorized(TENANT_REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ValueError
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict) or set(body) != {
                    "approved_tenant_spec_sha256",
                    "expected_before_sha256",
                    "transaction_id",
                    "approval_reference",
                    "operator_audit_context",
                }:
                    raise ValueError
                result = set_tenant_package_state_governed(
                    *tenant_deactivate_match.groups(),
                    action="deactivate",
                    **body,
                )
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_tenant_deactivation"})
                return
            except TenantLifecycleConflict as exc:
                self.send_json(409, {"error": exc.reason})
                return
            except Exception:
                self.send_json(503, {"error": "tenant_registry_unavailable"})
                return
            if result is None:
                self.send_json(409, {"error": "tenant_deactivation_rejected"})
            else:
                self.send_json(200, result)
            return
        if path == "/v1/registry/pattern-catalog-bindings":
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ValueError
                binding = valid_pattern_catalog_binding(
                    json.loads(self.rfile.read(size))
                )
                if binding is None:
                    raise ValueError
                result = register_pattern_catalog_binding(binding)
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_pattern_catalog_binding"})
                return
            except Exception:
                self.send_json(503, {"error": "pattern_catalog_binding_unavailable"})
                return
            if result is None:
                self.send_json(409, {"error": "pattern_or_dataset_not_approved"})
            else:
                self.send_json(200, result)
            return
        if path in {
            "/v1/public-catalog/record-jurisdiction/preview",
            "/v1/public-catalog/record-jurisdiction/governed",
        }:
            if not self.authorized(PUBLIC_CATALOG_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                if path.endswith("/preview"):
                    result = preview_public_catalog_record_jurisdiction(payload)
                    if result is None:
                        self.send_json(
                            422, {"error": "public_catalog_record_metadata_rejected"}
                        )
                        return
                else:
                    result = apply_governed_public_catalog_record_jurisdiction(payload)
                    if result is None:
                        self.send_json(
                            422, {"error": "public_catalog_record_metadata_rejected"}
                        )
                        return
                    if result.get("status") == "stale_before_state":
                        self.send_json(409, result)
                        return
            except ValueError as exc:
                reason = str(exc)
                if reason not in {
                    "public_catalog_metadata_transaction_conflict",
                    "public_catalog_proposal_conflict",
                    "public_catalog_record_compare_and_set_conflict",
                    "published_program_record_is_immutable",
                }:
                    self.send_json(
                        400, {"error": "invalid_public_catalog_record_metadata"}
                    )
                    return
                self.send_json(409, {"error": reason})
                return
            except (json.JSONDecodeError, TypeError):
                self.send_json(400, {"error": "invalid_public_catalog_record_metadata"})
                return
            except Exception:
                self.send_json(503, {"error": "public_catalog_write_unavailable"})
                return
            self.send_json(200, result)
            return
        public_ingest_match = re.fullmatch(
            r"/v1/public-datasets/([a-z][a-z0-9-]{0,63})/ingestion-runs"
            r"(?P<validate>/validate)?",
            path,
        )
        if public_ingest_match:
            if not self.authorized(PUBLIC_CATALOG_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_PUBLIC_CATALOG_INGEST_BODY:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                if public_ingest_match.group("validate"):
                    if valid_public_catalog_ingestion(payload) is None:
                        raise ValueError
                    result = validate_public_dataset_ingestion(
                        public_ingest_match.group(1), payload
                    )
                    if result is None:
                        self.send_json(
                            422, {"error": "public_catalog_ingestion_rejected"}
                        )
                    else:
                        self.send_json(200, result)
                    return
                if valid_public_catalog_ingestion(payload) is None:
                    raise ValueError
                result = ingest_public_dataset(public_ingest_match.group(1), payload)
                if result is None:
                    self.send_json(404, {"error": "approved_dataset_not_found"})
                else:
                    self.send_json(201, result)
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_public_catalog_ingestion"})
            except Exception:
                self.send_json(503, {"error": "public_catalog_write_unavailable"})
            return
        if path == "/v1/registry/pattern-templates":
            if not self.authorized(TEMPLATE_REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_TEMPLATE_PACKAGE_BODY:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                package, created = register_template_package(payload)
            except IncompatibleTemplatePackage:
                self.send_json(409, {"error": "incompatible_core_contract"})
                return
            except InvalidTemplatePackage:
                self.send_json(400, {"error": "invalid_template_package"})
                return
            except TemplatePackageConflict:
                self.send_json(409, {"error": "immutable_template_conflict"})
                return
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_template_package"})
                return
            except Exception:
                self.send_json(503, {"error": "template_registry_unavailable"})
                return
            self.send_json(201 if created else 200, package)
            return

        if path == "/v1/registry/pattern-templates/validate":
            if not self.authorized(REGISTRY_READ_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_TEMPLATE_PACKAGE_BODY:
                    raise ValueError
                definition = validate_template_package(
                    json.loads(self.rfile.read(size))
                )
            except IncompatibleTemplatePackage:
                self.send_json(409, {"error": "incompatible_core_contract"})
                return
            except (InvalidTemplatePackage, ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_template_package"})
                return
            canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"))
            digest = hashlib.sha256(canonical.encode()).hexdigest()
            self.send_json(
                200,
                {
                    "status": "valid",
                    "descriptor_sha256": digest,
                    "definition": definition,
                },
            )
            return

        template_action = re.fullmatch(
            r"/v1/registry/pattern-templates/([a-z][a-z0-9-]{0,63})/"
            r"([0-9]+\.[0-9]+\.[0-9]+)/(?:(activate|deactivate)|operations/"
            r"([a-z][a-z0-9.-]{0,127})/route)",
            path,
        )
        if template_action:
            if not self.authorized(TEMPLATE_REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            template_id, template_version, action, operation_id = (
                template_action.groups()
            )
            if action:
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if size < 1 or size > 4096:
                        raise ValueError
                    payload = json.loads(self.rfile.read(size))
                    required = {
                        "expected_before_sha256",
                        "transaction_id",
                        "approval_reference",
                        "operator_audit_context",
                    }
                    if not isinstance(payload, dict) or set(payload) != required:
                        raise ValueError
                    if any(
                        not isinstance(payload[field], str)
                        for field in (
                            "expected_before_sha256",
                            "transaction_id",
                            "approval_reference",
                        )
                    ):
                        raise ValueError
                    if not isinstance(payload["operator_audit_context"], dict):
                        raise ValueError
                    lifecycle_action = (
                        activate_template_package_governed
                        if action == "activate"
                        else deactivate_template_package_governed
                    )
                    package = lifecycle_action(
                        template_id,
                        template_version,
                        expected_before_sha256=payload["expected_before_sha256"],
                        transaction_id=payload["transaction_id"],
                        approval_reference=payload["approval_reference"],
                        operator_audit_context=payload["operator_audit_context"],
                    )
                except TemplateLifecycleConflict as exc:
                    self.send_json(
                        409,
                        {
                            "error": exc.reason
                            if action == "deactivate"
                            else "template_before_state_conflict"
                        },
                    )
                    return
                except (ValueError, json.JSONDecodeError):
                    self.send_json(400, {"error": "invalid_template_lifecycle_request"})
                    return
                except Exception:
                    self.send_json(503, {"error": "template_registry_unavailable"})
                    return
                if package is None:
                    self.send_json(404, {"error": "template_not_found"})
                else:
                    self.send_json(200, package)
                return

            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 2048:
                    raise ValueError
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict) or set(payload) != {"endpoint"}:
                    raise ValueError
                endpoint = payload["endpoint"]
                if not valid_handler_endpoint(endpoint):
                    raise ValueError
                route, created = bind_template_operation(
                    template_id, template_version, operation_id, endpoint
                )
            except TemplatePackageNotFound:
                self.send_json(404, {"error": "active_template_operation_not_found"})
                return
            except TemplatePackageConflict:
                self.send_json(409, {"error": "immutable_route_binding_conflict"})
                return
            except (ValueError, json.JSONDecodeError):
                self.send_json(400, {"error": "invalid_template_route"})
                return
            except Exception:
                self.send_json(503, {"error": "template_registry_unavailable"})
                return
            self.send_json(201 if created else 200, route)
            return

        if path == "/v1/registry/tenant-endpoint":
            if not self.authorized(REGISTRY_WRITE_TOKEN_FILE):
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > MAX_BODY:
                    raise ValueError
                registration = valid_tenant_endpoint(json.loads(self.rfile.read(size)))
            except (ValueError, json.JSONDecodeError):
                registration = None
            if registration is None:
                self.send_json(400, {"error": "invalid_tenant_endpoint"})
                return
            tenant_id, pattern_id, pattern_version, endpoint = registration
            try:
                with connect_db() as conn:
                    row = conn.execute(
                        """UPDATE mcp_saas.tenant_registry AS tenant
                           SET mcp_endpoint = %s, updated_at = now()
                           WHERE tenant.tenant_id = %s
                             AND tenant.pattern_id = %s
                             AND tenant.pattern_version = %s
                             AND tenant.lifecycle_state = 'active'
                             AND EXISTS (
                               SELECT 1
                               FROM mcp_saas.use_case_tenants AS eligible
                               JOIN mcp_saas.use_case_registry AS use_case
                                 ON use_case.use_case_id = eligible.use_case_id
                                AND use_case.use_case_version = (
                                    eligible.use_case_version
                                )
                               WHERE eligible.tenant_id = tenant.tenant_id
                                 AND eligible.pattern_id = tenant.pattern_id
                                 AND eligible.pattern_version = tenant.pattern_version
                                 AND use_case.lifecycle_state = 'active'
                             )
                           RETURNING tenant.tenant_id""",
                        (endpoint, tenant_id, pattern_id, pattern_version),
                    ).fetchone()
                if row is None:
                    self.send_json(404, {"error": "active_registration_not_found"})
                else:
                    self.send_json(
                        200,
                        {
                            "status": "ok",
                            "tenant_id": tenant_id,
                            "pattern_id": pattern_id,
                            "pattern_version": pattern_version,
                        },
                    )
            except Exception:
                self.send_json(503, {"error": "registry_unavailable"})
            return
        if path != "/v1/probes":
            self.send_json(404, {"error": "not_found"})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > MAX_BODY:
                raise ValueError
            parsed = valid_probe(json.loads(self.rfile.read(size)))
        except (ValueError, json.JSONDecodeError):
            parsed = None
        if parsed is None:
            self.send_json(400, {"error": "invalid_synthetic_probe"})
            return
        probe_id, payload = parsed
        try:
            with connect_db() as conn:
                conn.execute(
                    """INSERT INTO mcp_saas.vm_data_api_probe_records (probe_id,payload)
                        VALUES (%s,%s::jsonb) ON CONFLICT (probe_id) DO NOTHING""",
                    (probe_id, json.dumps(payload)),
                )
                row = conn.execute(
                    """SELECT probe_id::text,payload,created_at
                        FROM mcp_saas.vm_data_api_probe_records WHERE probe_id=%s""",
                    (probe_id,),
                ).fetchone()
            self.send_json(
                201,
                {
                    "status": "ok",
                    "probe_id": row[0],
                    "payload": row[1],
                    "created_at": row[2].isoformat(),
                },
            )
        except Exception:
            self.send_json(503, {"error": "database_unavailable"})


def main() -> None:
    """Start the Persistence Access API server."""
    if not read_secret(TOKEN_FILE):
        raise RuntimeError("API bearer secret is empty")
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    print("data-api listening on container port 8080", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
