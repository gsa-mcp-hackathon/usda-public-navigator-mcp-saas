"""Contracts for promoting approved program candidates into shared inventory."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from urllib.parse import unquote, urlsplit

from operator_audit import validate_operator_audit_context


class InvalidProgramCandidatePublication(ValueError):
    """A publication request or candidate projection violates its contract."""


class ProgramCandidatePublicationConflict(RuntimeError):
    """An immutable publication or lifecycle request conflicts with stored state."""

    REASON_CODES = frozenset(
        {
            "candidate_digest_or_approval_mismatch",
            "candidate_source_dataset_mismatch",
            "target_dataset_not_publishable",
            "source_policy_not_active_or_approved",
            "existing_record_content_mismatch",
            "publication_readback_mismatch",
            "publication_state_conflict",
        }
    )

    def __init__(self, message: str, reason_code: str = "publication_state_conflict"):
        self.reason_code = (
            reason_code
            if isinstance(reason_code, str) and reason_code in self.REASON_CODES
            else "publication_state_conflict"
        )
        super().__init__(message)


_CANDIDATE_ID = re.compile(r"candidate-[0-9a-f]{32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DATASET_ID = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_MANIFEST_ID = re.compile(r"[a-z][a-z0-9.-]{0,127}\Z")
_SEMVER = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")


def validate_program_candidate_publication_request(
    payload: object, candidate_id: str
) -> tuple[str, str]:
    """Validate the exact candidate digest and target shared dataset."""
    if not _CANDIDATE_ID.fullmatch(candidate_id):
        raise InvalidProgramCandidatePublication("Candidate identity is invalid.")
    if not isinstance(payload, dict) or set(payload) != {
        "expected_content_sha256",
        "dataset_id",
    }:
        raise InvalidProgramCandidatePublication(
            "Publication request fields are invalid."
        )
    digest = payload["expected_content_sha256"]
    dataset_id = payload["dataset_id"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise InvalidProgramCandidatePublication("Candidate digest is invalid.")
    if not isinstance(dataset_id, str) or not _DATASET_ID.fullmatch(dataset_id):
        raise InvalidProgramCandidatePublication("Target dataset identity is invalid.")
    return digest, dataset_id


def validate_program_candidate_publication_lifecycle(
    payload: object, publication_key: str
) -> dict[str, object]:
    """Validate one digest-bound, compare-and-set publication lifecycle command."""
    if not _SHA256.fullmatch(publication_key):
        raise InvalidProgramCandidatePublication("Publication identity is invalid.")
    expected_fields = {"candidate_id", "content_sha256"}
    lifecycle_fields = {
        "request_id",
        "action",
        "expected_current",
        "target",
        "rationale",
    }
    if not isinstance(payload, dict) or frozenset(payload) not in {
        frozenset(lifecycle_fields),
        frozenset(lifecycle_fields | {"operator_audit_context"}),
    }:
        raise InvalidProgramCandidatePublication(
            "Lifecycle request fields are invalid."
        )
    try:
        request_id = str(uuid.UUID(str(payload["request_id"])))
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidProgramCandidatePublication(
            "Request identity is invalid."
        ) from exc
    action = payload["action"]
    rationale = payload["rationale"]
    if action not in {"activate", "deactivate", "rollback"}:
        raise InvalidProgramCandidatePublication("Lifecycle action is invalid.")
    if not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 2048:
        raise InvalidProgramCandidatePublication("Lifecycle rationale is required.")
    try:
        operator_audit_context = validate_operator_audit_context(
            payload.get("operator_audit_context")
        )
    except ValueError as exc:
        raise InvalidProgramCandidatePublication(
            "Lifecycle operator audit context is invalid."
        ) from exc
    expected = _publication_reference(payload["expected_current"], expected_fields)
    target = _publication_reference(payload["target"], expected_fields)
    if (action == "deactivate" and target is not None) or (
        action in {"activate", "rollback"} and target is None
    ):
        raise InvalidProgramCandidatePublication(
            "Lifecycle target does not match action."
        )
    normalized = {
        "request_id": request_id,
        "action": action,
        "expected_current": expected,
        "target": target,
        "rationale": rationale.strip(),
    }
    if operator_audit_context is not None:
        normalized["operator_audit_context"] = operator_audit_context
    return normalized


def _publication_reference(
    value: object, expected_fields: set[str]
) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != expected_fields:
        raise InvalidProgramCandidatePublication("Publication reference is invalid.")
    candidate_id = value["candidate_id"]
    digest = value["content_sha256"]
    if (
        not isinstance(candidate_id, str)
        or not _CANDIDATE_ID.fullmatch(candidate_id)
        or not isinstance(digest, str)
        or not _SHA256.fullmatch(digest)
    ):
        raise InvalidProgramCandidatePublication("Publication reference is invalid.")
    return {"candidate_id": candidate_id, "content_sha256": digest}


def approved_policy_entry_for_url(
    raw_url: str, entries: object, expected_source_id: str
) -> dict[str, object] | None:
    """Match an HTTPS URL to the active exact, domain-class, or related scope."""
    if not isinstance(entries, list):
        return None
    try:
        parsed = urlsplit(raw_url)
        host = (parsed.hostname or "").lower().rstrip(".")
        path = unquote(parsed.path or "/")
        if (
            parsed.scheme.lower() != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
            or "\\" in path
            or any(part in {".", ".."} for part in path.split("/"))
        ):
            return None
        matching: list[tuple[int, dict[str, object]]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            permitted_uses = entry.get("permitted_uses")
            scope_kind = entry.get("scope_kind", "exact_host_path")
            policy_host = entry.get("host")
            if not isinstance(policy_host, str):
                continue
            host_matches = (
                host == policy_host
                if scope_kind == "exact_host_path"
                else host.endswith("." + str(policy_host))
                if scope_kind == "domain_suffix"
                else host == policy_host or host.endswith("." + str(policy_host))
            )
            if (
                entry.get("source_id") != expected_source_id
                or entry.get("review_status") != "approved_for_bounded_discovery"
                or not isinstance(permitted_uses, list)
                or "discovery" not in permitted_uses
                or scope_kind
                not in {"exact_host_path", "domain_suffix", "related_domain"}
                or not host_matches
                or not isinstance(entry.get("owner"), str)
            ):
                continue
            prefixes = entry.get("path_prefixes")
            if not isinstance(prefixes, list):
                continue
            for prefix in prefixes:
                if (
                    isinstance(prefix, str)
                    and prefix.startswith("/")
                    and (
                        prefix.rstrip("/") in {"", "/"}
                        or path == prefix.rstrip("/")
                        or path.startswith(prefix.rstrip("/") + "/")
                    )
                ):
                    matching.append((len(policy_host), entry))
        return max(matching, key=lambda item: item[0])[1] if matching else None
    except (TypeError, ValueError):
        return None


def build_public_listing_record(
    candidate: dict[str, object],
    *,
    dataset: dict[str, object],
    policy_entry: dict[str, object],
    policy_reference: dict[str, object],
    policy_entries: object,
) -> tuple[str, str, dict[str, object]]:
    """Project reviewed listing fields without copying proposed criteria."""
    if (
        not isinstance(policy_reference, dict)
        or not isinstance(policy_reference.get("manifest_id"), str)
        or not _MANIFEST_ID.fullmatch(policy_reference["manifest_id"])
        or not isinstance(policy_reference.get("manifest_version"), str)
        or not _SEMVER.fullmatch(policy_reference["manifest_version"])
        or not isinstance(policy_reference.get("content_sha256"), str)
        or not _SHA256.fullmatch(policy_reference["content_sha256"])
        or not isinstance(policy_entry.get("entry_id"), str)
    ):
        raise InvalidProgramCandidatePublication(
            "Active source-policy reference is invalid."
        )
    lineage = candidate["source_lineage"]
    listing = candidate["listing"]
    source_id = str(lineage["source_id"])
    source_record_id = str(lineage["source_record_id"])
    canonical_url = listing.get("canonical_url")
    if (
        canonical_url is not None
        and approved_policy_entry_for_url(str(canonical_url), policy_entries, source_id)
        is None
    ):
        raise InvalidProgramCandidatePublication(
            "Candidate listing URL is outside the active approved source policy."
        )
    schema_version = str(dataset["schema_version"])
    listing_kind = listing.get("listing_kind")
    if listing_kind not in {"funding_opportunity", "assistance_program"}:
        raise InvalidProgramCandidatePublication(
            "Listing kind must be explicitly classified before publication."
        )
    allowed_versions = (
        {"public-listing-v1", "public-listing-v3"}
        if listing_kind == "funding_opportunity"
        else {"public-listing-v2", "public-listing-v3"}
    )
    if schema_version not in allowed_versions:
        raise InvalidProgramCandidatePublication(
            "Target dataset schema is incompatible with the candidate listing kind."
        )
    jurisdiction_code = listing.get("jurisdiction_code")
    if jurisdiction_code == "US":
        jurisdiction = {"level": "federal", "country_code": "US"}
    elif isinstance(jurisdiction_code, str) and re.fullmatch(
        r"US-[A-Z]{2}", jurisdiction_code
    ):
        jurisdiction = {
            "level": "state",
            "country_code": "US",
            "state_code": jurisdiction_code[-2:],
        }
    else:
        raise InvalidProgramCandidatePublication(
            "Candidate jurisdiction must be explicitly normalized as US or US-XX."
        )

    attributes: dict[str, object] = {
        "listing.title": listing["title"],
        "listing.agency": listing.get("agency") or policy_entry["owner"],
        "core.geography.jurisdiction_level": jurisdiction["level"],
        "core.geography.country_code": "US",
    }
    optional_fields = {
        "summary": "listing.summary",
        "program_number": "listing.program_number",
        "audience": "listing.applicant_types",
        "open_date": "listing.open_date",
        "close_date": "listing.close_date",
    }
    supported_fields = {
        "public-listing-v1": {"summary", "audience", "open_date", "close_date"},
        "public-listing-v2": {"summary", "program_number"},
        "public-listing-v3": set(optional_fields),
    }[schema_version]
    for field, attribute_id in optional_fields.items():
        value = listing.get(field)
        if field in supported_fields and value not in (None, []):
            attributes[attribute_id] = value
    if jurisdiction.get("state_code"):
        attributes["core.geography.state_code"] = jurisdiction["state_code"]
    status = listing.get("status") or "unknown"
    if listing_kind == "funding_opportunity" and not listing.get("canonical_url"):
        raise InvalidProgramCandidatePublication(
            "Funding opportunity requires its source-backed opportunity URL."
        )
    if (
        listing_kind == "assistance_program"
        and schema_version == "public-listing-v2"
        and not listing.get("program_number")
    ):
        raise InvalidProgramCandidatePublication(
            "Assistance program requires a source-backed program number."
        )
    if schema_version == "public-listing-v3":
        attributes["listing.record_kind"] = listing_kind
        attributes["listing.status"] = status
        target_url_attribute = (
            "listing.opportunity_url"
            if listing_kind == "funding_opportunity"
            else "listing.program_url"
        )
        attributes[target_url_attribute] = canonical_url or lineage["source_url"]
    elif listing_kind == "funding_opportunity":
        attributes["listing.status"] = status
        attributes["listing.opportunity_url"] = listing["canonical_url"]
    else:
        if jurisdiction["level"] != "federal":
            raise InvalidProgramCandidatePublication(
                "public-listing-v2 only supports the federal assistance catalog."
            )
        attributes["listing.record_kind"] = "assistance_program"
        attributes["listing.status"] = "unknown"
        if canonical_url:
            attributes["listing.program_url"] = canonical_url

    publication_key = hashlib.sha256(
        f"{source_id}\n{source_record_id}".encode("utf-8")
    ).hexdigest()
    candidate_suffix = str(candidate.get("candidate_id", "")).removeprefix("candidate-")
    candidate_digest_prefix = program_candidate_record_digest(candidate)[:16]
    record_id = f"candidate.{candidate_suffix}.{candidate_digest_prefix}"
    retrieved_at = str(lineage["retrieved_at"])
    source_metadata = {
        "source_id": source_id,
        "source_type": "public_source",
        "approval_status": "approved",
        "publisher": policy_entry["owner"],
        "source_title": dataset["title"],
        "source_version": lineage["source_version"],
        "transform_version": "program-candidate-promotion-v1",
        "retrieved_at": retrieved_at,
        "criteria_status": (
            "unavailable"
            if candidate["criteria_mapping"]["status"] == "unavailable"
            else "not_assessed"
        ),
        "candidate_criteria_review_status": candidate["criteria_mapping"][
            "review_status"
        ],
        "candidate_criteria_proposals_omitted": bool(
            candidate["criteria_mapping"]["proposed_criteria"]
        ),
        "criteria_available": False,
        "raw_payload_stored": False,
        "program_candidate_id": candidate["candidate_id"],
        "program_candidate_sha256": program_candidate_record_digest(candidate),
        "program_candidate_publication_key": publication_key,
        "source_policy_reference": {
            **policy_reference,
            "entry_id": policy_entry["entry_id"],
        },
    }
    semantic_context = {
        "catalog_namespace": "programs",
        "jurisdiction": jurisdiction,
        "listing_kind": listing_kind,
    }
    program_id = candidate.get("program_id")
    if isinstance(program_id, str) and program_id.strip():
        # Keep identity linkage separate from candidate criteria proposals. The
        # criteria remain in the ruleset store; this stable ID lets discovery
        # resolve that ruleset without relying on the content-derived listing ID.
        semantic_context["criteria_program_ids"] = [program_id]
    if schema_version == "public-listing-v3":
        semantic_context["listing_kind"] = listing_kind
    record = {
        "source_record_id": record_id,
        "record_type": "public_listing",
        "canonical_attributes": attributes,
        "semantic_context": semantic_context,
        "source_metadata": source_metadata,
        "source_url": lineage["source_url"],
        "schema_version": schema_version,
        "observed_at": retrieved_at,
    }
    record["content_hash"] = hashlib.sha256(
        json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return publication_key, record_id, record


def program_candidate_record_digest(candidate: dict[str, object]) -> str:
    """Return the canonical digest used by candidate review and publication."""
    return hashlib.sha256(
        json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
