"""Validation and canonical digests for unpublished Eligibility candidates."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from urllib.parse import urlsplit

from operator_audit import validate_operator_audit_context

_CANDIDATE_ID = re.compile(r"candidate-[0-9a-f]{32}\Z")
_CANDIDATE_REF = re.compile(r"candidate-ref-[0-9a-f]{32}\Z")
_PROGRAM_ID = re.compile(r"[a-z0-9][a-z0-9._-]{1,99}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CANDIDATE_IDENTITY_BASES = {
    "publisher_program_number",
    "canonical_url",
    "source_record_id",
}
_PROGRAM_IDENTITY_BASES = _CANDIDATE_IDENTITY_BASES | {"selected_catalog_program_id"}


class InvalidProgramCandidate(ValueError):
    """A normalized program candidate violates its unpublished-record contract."""


class ProgramCandidateConflict(RuntimeError):
    """A candidate identity is already stored with different immutable content."""


class ProgramCandidateDecisionConflict(RuntimeError):
    """A candidate already has a different immutable review decision."""


class ProgramCandidateNotFound(LookupError):
    """The requested pending program candidate does not exist."""


def program_candidate_sha256(candidate: dict[str, object]) -> str:
    """Hash the canonical JSON representation used for registration and readback."""
    encoded = json.dumps(
        candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_program_candidate(payload: object) -> dict[str, object]:
    """Accept only the normalizer's pending, provenance-bearing output shape."""
    common_fields = {
        "contract_version",
        "status",
        "candidate_id",
        "source_lineage",
        "listing",
        "field_lineage",
        "omitted_fields",
        "criteria_mapping",
        "validation_findings",
        "governance",
    }
    if not isinstance(payload, dict):
        raise InvalidProgramCandidate("Candidate fields do not match the contract.")
    contract_version = payload.get("contract_version")
    version_fields = {
        "program-candidate-v1": set(),
        "program-candidate-v2": {"candidate_ref", "candidate_identity_basis"},
        "program-candidate-v3": {
            "candidate_ref",
            "candidate_identity_basis",
            "program_id",
            "program_identity_basis",
        },
    }
    expected = common_fields | version_fields.get(contract_version, set())
    if contract_version not in version_fields or set(payload) != expected:
        raise InvalidProgramCandidate("Candidate fields do not match the contract.")
    if (
        payload["status"] != "candidate_ready_for_review"
        or not isinstance(payload["candidate_id"], str)
        or not _CANDIDATE_ID.fullmatch(payload["candidate_id"])
    ):
        raise InvalidProgramCandidate("Candidate identity or status is invalid.")
    if contract_version == "program-candidate-v2" and (
        not isinstance(payload["candidate_ref"], str)
        or not _CANDIDATE_REF.fullmatch(payload["candidate_ref"])
        or not isinstance(payload["candidate_identity_basis"], str)
        or payload["candidate_identity_basis"] not in _CANDIDATE_IDENTITY_BASES
    ):
        raise InvalidProgramCandidate("Candidate continuity identity is invalid.")
    if contract_version == "program-candidate-v3" and (
        not isinstance(payload["candidate_ref"], str)
        or not _CANDIDATE_REF.fullmatch(payload["candidate_ref"])
        or not isinstance(payload["candidate_identity_basis"], str)
        or payload["candidate_identity_basis"] not in _CANDIDATE_IDENTITY_BASES
        or not isinstance(payload["program_id"], str)
        or not _PROGRAM_ID.fullmatch(payload["program_id"])
        or not isinstance(payload["program_identity_basis"], str)
        or payload["program_identity_basis"] not in _PROGRAM_IDENTITY_BASES
    ):
        raise InvalidProgramCandidate("Candidate program identity is invalid.")

    lineage = payload["source_lineage"]
    lineage_fields = {
        "source_id",
        "source_record_id",
        "source_version",
        "source_url",
        "retrieved_at",
        "source_content_sha256",
    }
    if not isinstance(lineage, dict) or set(lineage) != lineage_fields:
        raise InvalidProgramCandidate("Source lineage does not match the contract.")
    if not all(
        isinstance(lineage[key], str) and lineage[key].strip()
        for key in ("source_id", "source_record_id", "source_version", "source_url")
    ):
        raise InvalidProgramCandidate("Source identity and version are required.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]+", lineage["source_id"]):
        raise InvalidProgramCandidate("Source identity is invalid.")
    if len(lineage["source_record_id"]) > 200 or len(lineage["source_version"]) > 120:
        raise InvalidProgramCandidate("Source record identity or version is too long.")
    parsed_url = urlsplit(lineage["source_url"])
    if parsed_url.scheme != "https" or not parsed_url.hostname:
        raise InvalidProgramCandidate("Candidate source URL must use HTTPS.")
    try:
        retrieved_at = lineage["retrieved_at"]
        if not isinstance(retrieved_at, str):
            raise ValueError
        retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise InvalidProgramCandidate("Retrieval time must be timezone-aware.") from exc
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise InvalidProgramCandidate("Retrieval time must be timezone-aware.")
    source_digest = lineage["source_content_sha256"]
    if source_digest is not None and (
        not isinstance(source_digest, str) or not _SHA256.fullmatch(source_digest)
    ):
        raise InvalidProgramCandidate("Source content digest is invalid.")

    listing = payload["listing"]
    field_lineage = payload["field_lineage"]
    if (
        not isinstance(listing, dict)
        or not isinstance(listing.get("title"), str)
        or not listing["title"].strip()
        or not isinstance(field_lineage, dict)
        or not set(field_lineage).issubset(listing)
        or any(not isinstance(item, dict) for item in field_lineage.values())
    ):
        raise InvalidProgramCandidate("Listing and field lineage must be objects.")
    if not isinstance(payload["omitted_fields"], list) or not all(
        isinstance(item, str) and item for item in payload["omitted_fields"]
    ):
        raise InvalidProgramCandidate("Omitted fields must be named strings.")
    criteria = payload["criteria_mapping"]
    base_criteria_fields = {
        "status",
        "review_status",
        "proposed_criteria",
        "unavailable_reason",
    }
    criteria_fields = base_criteria_fields | {"requirement_candidates", "coverage"}
    if (
        not isinstance(criteria, dict)
        or not base_criteria_fields.issubset(criteria)
        or not set(criteria).issubset(criteria_fields)
        or criteria["status"]
        not in {"not_assessed", "unavailable", "candidate_requires_review"}
        or criteria["review_status"] != "not_reviewed"
        or not isinstance(criteria["proposed_criteria"], list)
        or not isinstance(criteria.get("requirement_candidates", []), list)
        or criteria["status"] == "candidate_requires_review"
        and not criteria["proposed_criteria"]
        and not criteria.get("requirement_candidates")
        or criteria["status"] == "unavailable"
        and not isinstance(criteria["unavailable_reason"], str)
        or criteria["status"] == "unavailable"
        and (criteria["proposed_criteria"] or criteria.get("requirement_candidates"))
    ):
        raise InvalidProgramCandidate("Criteria state is invalid or overclaimed.")
    requirement_candidates = criteria.get("requirement_candidates", [])
    for requirement in requirement_candidates:
        if not _valid_requirement_candidate(requirement):
            raise InvalidProgramCandidate("Requirement candidate is invalid.")
    coverage = criteria.get("coverage")
    if coverage is not None and not _valid_requirement_coverage(
        coverage, requirement_candidates
    ):
        raise InvalidProgramCandidate("Requirement candidate coverage is invalid.")
    if not isinstance(payload["validation_findings"], list) or not all(
        isinstance(item, dict) for item in payload["validation_findings"]
    ):
        raise InvalidProgramCandidate("Validation findings must be objects.")
    governance = payload["governance"]
    if governance != {
        "source_verified": False,
        "review_status": "review_required",
        "catalog_status": "not_published",
        "approval_status": "not_reviewed",
        "persistence_performed": False,
        "tenant_semantics_changed": False,
        "eligibility_outcome": None,
    }:
        raise InvalidProgramCandidate("Candidate must remain pending and unpublished.")
    return payload


def _valid_requirement_candidate(value: object) -> bool:
    """Validate review-only criterion metadata without activating any rules."""
    required = {
        "requirement_id",
        "statement",
        "requirement_type",
        "mapping_status",
        "citation",
    }
    optional = {
        "concept_id",
        "profile_attribute",
        "operator",
        "value",
        "context_attribute",
        "unit",
        "threshold_basis",
        "applicability",
        "logic_group_id",
        "logic_group_operator",
        "unresolved_reason",
    }
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(required | optional)
    ):
        return False
    requirement_id = value["requirement_id"]
    statement = value["statement"]
    requirement_type = value["requirement_type"]
    mapping_status = value["mapping_status"]
    citation = value["citation"]
    if (
        not isinstance(requirement_id, str)
        or not requirement_id.strip()
        or len(requirement_id) > 160
        or not isinstance(statement, str)
        or not statement.strip()
        or len(statement) > 1200
        or requirement_type
        not in {"eligibility", "application", "availability", "context", "unclassified"}
        or mapping_status not in {"mapped", "partial", "not_mapped", "context_only"}
        or not isinstance(citation, dict)
    ):
        return False
    citation_required = {"source_url", "source_record_id", "retrieved_at"}
    citation_optional = {
        "source_id",
        "source_version",
        "source_content_sha256",
        "locator",
        "excerpt",
    }
    if (
        not citation_required.issubset(citation)
        or not set(citation).issubset(citation_required | citation_optional)
        or not isinstance(citation.get("source_url"), str)
        or not citation["source_url"].startswith("https://")
        or not isinstance(citation.get("source_record_id"), str)
        or not citation["source_record_id"].strip()
        or not isinstance(citation.get("retrieved_at"), str)
    ):
        return False
    try:
        retrieved = datetime.fromisoformat(
            citation["retrieved_at"].replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        return False
    source_digest = citation.get("source_content_sha256")
    if source_digest is not None and (
        not isinstance(source_digest, str) or not _SHA256.fullmatch(source_digest)
    ):
        return False
    mapping_parts = (
        value.get("concept_id"),
        value.get("profile_attribute"),
        value.get("operator"),
        value.get("value"),
    )
    has_mapping = any(part is not None for part in mapping_parts)
    complete_mapping = all(part is not None for part in mapping_parts)
    if mapping_status == "mapped" and (
        requirement_type != "eligibility" or not complete_mapping
    ):
        return False
    if mapping_status == "partial" and not value.get("unresolved_reason"):
        return False
    if mapping_status in {"not_mapped", "context_only"} and has_mapping:
        return False
    if (value.get("logic_group_id") is None) != (
        value.get("logic_group_operator") is None
    ):
        return False
    return True


def _valid_requirement_coverage(value: object, requirements: list[object]) -> bool:
    """Require normalizer coverage to reconcile and to keep active rules at zero."""
    if not isinstance(value, dict) or set(value) != {
        "source_completeness",
        "requirement_count",
        "mapped_count",
        "partial_count",
        "unmapped_or_context_only_count",
        "active_rule_count",
    }:
        return False
    counts = [
        value.get(name)
        for name in (
            "requirement_count",
            "mapped_count",
            "partial_count",
            "unmapped_or_context_only_count",
            "active_rule_count",
        )
    ]
    if any(
        isinstance(count, bool) or not isinstance(count, int) or count < 0
        for count in counts
    ):
        return False
    statuses = [
        item.get("mapping_status") for item in requirements if isinstance(item, dict)
    ]
    return (
        value["source_completeness"] == "not_proven"
        and value["requirement_count"] == len(requirements)
        and value["mapped_count"] == statuses.count("mapped")
        and value["partial_count"] == statuses.count("partial")
        and value["unmapped_or_context_only_count"]
        == statuses.count("not_mapped") + statuses.count("context_only")
        and value["active_rule_count"] == 0
    )


def validate_program_candidate_registration(
    payload: object,
) -> tuple[dict[str, object], str]:
    """Validate an exact-digest registration command."""
    if not isinstance(payload, dict) or set(payload) != {
        "candidate",
        "expected_content_sha256",
    }:
        raise InvalidProgramCandidate("Registration fields do not match the contract.")
    candidate = validate_program_candidate(payload["candidate"])
    expected = payload["expected_content_sha256"]
    if not isinstance(expected, str) or not _SHA256.fullmatch(expected):
        raise InvalidProgramCandidate("Expected candidate digest is invalid.")
    actual = program_candidate_sha256(candidate)
    if actual != expected:
        raise InvalidProgramCandidate("Candidate digest does not match the payload.")
    return candidate, actual


def validate_program_candidate_decision(
    payload: object, candidate_id: str
) -> tuple[str, str, str, str | None, str | None]:
    """Validate a review decision bound to one candidate identity and digest."""
    if not _CANDIDATE_ID.fullmatch(candidate_id):
        raise InvalidProgramCandidate("Candidate identity is invalid.")
    required = {"expected_content_sha256", "decision", "rationale"}
    governed = required | {"transaction_id", "approval_reference"}
    if not isinstance(payload, dict) or frozenset(payload) not in {
        frozenset(required),
        frozenset(required | {"operator_audit_context"}),
        frozenset(governed),
        frozenset(governed | {"operator_audit_context"}),
    }:
        raise InvalidProgramCandidate("Decision request fields do not match.")
    try:
        validate_operator_audit_context(payload.get("operator_audit_context"))
    except ValueError as exc:
        raise InvalidProgramCandidate("Decision audit context is invalid.") from exc
    digest = payload["expected_content_sha256"]
    decision = payload["decision"]
    rationale = payload["rationale"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise InvalidProgramCandidate("Expected candidate digest is invalid.")
    if not isinstance(decision, str) or decision not in {"approved", "rejected"}:
        raise InvalidProgramCandidate("Decision must be approved or rejected.")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2048:
        raise InvalidProgramCandidate("Decision rationale is required.")
    transaction_id = payload.get("transaction_id")
    approval_reference = payload.get("approval_reference")
    if transaction_id is not None:
        try:
            transaction_id = str(uuid.UUID(transaction_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidProgramCandidate(
                "Decision transaction ID is invalid."
            ) from exc
    if approval_reference is not None and (
        not isinstance(approval_reference, str)
        or not approval_reference.strip()
        or len(approval_reference) > 512
        or any(ord(char) < 32 or ord(char) == 127 for char in approval_reference)
    ):
        raise InvalidProgramCandidate("Decision approval reference is invalid.")
    return digest, decision, rationale.strip(), transaction_id, approval_reference
