"""Typed validation and canonical digests for immutable source-policy candidates."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from operator_audit import validate_operator_audit_context

MANIFEST_ID = re.compile(r"[a-z][a-z0-9.-]{0,127}\Z")
SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
FIELD_ID = re.compile(r"[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)+\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
AUTHORITY_TYPES = {"federal", "state", "local", "tribal", "institutional"}
JURISDICTION_LEVELS = AUTHORITY_TYPES
PERMITTED_USES = {
    "source_discovery",
    "program_listing",
    "eligibility_criteria",
    "application_guidance",
    "opportunity_discovery",
    "compliance_guidance",
}
DATA_TYPES = {"string", "number", "boolean", "date", "array", "object"}
REVIEW_STATUS = "pending_review"


class InvalidSourcePolicyManifest(ValueError):
    """A source-policy candidate has a malformed or unsafe contract."""


class SourcePolicyManifestConflict(RuntimeError):
    """A manifest identity already exists with different immutable content."""


class SourcePolicyDecisionConflict(RuntimeError):
    """A manifest digest already has a different immutable review decision."""


class SourcePolicyManifestNotFound(LookupError):
    """A review decision refers to a candidate that is not registered."""


class SourcePolicyActivationConflict(RuntimeError):
    """A lifecycle request conflicts with immutable approval or active state."""


def _nonempty_text(value: object, maximum: int = 512) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and value == value.strip()
    )


def _is_choice(value: object, choices: set[str]) -> bool:
    return isinstance(value, str) and value in choices


def _string_list(value: object, allowed: set[str] | None = None) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            _nonempty_text(item, 128) and (allowed is None or item in allowed)
            for item in value
        )
        and len(value) == len(set(value))
    )


def _https_url(value: object) -> bool:
    if not _nonempty_text(value, 2048):
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and parsed.port in (None, 443)
            and not parsed.fragment
        )
    except ValueError:
        return False


def _utc_timestamp(value: object) -> bool:
    if not _nonempty_text(value, 40):
        return False
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        timestamp = datetime.fromisoformat(normalized)
        return (
            timestamp.tzinfo is not None
            and timestamp.utcoffset() == timezone.utc.utcoffset(None)
        )
    except (TypeError, ValueError):
        return False


def _valid_host_path_scope(value: object, canonical_url: str) -> bool:
    if not isinstance(value, dict) or set(value) != {"host", "path_prefixes"}:
        return False
    host = value["host"]
    try:
        parsed = urlsplit(f"https://{host}") if isinstance(host, str) else None
        labels = host.split(".") if isinstance(host, str) else []
        host_valid = (
            parsed is not None
            and parsed.hostname == host
            and len(host) <= 253
            and len(labels) > 1
            and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            )
        )
        source_host = urlsplit(canonical_url).hostname
        source_path = urlsplit(canonical_url).path or "/"
    except ValueError:
        return False
    prefixes = value["path_prefixes"]
    return (
        host_valid
        and source_host == host
        and isinstance(prefixes, list)
        and bool(prefixes)
        and all(isinstance(prefix, str) for prefix in prefixes)
        and len(prefixes) == len(set(prefixes))
        and all(
            isinstance(prefix, str)
            and prefix.startswith("/")
            and "?" not in prefix
            and "#" not in prefix
            and "\\" not in prefix
            and all(part not in {".", ".."} for part in prefix.split("/"))
            for prefix in prefixes
        )
        and any(
            source_path == prefix
            or prefix == "/"
            or prefix.endswith("/")
            and source_path.startswith(prefix)
            or source_path.startswith(prefix + "/")
            for prefix in prefixes
        )
    )


def validate_source_policy_manifest(payload: object) -> dict[str, object]:
    """Validate a review-pending candidate; approval is a later immutable record."""
    if (
        isinstance(payload, dict)
        and payload.get("manifest_kind") == "public_search_policy_bundle"
    ):
        return _validate_public_search_policy_bundle(payload)

    required = {
        "manifest_id",
        "manifest_version",
        "candidate_id",
        "source",
        "normalized_fields",
        "criteria_semantics",
        "intended_effect",
        "verification_evidence",
        "review_status",
        "approval",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise InvalidSourcePolicyManifest("Manifest fields do not match the contract.")

    manifest_id = payload["manifest_id"]
    version = payload["manifest_version"]
    candidate_id = payload["candidate_id"]
    if not isinstance(manifest_id, str) or not MANIFEST_ID.fullmatch(manifest_id):
        raise InvalidSourcePolicyManifest("Manifest identity is invalid.")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise InvalidSourcePolicyManifest(
            "Manifest version must use semantic versioning."
        )
    if not isinstance(candidate_id, str) or not MANIFEST_ID.fullmatch(candidate_id):
        raise InvalidSourcePolicyManifest("Candidate identity is invalid.")

    source = payload["source"]
    source_fields = {
        "source_id",
        "name",
        "canonical_url",
        "authority",
        "jurisdiction",
        "host_path_scope",
        "permitted_use",
    }
    if not isinstance(source, dict) or set(source) != source_fields:
        raise InvalidSourcePolicyManifest("Source scope does not match the contract.")
    if not isinstance(source["source_id"], str) or not MANIFEST_ID.fullmatch(
        source["source_id"]
    ):
        raise InvalidSourcePolicyManifest("Source identity is invalid.")
    if not _nonempty_text(source["name"]):
        raise InvalidSourcePolicyManifest("Source name is invalid.")
    if not _https_url(source["canonical_url"]):
        raise InvalidSourcePolicyManifest("Canonical source URL must use HTTPS.")
    authority = source["authority"]
    if (
        not isinstance(authority, dict)
        or set(authority) != {"type", "name"}
        or not _is_choice(authority["type"], AUTHORITY_TYPES)
        or not _nonempty_text(authority["name"])
    ):
        raise InvalidSourcePolicyManifest("Authority metadata is invalid.")
    jurisdiction = source["jurisdiction"]
    if (
        not isinstance(jurisdiction, dict)
        or set(jurisdiction) != {"level", "code"}
        or not _is_choice(jurisdiction["level"], JURISDICTION_LEVELS)
        or not _nonempty_text(jurisdiction["code"], 32)
    ):
        raise InvalidSourcePolicyManifest("Jurisdiction metadata is invalid.")
    if not _valid_host_path_scope(source["host_path_scope"], source["canonical_url"]):
        raise InvalidSourcePolicyManifest("Host and path scope is invalid.")
    if not _string_list(source["permitted_use"], PERMITTED_USES):
        raise InvalidSourcePolicyManifest("Permitted-use values are invalid.")

    fields = payload["normalized_fields"]
    if not isinstance(fields, list) or not fields:
        raise InvalidSourcePolicyManifest("Normalized fields must be non-empty.")
    seen_field_ids: set[str] = set()
    for item in fields:
        if not isinstance(item, dict) or set(item) != {
            "field_id",
            "source_field",
            "data_type",
            "mapping_status",
        }:
            raise InvalidSourcePolicyManifest("Normalized field shape is invalid.")
        field_id = item["field_id"]
        if (
            not isinstance(field_id, str)
            or not FIELD_ID.fullmatch(field_id)
            or field_id in seen_field_ids
            or not _nonempty_text(item["source_field"], 256)
            or not _is_choice(item["data_type"], DATA_TYPES)
            or not _is_choice(
                item["mapping_status"], {"mapped", "unmapped", "not_assessed"}
            )
        ):
            raise InvalidSourcePolicyManifest("Normalized field metadata is invalid.")
        seen_field_ids.add(field_id)

    criteria = payload["criteria_semantics"]
    if criteria is not None:
        if not isinstance(criteria, list):
            raise InvalidSourcePolicyManifest(
                "Criteria semantics must be a list or null."
            )
        seen_criteria: set[str] = set()
        for item in criteria:
            if not isinstance(item, dict) or set(item) != {
                "criterion_id",
                "description",
                "source_field",
                "interpretation_status",
            }:
                raise InvalidSourcePolicyManifest("Criteria semantic shape is invalid.")
            criterion_id = item["criterion_id"]
            if (
                not isinstance(criterion_id, str)
                or not FIELD_ID.fullmatch(criterion_id)
                or criterion_id in seen_criteria
                or not _nonempty_text(item["description"], 2048)
                or not _nonempty_text(item["source_field"], 256)
                or not _is_choice(
                    item["interpretation_status"],
                    {"mapped", "unmapped", "not_assessed"},
                )
            ):
                raise InvalidSourcePolicyManifest(
                    "Criteria semantic values are invalid."
                )
            seen_criteria.add(criterion_id)

    effect = payload["intended_effect"]
    if not isinstance(effect, dict) or set(effect) != {
        "cluster_id",
        "tenant_ids",
        "publication_scope",
    }:
        raise InvalidSourcePolicyManifest(
            "Intended effect does not match the contract."
        )
    if (
        not isinstance(effect["cluster_id"], str)
        or not MANIFEST_ID.fullmatch(effect["cluster_id"])
        or not isinstance(effect["tenant_ids"], list)
        or any(
            not isinstance(tenant_id, str) or not MANIFEST_ID.fullmatch(tenant_id)
            for tenant_id in effect["tenant_ids"]
        )
        or len(effect["tenant_ids"]) != len(set(effect["tenant_ids"]))
        or not _is_choice(
            effect["publication_scope"],
            {"shared_cluster_inventory", "tenant_catalog_binding"},
        )
        or (
            effect["publication_scope"] == "tenant_catalog_binding"
            and not effect["tenant_ids"]
        )
    ):
        raise InvalidSourcePolicyManifest("Intended effect values are invalid.")

    evidence = payload["verification_evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise InvalidSourcePolicyManifest("Verification evidence must be non-empty.")
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {
            "evidence_type",
            "reference_url",
            "observed_at_utc",
            "result",
        }:
            raise InvalidSourcePolicyManifest("Verification evidence shape is invalid.")
        if (
            not _is_choice(
                item["evidence_type"],
                {"official_page", "api_response", "policy_review"},
            )
            or not _https_url(item["reference_url"])
            or not _utc_timestamp(item["observed_at_utc"])
            or not _nonempty_text(item["result"], 1024)
        ):
            raise InvalidSourcePolicyManifest(
                "Verification evidence values are invalid."
            )

    if payload["review_status"] != REVIEW_STATUS or payload["approval"] is not None:
        raise InvalidSourcePolicyManifest(
            "Candidate manifests must remain pending and unapproved in this contract."
        )
    return payload


def _validate_public_search_policy_bundle(
    payload: dict[str, object],
) -> dict[str, object]:
    """Validate one digest-bound, multi-source external-search policy version."""
    required = {
        "manifest_id",
        "manifest_version",
        "candidate_id",
        "manifest_kind",
        "policy_bundle",
        "review_status",
        "approval",
    }
    if set(payload) != required:
        raise InvalidSourcePolicyManifest(
            "Policy bundle fields do not match the contract."
        )
    if (
        not isinstance(payload["manifest_id"], str)
        or not MANIFEST_ID.fullmatch(payload["manifest_id"])
        or not isinstance(payload["manifest_version"], str)
        or not SEMVER.fullmatch(payload["manifest_version"])
        or not isinstance(payload["candidate_id"], str)
        or not MANIFEST_ID.fullmatch(payload["candidate_id"])
    ):
        raise InvalidSourcePolicyManifest("Policy bundle identity is invalid.")
    if payload["review_status"] != REVIEW_STATUS or payload["approval"] is not None:
        raise InvalidSourcePolicyManifest(
            "Policy bundle must remain pending and unapproved."
        )

    bundle = payload["policy_bundle"]
    if not isinstance(bundle, dict) or set(bundle) != {"reviewed_at", "entries"}:
        raise InvalidSourcePolicyManifest(
            "Policy bundle metadata does not match the contract."
        )
    if not _date(bundle["reviewed_at"]):
        raise InvalidSourcePolicyManifest("Policy bundle review date is invalid.")
    entries = bundle["entries"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= 12:
        raise InvalidSourcePolicyManifest("Policy bundle must have 1 to 12 entries.")
    required_entry_fields = {
        "entry_id",
        "source_id",
        "host",
        "path_prefixes",
        "authority_class",
        "owner",
        "authority_jurisdiction",
        "permitted_uses",
        "verification_evidence",
        "review_status",
        "reviewed_at",
    }
    entry_fields = required_entry_fields | {"scope_kind"}
    entry_ids: set[str] = set()
    scopes: set[tuple[str, str, tuple[str, ...]]] = set()
    allowed_authorities = AUTHORITY_TYPES | {
        "approved_operator",
        "unverified_domain_class",
        "unverified_related_domain",
    }
    allowed_uses = {
        "discovery",
        "criteria",
        "application_guidance",
        "context",
    }
    for entry in entries:
        if not isinstance(entry, dict) or frozenset(entry) not in {
            frozenset(required_entry_fields),
            frozenset(entry_fields),
        }:
            raise InvalidSourcePolicyManifest(
                "Policy bundle entry fields do not match."
            )
        entry_id = entry["entry_id"]
        source_id = entry["source_id"]
        host = entry["host"]
        prefixes = entry["path_prefixes"]
        scope_kind = entry.get("scope_kind", "exact_host_path")
        labels = host.split(".") if isinstance(host, str) else []
        exact_host_valid = (
            isinstance(host, str)
            and host == host.lower()
            and len(host) <= 253
            and len(labels) > 1
            and all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in labels
            )
        )
        domain_class_valid = (
            scope_kind == "domain_suffix"
            and isinstance(host, str)
            and host in {"gov", "org", "us", "edu"}
            and entry.get("authority_class") == "unverified_domain_class"
            and entry.get("authority_jurisdiction") == "unknown"
            and entry.get("permitted_uses") == ["discovery"]
        )
        related_domain_valid = (
            scope_kind == "related_domain"
            and exact_host_valid
            and isinstance(host, str)
            and host.endswith(".com")
            and entry.get("authority_class") == "unverified_related_domain"
            and "https://" in str(entry.get("verification_evidence", ""))
        )
        scope_host_valid = (
            exact_host_valid
            if scope_kind == "exact_host_path"
            else domain_class_valid or related_domain_valid
        )
        prefix_valid = (
            isinstance(prefixes, list)
            and 1 <= len(prefixes) <= 10
            and len(prefixes) == len(set(prefixes))
            and all(
                isinstance(prefix, str)
                and prefix.startswith("/")
                and "?" not in prefix
                and "#" not in prefix
                and "\\" not in prefix
                and all(part not in {".", ".."} for part in prefix.split("/"))
                for prefix in prefixes
            )
        )
        uses = entry["permitted_uses"]
        if (
            not isinstance(entry_id, str)
            or not re.fullmatch(r"[a-z0-9.-]{2,80}", entry_id)
            or entry_id in entry_ids
            or not isinstance(source_id, str)
            or not MANIFEST_ID.fullmatch(source_id)
            or not scope_host_valid
            or not isinstance(scope_kind, str)
            or scope_kind not in {"exact_host_path", "domain_suffix", "related_domain"}
            or not prefix_valid
            or (scope_kind != "exact_host_path" and prefixes != ["/"])
            or not _is_choice(entry["authority_class"], allowed_authorities)
            or not _nonempty_text(entry["owner"], 200)
            or not isinstance(entry["authority_jurisdiction"], str)
            or not re.fullmatch(
                r"US(?:-[A-Z]{2})?|unknown", entry["authority_jurisdiction"]
            )
            or not _string_list(uses, allowed_uses)
            or not _nonempty_text(entry["verification_evidence"], 1200)
            or entry["review_status"] != "approved_for_bounded_discovery"
            or not _date(entry["reviewed_at"])
        ):
            raise InvalidSourcePolicyManifest("Policy bundle entry is invalid.")
        scope = (scope_kind, host, tuple(sorted(prefixes)))
        if scope in scopes:
            raise InvalidSourcePolicyManifest(
                "Policy bundle contains a duplicate host/path scope."
            )
        entry_ids.add(entry_id)
        scopes.add(scope)
    return payload


def _date(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def canonical_manifest_json(manifest: dict[str, object]) -> str:
    """Serialize a validated manifest with stable key ordering for digesting."""
    return json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def source_policy_manifest_sha256(manifest: dict[str, object]) -> str:
    """Return the SHA-256 digest of the canonical manifest JSON."""
    return hashlib.sha256(canonical_manifest_json(manifest).encode("utf-8")).hexdigest()


def validate_manifest_registration(payload: object) -> tuple[dict[str, object], str]:
    """Validate immutable registration envelope and caller-supplied digest."""
    if not isinstance(payload, dict) or set(payload) != {
        "manifest",
        "expected_content_sha256",
    }:
        raise InvalidSourcePolicyManifest("Registration envelope is invalid.")
    manifest = validate_source_policy_manifest(payload["manifest"])
    expected = payload["expected_content_sha256"]
    if not isinstance(expected, str) or not SHA256.fullmatch(expected):
        raise InvalidSourcePolicyManifest("Expected content digest is invalid.")
    actual = source_policy_manifest_sha256(manifest)
    if actual != expected:
        raise InvalidSourcePolicyManifest(
            "Expected digest does not match manifest content."
        )
    return manifest, actual


def validate_source_policy_decision(
    payload: object, manifest_id: str, manifest_version: str
) -> tuple[str, str, str]:
    """Validate a review decision bound to one exact candidate digest."""
    if not MANIFEST_ID.fullmatch(manifest_id) or not SEMVER.fullmatch(manifest_version):
        raise InvalidSourcePolicyManifest("Manifest identity is invalid.")
    required = {"expected_content_sha256", "decision", "rationale"}
    if not isinstance(payload, dict) or frozenset(payload) not in {
        frozenset(required),
        frozenset(required | {"operator_audit_context"}),
    }:
        raise InvalidSourcePolicyManifest("Decision request fields do not match.")
    try:
        validate_operator_audit_context(payload.get("operator_audit_context"))
    except ValueError as exc:
        raise InvalidSourcePolicyManifest("Decision audit context is invalid.") from exc
    digest = payload["expected_content_sha256"]
    decision = payload["decision"]
    rationale = payload["rationale"]
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise InvalidSourcePolicyManifest("Expected content digest is invalid.")
    if not _is_choice(decision, {"approved", "rejected"}):
        raise InvalidSourcePolicyManifest("Decision must be approved or rejected.")
    if not _nonempty_text(rationale, 2048):
        raise InvalidSourcePolicyManifest("Decision rationale is required.")
    return digest, decision, rationale


def validate_source_policy_activation(payload: object) -> dict[str, object]:
    """Validate an exact-digest activation, deactivation, or rollback request."""
    required = {
        "request_id",
        "action",
        "expected_current",
        "target",
        "rationale",
    }
    if not isinstance(payload, dict) or frozenset(payload) not in {
        frozenset(required),
        frozenset(required | {"operator_audit_context"}),
    }:
        raise InvalidSourcePolicyManifest(
            "Lifecycle request fields do not match the contract."
        )

    try:
        request_id = str(uuid.UUID(str(payload["request_id"])))
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidSourcePolicyManifest("Lifecycle request ID is invalid.") from exc

    action = payload["action"]
    if not isinstance(action, str) or action not in {
        "activate",
        "deactivate",
        "rollback",
    }:
        raise InvalidSourcePolicyManifest("Lifecycle action is invalid.")

    expected_current = _validate_activation_reference(
        payload["expected_current"], optional=True, field_name="expected current"
    )
    target = _validate_activation_reference(
        payload["target"], optional=action == "deactivate", field_name="target"
    )
    if action == "deactivate" and target is not None:
        raise InvalidSourcePolicyManifest("Deactivation cannot include a target.")
    if action != "deactivate" and target is None:
        raise InvalidSourcePolicyManifest("Activation requires a target digest.")

    rationale = payload["rationale"]
    if not _nonempty_text(rationale, 2048):
        raise InvalidSourcePolicyManifest("Lifecycle rationale is invalid.")

    try:
        operator_audit_context = validate_operator_audit_context(
            payload.get("operator_audit_context")
        )
    except ValueError as exc:
        raise InvalidSourcePolicyManifest("Operator audit context is invalid.") from exc

    normalized = {
        "request_id": request_id,
        "action": action,
        "expected_current": expected_current,
        "target": target,
        "rationale": rationale,
    }
    if operator_audit_context is not None:
        normalized["operator_audit_context"] = operator_audit_context
    return normalized


def _validate_activation_reference(
    value: object, *, optional: bool, field_name: str
) -> dict[str, str] | None:
    """Validate one manifest identity and content digest in a lifecycle request."""
    if value is None and optional:
        return None
    if not isinstance(value, dict) or set(value) != {
        "manifest_id",
        "manifest_version",
        "content_sha256",
    }:
        raise InvalidSourcePolicyManifest(f"Lifecycle {field_name} is invalid.")
    manifest_id = value["manifest_id"]
    manifest_version = value["manifest_version"]
    digest = value["content_sha256"]
    if (
        not isinstance(manifest_id, str)
        or not MANIFEST_ID.fullmatch(manifest_id)
        or not isinstance(manifest_version, str)
        or not SEMVER.fullmatch(manifest_version)
        or not isinstance(digest, str)
        or not SHA256.fullmatch(digest)
    ):
        raise InvalidSourcePolicyManifest(f"Lifecycle {field_name} is invalid.")
    return {
        "manifest_id": manifest_id,
        "manifest_version": manifest_version,
        "content_sha256": digest,
    }
