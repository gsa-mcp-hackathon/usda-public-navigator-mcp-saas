"""Deterministic, non-persisting normalization of public program candidates."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastmcp import FastMCP
from pydantic import ConfigDict, Field, HttpUrl, model_validator

from clusters.eligibility_contracts import (
    CriterionOperator,
    EligibilityAttribute,
    EligibilityCriterion,
    Jurisdiction,
    NavigatorId,
    ProgramRecord,
    SourceReference,
    StrictModel,
)
from clusters.eligibility_discovery import CANONICAL_TO_PROFILE_ATTRIBUTE


class CandidateCitation(StrictModel):
    """A source location offered as evidence for one candidate assertion."""

    source_url: HttpUrl
    source_record_id: str = Field(min_length=1, max_length=200)
    retrieved_at: datetime
    source_id: str | None = Field(default=None, min_length=2, max_length=80)
    source_version: str | None = Field(default=None, max_length=500)
    source_content_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    source_visible_text_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    locator: str | None = Field(default=None, max_length=300)
    excerpt: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def citation_is_https_and_timestamped(self) -> CandidateCitation:
        """Require an HTTPS evidence URL and a timezone-aware retrieval time."""
        if self.source_url.scheme != "https":
            raise ValueError("Candidate evidence URLs must use HTTPS.")
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("Candidate evidence retrieval time needs a timezone.")
        return self


class ProposedCriterion(StrictModel):
    """A source-cited criterion claim, retained for review and never evaluated."""

    concept_id: str = Field(min_length=1, max_length=160)
    operator: CriterionOperator
    value: str | int | float | bool | list[str | int | float | bool]
    unit: str | None = Field(default=None, max_length=80)
    applicability: str | None = Field(default=None, max_length=500)
    citation: CandidateCitation


class RequirementCandidate(StrictModel):
    """One source statement with an optional, review-only profile mapping."""

    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {
                        "properties": {"mapping_status": {"const": "mapped"}},
                        "required": ["mapping_status"],
                    },
                    "then": {
                        "required": [
                            "concept_id",
                            "profile_attribute",
                            "operator",
                            "value",
                        ],
                        "properties": {
                            field: {"not": {"type": "null"}}
                            for field in (
                                "concept_id",
                                "profile_attribute",
                                "operator",
                                "value",
                            )
                        },
                    },
                },
                {
                    "if": {
                        "properties": {"mapping_status": {"const": "partial"}},
                        "required": ["mapping_status"],
                    },
                    "then": {
                        "required": ["unresolved_reason"],
                        "properties": {"unresolved_reason": {"not": {"type": "null"}}},
                    },
                },
                {
                    "if": {
                        "properties": {
                            "mapping_status": {"enum": ["not_mapped", "context_only"]}
                        },
                        "required": ["mapping_status"],
                    },
                    "then": {
                        "not": {
                            "anyOf": [
                                {
                                    "required": [field],
                                    "properties": {field: {"not": {"type": "null"}}},
                                }
                                for field in (
                                    "concept_id",
                                    "profile_attribute",
                                    "operator",
                                    "value",
                                )
                            ]
                        }
                    },
                },
                {
                    "if": {
                        "properties": {
                            "mapping_status": {"const": "mapped"},
                            "operator": {"const": "lte_by"},
                        },
                        "required": ["mapping_status", "operator"],
                    },
                    "then": {
                        "required": ["context_attribute"],
                        "properties": {
                            "context_attribute": {"not": {"type": "null"}},
                            "value": {"type": "object"},
                        },
                    },
                },
            ]
        }
    )

    requirement_id: str = Field(min_length=1, max_length=160)
    criterion_id: str | None = Field(default=None, min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=1200)
    requirement_type: Literal[
        "eligibility", "application", "availability", "context", "unclassified"
    ]
    mapping_status: Literal["mapped", "partial", "not_mapped", "context_only"]
    concept_id: str | None = Field(default=None, max_length=160)
    profile_attribute: EligibilityAttribute | None = None
    operator: CriterionOperator | None = None
    value: (
        str
        | int
        | float
        | bool
        | list[str | int | float | bool]
        | dict[str, int | float]
        | None
    ) = None
    context_attribute: EligibilityAttribute | None = None
    unit: str | None = Field(default=None, max_length=80)
    threshold_basis: str | None = Field(default=None, max_length=300)
    applicability: str | None = Field(default=None, max_length=500)
    logic_group_id: str | None = Field(default=None, max_length=160)
    logic_group_operator: Literal["all_of", "any_of"] | None = None
    unresolved_reason: str | None = Field(default=None, max_length=500)
    citation: CandidateCitation

    @model_validator(mode="after")
    def validate_mapping_state(self) -> RequirementCandidate:
        """Require a complete profile rule, and thresholds for lte_by, when mapped."""
        mapping_parts = (
            self.concept_id,
            self.profile_attribute,
            self.operator,
            self.value,
        )
        has_mapping = any(item is not None for item in mapping_parts)
        complete_mapping = all(item is not None for item in mapping_parts)
        if self.mapping_status == "mapped" and not complete_mapping:
            raise ValueError("Mapped requirements need a complete profile rule.")
        if self.mapping_status == "mapped" and self.operator == "lte_by":
            if not isinstance(self.value, dict) or not self.context_attribute:
                raise ValueError(
                    "Mapped lte_by requirements need a threshold table and "
                    "context attribute."
                )
        if self.mapping_status == "partial" and not self.unresolved_reason:
            raise ValueError("Partial mappings need an unresolved reason.")
        if self.mapping_status in {"not_mapped", "context_only"} and has_mapping:
            raise ValueError(
                "Unmapped and context-only requirements cannot carry rules."
            )
        if self.requirement_type != "eligibility" and self.mapping_status == "mapped":
            raise ValueError("Only eligibility requirements can be executable rules.")
        if self.logic_group_id is not None and self.logic_group_operator is None:
            raise ValueError("Grouped requirements need an explicit logic operator.")
        if self.logic_group_id is None and self.logic_group_operator is not None:
            raise ValueError("A logic operator requires a logic group ID.")
        if self.operator == "one_of" and not isinstance(self.value, list):
            raise ValueError("one_of requires a list of allowed values.")
        if self.operator == "exists" and not isinstance(self.value, bool):
            raise ValueError("exists requires a boolean value.")
        if self.operator in {"gt", "gte", "lt", "lte"} and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float))
        ):
            raise ValueError("Numeric comparisons require a numeric value.")
        if self.operator not in {None, "one_of"} and isinstance(self.value, list):
            raise ValueError("Only one_of accepts a list value.")
        return self


class RequirementExtractionRequest(StrictModel):
    """A bounded source excerpt to turn into cited requirement candidates."""

    source_id: str = Field(min_length=2, max_length=80)
    source_record_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl
    retrieved_at: datetime
    source_content_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    source_visible_text_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    source_locator: str | None = Field(default=None, max_length=300)
    source_text: str = Field(min_length=1, max_length=50000)

    @model_validator(mode="after")
    def validate_source_snapshot(self) -> RequirementExtractionRequest:
        """Require HTTPS source URLs and a timezone-aware retrieval time."""
        if self.source_url.scheme != "https":
            raise ValueError("Requirement source URLs must use HTTPS.")
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("Source retrieval time needs a timezone.")
        return self


class NormalizedCriteriaExtractionRequestV1(StrictModel):
    """Previous JSON-string contract retained for template 0.1.13 clients."""

    source_id: str = Field(min_length=2, max_length=80)
    source_record_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=120)
    source_url: HttpUrl
    retrieved_at: datetime
    source_content_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    source_visible_text_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    source_locator: str = Field(min_length=1, max_length=300)
    criteria_clauses_json: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_source_snapshot(self) -> NormalizedCriteriaExtractionRequestV1:
        """Require HTTPS source URLs and a timezone-aware retrieval time."""
        if self.source_url.scheme != "https":
            raise ValueError("Requirement source URLs must use HTTPS.")
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("Source retrieval time needs a timezone.")
        for clause in self.criteria_clauses_json:
            try:
                parsed = json.loads(clause)
            except json.JSONDecodeError as exc:
                raise ValueError("Each criteria clause must be valid JSON.") from exc
            if not isinstance(parsed, dict):
                raise ValueError("Each criteria clause must be a JSON object.")
        return self


class NormalizedCriteriaExtractionRequest(StrictModel):
    """Extract candidates from normalized criteria objects returned by inventory."""

    source_id: str = Field(min_length=2, max_length=80)
    source_record_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=500)
    source_url: HttpUrl
    retrieved_at: datetime
    source_content_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    source_visible_text_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    source_locator: str = Field(min_length=1, max_length=300)
    criteria_clauses: list[dict[str, object]] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_source_snapshot(self) -> NormalizedCriteriaExtractionRequest:
        """Require HTTPS source URLs and a timezone-aware retrieval time."""
        if self.source_url.scheme != "https":
            raise ValueError("Requirement source URLs must use HTTPS.")
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("Source retrieval time needs a timezone.")
        return self


class ProgramCriteriaPackageRequest(StrictModel):
    """Compile explicitly mapped source statements into a review-only rule package."""

    program_id: str = Field(
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._-]{1,99}$",
    )
    catalog_version: str = Field(min_length=1, max_length=120)
    navigator: NavigatorId
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(min_length=1, max_length=2000)
    jurisdiction: Jurisdiction
    sources: list[SourceReference] = Field(min_length=1, max_length=20)
    requirements: list[RequirementCandidate] = Field(min_length=1, max_length=100)
    source_limitations: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def cited_source_references_match(self) -> ProgramCriteriaPackageRequest:
        """Require unique source and requirement ids that cite declared sources."""
        source_by_id = {item.source_id: item for item in self.sources}
        if len(source_by_id) != len(self.sources):
            raise ValueError("Source references must have unique source IDs.")
        requirement_ids = [item.requirement_id for item in self.requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("Requirement IDs must be unique within a package.")
        for requirement in self.requirements:
            citation = requirement.citation
            if citation.source_id is None or citation.source_id not in source_by_id:
                raise ValueError("Each requirement must cite a supplied source.")
            source = source_by_id[citation.source_id]
            if (
                source.url != citation.source_url
                or source.retrieved_at != citation.retrieved_at
                or (
                    citation.source_version is not None
                    and source.publisher_version != citation.source_version
                )
            ):
                raise ValueError("Requirement citation does not match source lineage.")
        return self


class ProgramCriteriaPackageCompiler:
    """Compile only explicit, supported mappings; never activate or persist rules."""

    def compile(self, request: ProgramCriteriaPackageRequest) -> dict[str, object]:
        """Compile requirements into executable criteria and unresolved items."""
        executable: list[dict[str, object]] = []
        unresolved: list[dict[str, object]] = []
        grouped: dict[str, list[RequirementCandidate]] = {}
        for requirement in request.requirements:
            if requirement.logic_group_id is not None:
                grouped.setdefault(requirement.logic_group_id, []).append(requirement)
        invalid_groups: set[str] = set()
        for group_id, members in grouped.items():
            operators = {member.logic_group_operator for member in members}
            if len(members) < 2 or len(operators) != 1:
                invalid_groups.add(group_id)

        for requirement in request.requirements:
            reason = self._unresolved_reason(requirement)
            if reason is None and requirement.logic_group_id in invalid_groups:
                reason = "compound_logic_group_requires_two_consistent_clauses"
            elif (
                reason is None
                and requirement.logic_group_id in grouped
                and any(
                    self._unresolved_reason(member) is not None
                    for member in grouped[requirement.logic_group_id]
                )
            ):
                reason = "compound_logic_group_has_unresolved_clause"
            if reason is not None:
                unresolved.append(
                    {
                        "requirement_id": requirement.requirement_id,
                        "reason": reason,
                        **_requirement_gap_diagnostic(requirement, reason),
                    }
                )
                continue
            profile_attribute = CANONICAL_TO_PROFILE_ATTRIBUTE.get(
                requirement.profile_attribute or "",
                requirement.profile_attribute or "",
            )
            criterion = EligibilityCriterion(
                criterion_id=requirement.criterion_id or requirement.requirement_id,
                attribute=profile_attribute,
                operator=requirement.operator,
                value=requirement.value,
                source_id=requirement.citation.source_id or "",
                logic_group_id=requirement.logic_group_id,
                logic_group_operator=requirement.logic_group_operator,
                context_attribute=(
                    CANONICAL_TO_PROFILE_ATTRIBUTE.get(
                        requirement.context_attribute, requirement.context_attribute
                    )
                    if requirement.context_attribute is not None
                    else None
                ),
            )
            executable.append(
                {
                    "criterion": criterion.model_dump(mode="json"),
                    "requirement_id": requirement.requirement_id,
                    "statement": requirement.statement,
                    "threshold_basis": requirement.threshold_basis,
                    "applicability": requirement.applicability,
                    "citation": requirement.citation.model_dump(
                        mode="json", exclude_none=True
                    ),
                }
            )

        serialized_request = json.dumps(
            request.model_dump(mode="json", exclude_none=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        rule_package_id = (
            "criteria-"
            + hashlib.sha256(serialized_request.encode("utf-8")).hexdigest()[:32]
        )

        package: dict[str, object] | None = None
        if executable:
            preview_catalog_version = (
                request.catalog_version
                if all(
                    source.approval_status == "approved" for source in request.sources
                )
                else "request-scoped"
            )
            record = ProgramRecord(
                catalog_version=preview_catalog_version,
                program_id=request.program_id,
                navigator=request.navigator,
                title=request.title,
                description=request.description,
                jurisdiction=request.jurisdiction,
                data_origin="public_source",
                sources=request.sources,
                criteria=[item["criterion"] for item in executable],
                source_limitations=request.source_limitations,
            )
            package = record.model_dump(mode="json")

        mapped_count = sum(
            item.mapping_status == "mapped" and item.requirement_type == "eligibility"
            for item in request.requirements
        )
        return {
            "contract_version": "program-criteria-package-preview-v1",
            "status": "candidate_requires_review",
            "criteria_package_id": rule_package_id,
            "program_id": request.program_id,
            "navigator": request.navigator,
            "compiled_rules": executable,
            "unresolved_requirements": unresolved,
            "program_record_preview": package,
            "coverage": {
                "source_completeness": "not_proven",
                "source_completeness_reason": (
                    "The supplied clauses are source-backed candidates, but no "
                    "evidence establishes that they include every applicable "
                    "program requirement."
                ),
                "source_completeness_action": "review_source_scope_and_effective_dates",
                "requirement_count": len(request.requirements),
                "declared_mapped_eligibility_count": mapped_count,
                "compiled_candidate_count": len(executable),
                "unresolved_count": len(unresolved),
                "active_rule_count": 0,
            },
            "governance": {
                "review_status": "review_required",
                "approved": False,
                "published": False,
                "persistence_performed": False,
                "eligibility_outcome": None,
            },
        }

    def _unresolved_reason(self, requirement: RequirementCandidate) -> str | None:
        if requirement.requirement_type != "eligibility":
            return "not_an_eligibility_requirement"
        if requirement.mapping_status != "mapped":
            return requirement.unresolved_reason or requirement.mapping_status
        if not all(
            (
                requirement.citation.source_version,
                requirement.citation.source_content_sha256,
                requirement.citation.locator,
                requirement.citation.excerpt,
            )
        ):
            return "source_citation_needs_version_hash_locator_and_excerpt"
        if requirement.threshold_basis is not None and requirement.operator != "lte_by":
            return "threshold_basis_not_supported_by_current_evaluator"
        if requirement.applicability is not None:
            return "applicability_not_supported_by_current_evaluator"
        profile_attribute = CANONICAL_TO_PROFILE_ATTRIBUTE.get(
            requirement.profile_attribute or "", requirement.profile_attribute
        )
        if not isinstance(profile_attribute, str) or not re.fullmatch(
            r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$", profile_attribute
        ):
            return "canonical_attribute_is_invalid"
        supported_units = {
            "years_farming": {"years"},
            "acreage": {"acres"},
            "household_size": {"persons"},
            "household_adults": {"persons"},
            "household_children": {"persons"},
            "annual_household_income_usd": {"USD/year"},
            "monthly_gross_income_usd": {"USD/month"},
            "monthly_net_income_usd": {"USD/month"},
        }
        known_unit = supported_units.get(profile_attribute)
        if known_unit is not None and requirement.unit not in {None, *known_unit}:
            return "unit_not_supported_by_current_profile_mapping"
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,79}", requirement.requirement_id):
            return "requirement_id_not_valid_for_rule_contract"
        if requirement.operator == "lte_by":
            context_attribute = CANONICAL_TO_PROFILE_ATTRIBUTE.get(
                requirement.context_attribute or "",
                requirement.context_attribute,
            )
            if context_attribute is None or not re.fullmatch(
                r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$",
                context_attribute,
            ):
                return "threshold_context_has_no_versioned_profile_mapping"
            if not isinstance(requirement.value, dict) or not requirement.value:
                return "threshold_table_is_missing_or_invalid"
            if any(
                not key.isdigit()
                or int(key) < 1
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < 0
                for key, value in requirement.value.items()
            ):
                return "threshold_table_is_missing_or_invalid"
        elif requirement.context_attribute is not None:
            return "context_attribute_requires_lte_by_operator"
        if requirement.citation.source_id is None:
            return "source_id_missing_from_citation"
        return None


def _requirement_gap_diagnostic(
    requirement: RequirementCandidate, reason: str
) -> dict[str, object]:
    """Explain why a criterion is unresolved and identify the responsible next step.

    This compiler has no applicant profile values, so it never recommends asking
    the user. Profile questions are generated only by evaluation when a required
    profile fact is actually absent or ambiguous.
    """
    normalized = reason.casefold()
    if (
        reason == "not_an_eligibility_requirement"
        or requirement.mapping_status == "context_only"
    ):
        return {
            "reason_code": "not_an_eligibility_predicate",
            "explanation": (
                "This statement is context or application guidance, not an "
                "eligibility predicate."
            ),
            "recommended_action": "retain_as_program_context",
            "ask_user": False,
        }
    if any(
        token in normalized for token in ("citation", "source_id", "source evidence")
    ):
        return {
            "reason_code": "source_evidence_incomplete",
            "explanation": (
                "The clause lacks source lineage needed for a reviewable rule."
            ),
            "recommended_action": "complete_or_refresh_source_citation",
            "ask_user": False,
        }
    if any(token in normalized for token in ("compound", "logic_group")):
        return {
            "reason_code": "compound_logic_not_supported",
            "explanation": (
                "The clause belongs to a compound rule whose membership or "
                "logic is incomplete or unsupported."
            ),
            "recommended_action": "map_all_compound_clauses_and_logic",
            "ask_user": False,
        }
    if any(
        token in normalized
        for token in (
            "threshold",
            "applicability",
            "temporal",
            "date-sensitive",
            "relationship",
        )
    ):
        return {
            "reason_code": "rule_semantics_not_supported",
            "explanation": (
                "The source rule depends on a threshold, applicability, time, "
                "or relationship condition the evaluator cannot represent safely."
            ),
            "recommended_action": "extend_rule_model_or_keep_unresolved",
            "ask_user": False,
        }
    if any(
        token in normalized
        for token in (
            "attribute",
            "profile",
            "schema",
            "mapping",
            "unit",
            "not equivalent",
        )
    ):
        return {
            "reason_code": "profile_fact_mapping_gap",
            "explanation": (
                "No compatible versioned profile fact and mapping represents "
                "this requirement. This is a schema or mapping gap, not a "
                "missing user answer."
            ),
            "recommended_action": "add_or_correct_profile_fact_mapping",
            "ask_user": False,
        }
    return {
        "reason_code": "criteria_mapping_unresolved",
        "explanation": (
            "The requirement is not mapped to a supported deterministic rule; "
            "review the source statement and mapping rationale."
        ),
        "recommended_action": "review_requirement_mapping",
        "ask_user": False,
    }


class CandidateListing(StrictModel):
    """Criteria-free fields extracted from one source record."""

    listing_kind: Literal["funding_opportunity", "assistance_program"] | None = None
    program_number: str | None = Field(default=None, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    summary: str | None = Field(default=None, max_length=1200)
    agency: str | None = Field(default=None, max_length=300)
    audience: list[str] = Field(default_factory=list, max_length=40)
    jurisdiction_code: str | None = Field(default=None, max_length=100)
    status: str | None = Field(default=None, max_length=100)
    open_date: str | None = Field(default=None, max_length=40)
    close_date: str | None = Field(default=None, max_length=40)
    canonical_url: HttpUrl | None = None


class FieldLineage(StrictModel):
    """Map one normalized listing field back to its source record path."""

    field_id: Literal[
        "listing_kind",
        "program_number",
        "title",
        "summary",
        "agency",
        "audience",
        "jurisdiction_code",
        "status",
        "open_date",
        "close_date",
        "canonical_url",
    ]
    source_path: str = Field(min_length=1, max_length=300)
    citation: CandidateCitation | None = None


class ProgramCandidateRequest(StrictModel):
    """Caller-supplied source record and evidence; never treated as approved."""

    source_id: str = Field(
        min_length=2, max_length=80, pattern=r"^[a-z0-9][a-z0-9._-]+$"
    )
    source_record_id: str = Field(min_length=1, max_length=200)
    source_version: str = Field(min_length=1, max_length=120)
    source_url: HttpUrl
    retrieved_at: datetime
    source_content_sha256: str | None = Field(
        default=None, pattern=r"^[a-fA-F0-9]{64}$"
    )
    selected_program_id: str | None = Field(
        default=None,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._-]{1,99}$",
    )
    listing: CandidateListing
    field_lineage: list[FieldLineage] = Field(default_factory=list, max_length=40)
    criteria_status: Literal["not_assessed", "unavailable"] = "not_assessed"
    criteria_unavailable_reason: str | None = Field(default=None, max_length=500)
    proposed_criteria: list[ProposedCriterion] = Field(
        default_factory=list, max_length=30
    )
    requirement_candidates: list[RequirementCandidate] = Field(
        default_factory=list, max_length=100
    )

    @model_validator(mode="after")
    def validate_candidate_provenance(self) -> ProgramCandidateRequest:
        """Require HTTPS source URLs and a timezone-aware retrieval time."""
        if self.source_url.scheme != "https":
            raise ValueError("Candidate source URLs must use HTTPS.")
        if self.retrieved_at.utcoffset() is None:
            raise ValueError("Source retrieval time needs a timezone.")
        if (
            self.criteria_status == "unavailable"
            and not self.criteria_unavailable_reason
        ):
            raise ValueError("Unavailable criteria require a reason.")
        if self.criteria_status == "unavailable" and self.proposed_criteria:
            raise ValueError("Unavailable criteria cannot include proposed rules.")
        if self.listing.canonical_url and self.listing.canonical_url.scheme != "https":
            raise ValueError("Canonical program links must use HTTPS.")
        field_ids = [item.field_id for item in self.field_lineage]
        if len(field_ids) != len(set(field_ids)):
            raise ValueError("Each normalized field can have only one lineage entry.")
        listing = self.listing.model_dump(mode="json", exclude_none=True)
        if any(field_id not in listing for field_id in field_ids):
            raise ValueError("Field lineage cannot reference an omitted listing field.")
        return self


def _candidate_identity(request: ProgramCandidateRequest) -> tuple[str, str]:
    """Return an opaque source-scoped reference and the identity basis used."""
    program_number = request.listing.program_number
    if program_number is not None and program_number.strip():
        basis = "publisher_program_number"
        value = program_number.strip()
    elif request.listing.canonical_url is not None:
        basis = "canonical_url"
        value = str(request.listing.canonical_url)
    else:
        basis = "source_record_id"
        value = request.source_record_id

    identity = json.dumps(
        {"source_id": request.source_id, "basis": basis, "value": value},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    reference = (
        "candidate-ref-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    )
    return reference, basis


def _canonical_program_url(url: str) -> str:
    """Normalize tracking-only URL differences without conflating source pages."""
    parts = urlsplit(url)
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            path,
            urlencode(query),
            "",
        )
    )


def _stable_program_id(request: ProgramCandidateRequest) -> tuple[str, str]:
    """Resolve an exact selected ID or derive a reusable ID from strong identity."""
    if request.selected_program_id is not None:
        return request.selected_program_id, "selected_catalog_program_id"

    program_number = request.listing.program_number
    agency = " ".join((request.listing.agency or "").casefold().split())
    if program_number is not None and program_number.strip() and agency:
        basis = "publisher_program_number"
        identity = {
            "agency": agency,
            "program_number": " ".join(program_number.casefold().split()),
        }
    elif request.listing.canonical_url is not None:
        basis = "canonical_url"
        identity = {
            "canonical_url": _canonical_program_url(str(request.listing.canonical_url))
        }
    else:
        basis = "source_record_id"
        identity = {
            "source_id": request.source_id,
            "source_record_id": request.source_record_id,
        }

    serialized = json.dumps(
        {"basis": basis, "identity": identity},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return (
        "program-ref-" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32],
        basis,
    )


class ProgramCandidateNormalizer:
    """Normalize a fixed source claim into a deterministic review-only record."""

    def normalize(self, request: ProgramCandidateRequest) -> dict[str, object]:
        """Normalize a candidate: derive its ids, omissions and canonical digest."""
        payload = request.model_dump(mode="json", exclude_none=True)
        serialized = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        candidate_id = (
            "candidate-" + hashlib.sha256(serialized.encode()).hexdigest()[:32]
        )
        candidate_ref, identity_basis = _candidate_identity(request)
        program_id, program_identity_basis = _stable_program_id(request)
        listing = request.listing.model_dump(mode="json", exclude_none=True)
        omissions = [
            field
            for field in (
                "listing_kind",
                "program_number",
                "summary",
                "agency",
                "audience",
                "jurisdiction_code",
                "status",
                "open_date",
                "close_date",
                "canonical_url",
            )
            if field not in listing or listing[field] == []
        ]
        findings: list[dict[str, str]] = []
        if not request.proposed_criteria and request.criteria_status == "not_assessed":
            findings.append(
                {
                    "code": "criteria_not_assessed",
                    "message": (
                        "No cited criteria mapping was supplied; eligibility "
                        "remains unassessed."
                    ),
                }
            )
        if request.proposed_criteria:
            findings.append(
                {
                    "code": "criteria_require_review",
                    "message": (
                        "Cited criteria are source claims and require review; "
                        "this operation does not verify or evaluate them."
                    ),
                }
            )
        if request.requirement_candidates:
            findings.append(
                {
                    "code": "requirements_require_review",
                    "message": (
                        "Extracted requirement statements and proposed profile "
                        "mappings are candidates only; source coverage and every "
                        "mapping require review before publication."
                    ),
                }
            )
        mapped_count = sum(
            item.mapping_status == "mapped" for item in request.requirement_candidates
        )
        partial_count = sum(
            item.mapping_status == "partial" for item in request.requirement_candidates
        )
        unmapped_count = sum(
            item.mapping_status in {"not_mapped", "context_only"}
            for item in request.requirement_candidates
        )
        for field in ("open_date", "close_date"):
            value = listing.get(field)
            if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
                findings.append(
                    {
                        "code": "date_format_unrecognized",
                        "field": field,
                        "message": (
                            "Date is preserved as supplied; use an ISO 8601 "
                            "calendar date for machine comparison."
                        ),
                    }
                )
        if request.listing.jurisdiction_code is None:
            findings.append(
                {
                    "code": "jurisdiction_unresolved",
                    "field": "jurisdiction_code",
                    "message": (
                        "Jurisdiction was omitted and was not inferred from "
                        "the source or user context."
                    ),
                }
            )
        provided_lineage = {item.field_id for item in request.field_lineage}
        missing_lineage = sorted(
            field
            for field, value in listing.items()
            if value not in (None, []) and field not in provided_lineage
        )
        if missing_lineage:
            findings.append(
                {
                    "code": "field_lineage_missing",
                    "message": (
                        "Source paths are missing for one or more normalized "
                        "listing fields."
                    ),
                    "fields": ", ".join(missing_lineage),
                }
            )

        return {
            "contract_version": "program-candidate-v3",
            "status": "candidate_ready_for_review",
            "candidate_id": candidate_id,
            "candidate_ref": candidate_ref,
            "candidate_identity_basis": identity_basis,
            "program_id": program_id,
            "program_identity_basis": program_identity_basis,
            "source_lineage": {
                "source_id": request.source_id,
                "source_record_id": request.source_record_id,
                "source_version": request.source_version,
                "source_url": str(request.source_url),
                "retrieved_at": request.retrieved_at.isoformat(),
                "source_content_sha256": request.source_content_sha256,
            },
            "listing": listing,
            "field_lineage": {
                item.field_id: {
                    "source_id": (
                        item.citation.source_id or request.source_id
                        if item.citation
                        else request.source_id
                    ),
                    "source_record_id": (
                        item.citation.source_record_id
                        if item.citation
                        else request.source_record_id
                    ),
                    "source_url": str(
                        item.citation.source_url
                        if item.citation
                        else request.source_url
                    ),
                    "source_version": (
                        item.citation.source_version or request.source_version
                        if item.citation
                        else request.source_version
                    ),
                    "source_content_sha256": (
                        item.citation.source_content_sha256
                        or request.source_content_sha256
                        if item.citation
                        else request.source_content_sha256
                    ),
                    "source_path": item.source_path,
                    "retrieved_at": (
                        item.citation.retrieved_at.isoformat()
                        if item.citation
                        else request.retrieved_at.isoformat()
                    ),
                }
                for item in request.field_lineage
            },
            "omitted_fields": omissions,
            "criteria_mapping": {
                "status": (
                    "candidate_requires_review"
                    if request.proposed_criteria or request.requirement_candidates
                    else request.criteria_status
                ),
                "review_status": "not_reviewed",
                "proposed_criteria": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in request.proposed_criteria
                ],
                "requirement_candidates": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in request.requirement_candidates
                ],
                "coverage": {
                    "source_completeness": "not_proven",
                    "requirement_count": len(request.requirement_candidates),
                    "mapped_count": mapped_count,
                    "partial_count": partial_count,
                    "unmapped_or_context_only_count": unmapped_count,
                    "active_rule_count": 0,
                },
                "unavailable_reason": request.criteria_unavailable_reason,
            },
            "validation_findings": findings,
            "governance": {
                "source_verified": False,
                "review_status": "review_required",
                "catalog_status": "not_published",
                "approval_status": "not_reviewed",
                "persistence_performed": False,
                "tenant_semantics_changed": False,
                "eligibility_outcome": None,
            },
        }


class ProgramRequirementExtractor:
    """Extract likely requirement sentences without inferring executable rules."""

    _requirement_cue = re.compile(
        r"\b(?:must|shall|required|eligib(?:le|ility)|qualif(?:y|ies|ied)|"
        r"not eligible|not qualify|cannot|may not|at least|no more than|"
        r"(?:has|have|had|does|do|is|are|must|shall|may)\s+not|"
        r"substantial(?:ly)?\s+participat(?:e|es|ing|ion)|"
        r"income limit|resource limit|within \d+ days|apply by|deadline|"
        r"application period|household size|service area|reside in)\b",
        re.IGNORECASE,
    )

    def extract(self, request: RequirementExtractionRequest) -> dict[str, object]:
        """Return cited paragraph excerpts with requirement cues and no rules.

        Callers should preserve paragraph breaks with blank lines. The bounded
        scan emits one candidate per cue-matching paragraph and does not split
        a concatenated paragraph into individual sentences.
        """
        paragraphs = [
            re.sub(r"\s+", " ", paragraph).strip()
            for paragraph in re.split(r"\n\s*\n", request.source_text)
        ]
        candidates: list[dict[str, object]] = []
        truncated_count = 0
        requirement_list_context = False
        for index, paragraph in enumerate(paragraphs, start=1):
            if not paragraph:
                continue
            is_list_item = paragraph.startswith("- ")
            statement = paragraph[2:] if is_list_item else paragraph
            has_requirement_cue = bool(self._requirement_cue.search(statement))
            if not has_requirement_cue and not (
                is_list_item and requirement_list_context
            ):
                if not is_list_item:
                    requirement_list_context = False
                continue
            clean_statement = statement[:1200]
            truncated_count += int(len(paragraph) > 1200)
            statement_id = hashlib.sha256(
                "\0".join(
                    (
                        request.source_id,
                        request.source_record_id,
                        str(index),
                        paragraph,
                    )
                ).encode("utf-8")
            ).hexdigest()[:24]
            locator = f"{request.source_locator or 'source'}: paragraph {index}"
            candidates.append(
                {
                    "requirement_id": f"req-{statement_id}",
                    "statement": clean_statement[:1200],
                    "requirement_type": "unclassified",
                    "mapping_status": "not_mapped",
                    "citation": CandidateCitation(
                        source_url=request.source_url,
                        source_record_id=request.source_record_id,
                        retrieved_at=request.retrieved_at,
                        source_id=request.source_id,
                        source_version=request.source_version,
                        source_content_sha256=request.source_content_sha256,
                        source_visible_text_sha256=request.source_visible_text_sha256,
                        locator=locator[:300],
                        excerpt=paragraph[:500],
                    ).model_dump(mode="json", exclude_none=True),
                }
            )
            if not is_list_item:
                requirement_list_context = bool(
                    has_requirement_cue and statement.endswith(":")
                )
        return {
            "contract_version": "program-requirements-extraction-v1",
            "status": "candidate_only",
            "source_lineage": {
                "source_id": request.source_id,
                "source_record_id": request.source_record_id,
                "source_version": request.source_version,
                "source_url": str(request.source_url),
                "retrieved_at": request.retrieved_at.isoformat(),
                "source_content_sha256": request.source_content_sha256,
                "source_visible_text_sha256": request.source_visible_text_sha256,
                "source_locator": request.source_locator,
            },
            "source_completeness": "not_proven",
            "extraction_method": "bounded_requirement_cue_scan_v1",
            "scanned_paragraph_count": len(paragraphs),
            "requirement_candidates": candidates[:100],
            "omitted_candidate_count": max(0, len(candidates) - 100),
            "truncated_candidate_count": truncated_count,
            "eligibility_rules_created": 0,
            "persistence_performed": False,
            "next_action": (
                "Review the cited statements against the complete source, mark "
                "each as eligibility, application, availability, or context, "
                "then map only supported eligibility clauses to versioned "
                "profile facts before candidate normalization."
            ),
        }

    def extract_normalized(
        self, request: NormalizedCriteriaExtractionRequest
    ) -> dict[str, object]:
        """Project cited clauses and only map explicit supported rule fields."""
        candidates: list[dict[str, object]] = []
        truncated_count = 0
        for index, clause in enumerate(request.criteria_clauses, start=1):
            clause_json = json.dumps(
                clause, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            )
            statement_value = clause.get("statement")
            statement = (
                statement_value.strip()
                if isinstance(statement_value, str) and statement_value.strip()
                else json.dumps(
                    clause, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                )
            )
            truncated_count += int(len(statement) > 1200)
            statement = statement[:1200]
            requirement_identifier = next(
                (
                    clause[key]
                    for key in ("requirement_id", "id")
                    if isinstance(clause.get(key), str) and clause[key].strip()
                ),
                None,
            )
            legacy_criterion_identifier = clause.get("criterion_id")
            requirement_id = (
                requirement_identifier
                or (
                    legacy_criterion_identifier
                    if isinstance(legacy_criterion_identifier, str)
                    and legacy_criterion_identifier.strip()
                    else None
                )
                or (
                    "req-"
                    + hashlib.sha256(
                        "\0".join(
                            (
                                request.source_id,
                                request.source_record_id,
                                str(index),
                                clause_json,
                            )
                        ).encode("utf-8")
                    ).hexdigest()[:24]
                )
            )
            declared_status = clause.get("mapping_status", clause.get("logic_status"))
            declared_type = clause.get("requirement_type")
            recognized_types = {
                "eligibility",
                "application",
                "availability",
                "context",
                "unclassified",
            }
            requirement_type = (
                declared_type
                if isinstance(declared_type, str) and declared_type in recognized_types
                else "unclassified"
            )
            mapping_status = "not_mapped"
            unresolved_reason = (
                "Source clause is preserved for review; profile mapping "
                "is not inferred."
            )
            mapping: dict[str, object] = {}
            table_locator_suffix: str | None = None

            if declared_status == "context_only":
                mapping_status = "context_only"
                requirement_type = "context"
                unresolved_reason = None
            elif declared_status == "partial":
                mapping_status = "partial"
                unresolved_reason = str(
                    clause.get("unresolved_reason")
                    or "Source clause has only a partial profile mapping."
                )[:500]
            elif (declared_status is None or declared_status == "mapped") and (
                requirement_type in {"unclassified", "eligibility"}
            ):
                mapping, unresolved_reason = self._explicit_normalized_mapping(
                    clause, request.source_id
                )
                if mapping:
                    suffix = mapping.pop("table_locator_suffix", None)
                    table_locator_suffix = suffix if isinstance(suffix, str) else None
                    mapping_status = "mapped"
                    requirement_type = "eligibility"
            locator = f"{request.source_locator}[{index - 1}]"
            if table_locator_suffix is not None:
                locator += table_locator_suffix
            candidates.append(
                {
                    "requirement_id": str(requirement_id)[:160],
                    **(
                        {"criterion_id": legacy_criterion_identifier[:160]}
                        if isinstance(legacy_criterion_identifier, str)
                        and legacy_criterion_identifier.strip()
                        and legacy_criterion_identifier != requirement_id
                        else {}
                    ),
                    "statement": statement,
                    "requirement_type": requirement_type,
                    "mapping_status": mapping_status,
                    **mapping,
                    "unresolved_reason": unresolved_reason,
                    "citation": CandidateCitation(
                        source_url=request.source_url,
                        source_record_id=request.source_record_id,
                        retrieved_at=request.retrieved_at,
                        source_id=request.source_id,
                        source_version=request.source_version,
                        source_content_sha256=request.source_content_sha256,
                        source_visible_text_sha256=request.source_visible_text_sha256,
                        locator=locator[:300],
                        excerpt=statement[:500],
                    ).model_dump(mode="json", exclude_none=True),
                }
            )
        return {
            "contract_version": "program-requirements-extraction-v1",
            "status": "candidate_only",
            "source_lineage": {
                "source_id": request.source_id,
                "source_record_id": request.source_record_id,
                "source_version": request.source_version,
                "source_url": str(request.source_url),
                "retrieved_at": request.retrieved_at.isoformat(),
                "source_content_sha256": request.source_content_sha256,
                "source_visible_text_sha256": request.source_visible_text_sha256,
                "source_locator": request.source_locator,
            },
            "source_completeness": "not_proven",
            "extraction_method": "normalized_criteria_clause_projection_v2",
            "scanned_paragraph_count": len(request.criteria_clauses),
            "requirement_candidates": candidates,
            "omitted_candidate_count": 0,
            "truncated_candidate_count": truncated_count,
            "eligibility_rules_created": 0,
            "persistence_performed": False,
            "next_action": (
                "Review each source clause and its locator against the "
                "complete source. "
                "Review any projected explicit canonical mapping, classify its "
                "role, and map only supported eligibility conditions to "
                "compatible versioned profile facts."
            ),
        }

    @staticmethod
    def _explicit_normalized_mapping(
        clause: dict[str, object], source_id: str
    ) -> tuple[dict[str, object], str | None]:
        """Project a complete declared rule only through the canonical field map."""
        attribute = clause.get("attribute", clause.get("profile_attribute"))
        operator = clause.get("operator")
        value = clause.get("value")
        allowed_operators = {
            "eq",
            "ne",
            "gt",
            "gte",
            "lt",
            "lte",
            "lte_by",
            "one_of",
            "exists",
        }
        if not isinstance(attribute, str) or not re.fullmatch(
            r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$", attribute
        ):
            return {}, "canonical_attribute_has_no_versioned_profile_mapping"
        if not isinstance(operator, str) or operator not in allowed_operators:
            return {}, "explicit_rule_operator_is_missing_or_unsupported"
        table_locator_suffix: str | None = None
        if "table" in clause:
            if operator != "lte_by":
                return {}, "threshold_table_requires_lte_by_operator"
            value, table_locator_suffix, table_error = (
                ProgramRequirementExtractor._threshold_table_from_rows(
                    clause.get("table")
                )
            )
            if table_error is not None:
                return {}, table_error
        if not ProgramRequirementExtractor._valid_explicit_rule_value(operator, value):
            return {}, "explicit_rule_value_is_missing_or_incompatible_with_operator"
        clause_source_id = clause.get("source_id")
        if clause_source_id is not None and clause_source_id != source_id:
            return {}, "criterion_source_id_does_not_match_record_source"

        concept_id = clause.get("concept_id", attribute)
        if not isinstance(concept_id, str) or not concept_id.strip():
            return {}, "explicit_rule_concept_id_is_invalid"
        mapping: dict[str, object] = {
            "concept_id": concept_id,
            "profile_attribute": attribute,
            "operator": operator,
            "value": value,
        }
        context_attribute = clause.get("context_attribute")
        if operator == "lte_by":
            if not isinstance(context_attribute, str):
                return {}, "threshold_context_attribute_is_missing_or_invalid"
            mapping["context_attribute"] = context_attribute
        elif context_attribute is not None:
            return {}, "context_attribute_requires_lte_by_operator"
        for key in (
            "unit",
            "threshold_basis",
            "applicability",
            "logic_group_id",
            "logic_group_operator",
        ):
            if key in clause:
                mapping[key] = clause[key]
        if table_locator_suffix is not None:
            mapping["table_locator_suffix"] = table_locator_suffix
        return mapping, None

    @staticmethod
    def _threshold_table_from_rows(
        table: object,
    ) -> tuple[dict[str, int | float] | None, str | None, str | None]:
        """Build a bounded numeric lookup from explicitly selected source rows."""
        if not isinstance(table, dict):
            return None, None, "threshold_table_descriptor_is_invalid"
        rows = table.get("rows")
        key_field = table.get("key_field")
        value_field = table.get("value_field")
        row_filters = table.get("filters", table.get("filter"))
        if (
            not isinstance(rows, list)
            or not rows
            or len(rows) > 500
            or not isinstance(key_field, str)
            or not key_field
            or not isinstance(value_field, str)
            or not value_field
        ):
            return None, None, "threshold_table_descriptor_is_invalid"
        if row_filters is None:
            filters: dict[str, object] = {}
        elif isinstance(row_filters, dict) and "field" in row_filters:
            filter_field = row_filters.get("field")
            filter_value = row_filters.get("equals")
            if (
                not isinstance(filter_field, str)
                or not filter_field
                or not isinstance(filter_value, (str, int, float, bool))
            ):
                return None, None, "threshold_table_filter_is_invalid"
            filters = {filter_field: filter_value}
        elif isinstance(row_filters, dict) and row_filters:
            if any(
                not isinstance(field, str)
                or not field
                or not isinstance(value, (str, int, float, bool))
                for field, value in row_filters.items()
            ):
                return None, None, "threshold_table_filter_is_invalid"
            filters = row_filters
        else:
            return None, None, "threshold_table_filter_is_invalid"

        selected_rows: list[dict[str, object]] = []
        for row in rows:
            if not isinstance(row, dict):
                return None, None, "threshold_table_contains_an_invalid_row"
            if all(row.get(field) == value for field, value in filters.items()):
                selected_rows.append(row)
        if not selected_rows:
            return None, None, "threshold_table_filter_matched_no_rows"

        thresholds: dict[str, int | float] = {}
        for row in selected_rows:
            key = row.get(key_field)
            threshold = row.get(value_field)
            if (
                not isinstance(key, int)
                or isinstance(key, bool)
                or key < 1
                or isinstance(threshold, bool)
                or not isinstance(threshold, (int, float))
                or threshold < 0
            ):
                return None, None, "threshold_table_contains_an_invalid_key_or_value"
            if str(key) in thresholds:
                return None, None, "threshold_table_contains_duplicate_keys"
            thresholds[str(key)] = threshold

        locator_suffix = (
            "["
            + ";".join(f"{field}={filters[field]}" for field in sorted(filters))
            + "]"
            if filters
            else "[table]"
        )
        return thresholds, locator_suffix, None

    @staticmethod
    def _valid_explicit_rule_value(operator: str, value: object) -> bool:
        """Match the evaluator's scalar and one_of value constraints."""
        scalar = isinstance(value, (str, int, float, bool)) and value is not None
        if operator == "lte_by":
            return (
                isinstance(value, dict)
                and bool(value)
                and all(
                    isinstance(key, str)
                    and key.isdigit()
                    and int(key) >= 1
                    and isinstance(item, (int, float))
                    and not isinstance(item, bool)
                    and item >= 0
                    for key, item in value.items()
                )
            )
        if operator == "one_of":
            return (
                isinstance(value, list)
                and bool(value)
                and all(isinstance(item, (str, int, float, bool)) for item in value)
            )
        if operator == "exists":
            return isinstance(value, bool)
        if operator in {"gt", "gte", "lt", "lte"}:
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        return scalar

    def extract_normalized_v1(
        self, request: NormalizedCriteriaExtractionRequestV1
    ) -> dict[str, object]:
        """Adapt the immutable 0.1.13 string-list request to object clauses."""
        object_request = NormalizedCriteriaExtractionRequest(
            source_id=request.source_id,
            source_record_id=request.source_record_id,
            source_version=request.source_version,
            source_url=request.source_url,
            retrieved_at=request.retrieved_at,
            source_content_sha256=request.source_content_sha256,
            source_visible_text_sha256=request.source_visible_text_sha256,
            source_locator=request.source_locator,
            criteria_clauses=[
                json.loads(clause) for clause in request.criteria_clauses_json
            ],
        )
        return self.extract_normalized(object_request)


def register_program_candidate_normalizer(app: FastMCP) -> None:
    """Register the read-only, no-network normalization operation."""

    normalizer = ProgramCandidateNormalizer()

    @app.tool(name="normalize_program_candidate")
    def normalize_program_candidate(
        source_id: str | None = None,
        source_record_id: str | None = None,
        source_version: str | None = None,
        source_url: HttpUrl | None = None,
        retrieved_at: datetime | None = None,
        source_content_sha256: str | None = None,
        selected_program_id: str | None = None,
        listing: CandidateListing | None = None,
        field_lineage: list[FieldLineage] | None = None,
        criteria_status: Literal["not_assessed", "unavailable"] | None = None,
        criteria_unavailable_reason: str | None = None,
        proposed_criteria: list[ProposedCriterion] | None = None,
        requirement_candidates: list[RequirementCandidate] | None = None,
        request: ProgramCandidateRequest | None = None,
    ) -> dict[str, object]:
        """Prepare one cited public program record and return a reusable program ID.

        Preserves exact source and field lineage, reports omitted and unresolved
        values, and keeps proposed criteria separate from discovery metadata. Copy
        selected_program_id only from the exact earlier discovery result; when it
        is absent, the server derives a stable ID from publisher identity.
        Source claims and criteria remain unverified; this operation performs no
        network fetch, catalog/profile write, approval, or eligibility evaluation.
        """
        flat_arguments = (
            source_id,
            source_record_id,
            source_version,
            source_url,
            retrieved_at,
            source_content_sha256,
            selected_program_id,
            listing,
            field_lineage,
            criteria_status,
            criteria_unavailable_reason,
            proposed_criteria,
            requirement_candidates,
        )
        if request is None:
            flat_payload = {
                key: value
                for key, value in {
                    "source_id": source_id,
                    "source_record_id": source_record_id,
                    "source_version": source_version,
                    "source_url": source_url,
                    "retrieved_at": retrieved_at,
                    "source_content_sha256": source_content_sha256,
                    "selected_program_id": selected_program_id,
                    "listing": listing,
                    "field_lineage": field_lineage,
                    "criteria_status": criteria_status,
                    "criteria_unavailable_reason": criteria_unavailable_reason,
                    "proposed_criteria": proposed_criteria,
                    "requirement_candidates": requirement_candidates,
                }.items()
                if value is not None
            }
            request = ProgramCandidateRequest.model_validate(flat_payload)
        elif any(value is not None for value in flat_arguments):
            raise ValueError("Use either flat arguments or the request envelope.")
        return normalizer.normalize(request)


def register_program_requirement_extractor(
    app: FastMCP, *, normalized_contract_version: Literal["v1", "v2"] | None = None
) -> None:
    """Register bounded extraction only on cluster versions that declare it."""

    extractor = ProgramRequirementExtractor()

    @app.tool(name="extract_program_requirements")
    def extract_program_requirements(
        source_id: str | None = None,
        source_record_id: str | None = None,
        source_version: str | None = None,
        source_url: HttpUrl | None = None,
        retrieved_at: datetime | None = None,
        source_content_sha256: str | None = None,
        source_visible_text_sha256: str | None = None,
        source_locator: str | None = None,
        source_text: str | None = None,
        request: RequirementExtractionRequest | None = None,
    ) -> dict[str, object]:
        """Extract cited requirement statement candidates from supplied source text.

        Input text must come from a selected public source record. The bounded
        cue scan is intentionally incomplete: preserve paragraph boundaries with
        blank lines, because each matching paragraph is returned as one candidate.
        It creates no eligibility rules, does not prove the document was fully
        read, and performs no fetch, write, approval, or profile evaluation.
        Review every candidate against its source.
        Accepts the published flat request schema and the legacy request envelope.
        """
        flat_arguments = (
            source_id,
            source_record_id,
            source_version,
            source_url,
            retrieved_at,
            source_content_sha256,
            source_visible_text_sha256,
            source_locator,
            source_text,
        )
        if request is None:
            flat_payload = {
                key: value
                for key, value in {
                    "source_id": source_id,
                    "source_record_id": source_record_id,
                    "source_version": source_version,
                    "source_url": source_url,
                    "retrieved_at": retrieved_at,
                    "source_content_sha256": source_content_sha256,
                    "source_visible_text_sha256": source_visible_text_sha256,
                    "source_locator": source_locator,
                    "source_text": source_text,
                }.items()
                if value is not None
            }
            request = RequirementExtractionRequest.model_validate(flat_payload)
        elif any(value is not None for value in flat_arguments):
            raise ValueError("Use either flat arguments or the request envelope.")
        return extractor.extract(request)

    if normalized_contract_version == "v1":

        @app.tool(name="extract_normalized_program_criteria")
        def extract_normalized_program_criteria_v1(
            request: NormalizedCriteriaExtractionRequestV1,
        ) -> dict[str, object]:
            """Project legacy JSON-string clauses into cited candidates.

            Preserves source lineage and declared mapping state without inferring
            profile rules, fetching, persisting, approving, publishing, or evaluating.
            """
            return extractor.extract_normalized_v1(request)

    elif normalized_contract_version == "v2":

        @app.tool(name="extract_normalized_program_criteria")
        def extract_normalized_program_criteria_v2(
            source_id: str | None = None,
            source_record_id: str | None = None,
            source_version: str | None = None,
            source_url: str | None = None,
            retrieved_at: datetime | None = None,
            source_content_sha256: str | None = None,
            source_visible_text_sha256: str | None = None,
            source_locator: str | None = None,
            criteria_clauses: list[dict[str, object]] | None = None,
            request: NormalizedCriteriaExtractionRequest | None = None,
        ) -> dict[str, object]:
            """Project selected inventory-record clauses into cited candidates.

            Accepts the published flat argument schema and the legacy request
            envelope, preserving inventory clauses without inferring rules.
            """
            if request is None:
                request = NormalizedCriteriaExtractionRequest.model_validate(
                    {
                        "source_id": source_id,
                        "source_record_id": source_record_id,
                        "source_version": source_version,
                        "source_url": source_url,
                        "retrieved_at": retrieved_at,
                        "source_content_sha256": source_content_sha256,
                        "source_visible_text_sha256": source_visible_text_sha256,
                        "source_locator": source_locator,
                        "criteria_clauses": criteria_clauses,
                    }
                )
            elif any(
                value is not None
                for value in (
                    source_id,
                    source_record_id,
                    source_version,
                    source_url,
                    retrieved_at,
                    source_content_sha256,
                    source_visible_text_sha256,
                    source_locator,
                    criteria_clauses,
                )
            ):
                raise ValueError("Use either flat arguments or the request envelope.")
            return extractor.extract_normalized(request)


def register_program_criteria_package_compiler(app: FastMCP) -> None:
    """Register deterministic review-only compiler for explicit rule mappings."""

    compiler = ProgramCriteriaPackageCompiler()

    @app.tool(name="prepare_program_criteria_package")
    def prepare_program_criteria_package(
        program_id: str | None = None,
        catalog_version: str | None = None,
        navigator: NavigatorId | None = None,
        title: str | None = None,
        description: str | None = None,
        jurisdiction: Jurisdiction | None = None,
        sources: list[SourceReference] | None = None,
        requirements: list[RequirementCandidate] | None = None,
        source_limitations: list[str] | None = None,
        request: ProgramCriteriaPackageRequest | None = None,
    ) -> dict[str, object]:
        """Compile cited, explicitly mapped criteria for review and test preview.

        Accepts the published flat request schema and the legacy request envelope.
        Unsupported mappings remain unresolved. The preview does not persist,
        approve, publish, or activate criteria and cannot return an outcome.
        """
        flat_arguments = (
            program_id,
            catalog_version,
            navigator,
            title,
            description,
            jurisdiction,
            sources,
            requirements,
            source_limitations,
        )
        if request is None:
            flat_payload = {
                key: value
                for key, value in {
                    "program_id": program_id,
                    "catalog_version": catalog_version,
                    "navigator": navigator,
                    "title": title,
                    "description": description,
                    "jurisdiction": jurisdiction,
                    "sources": sources,
                    "requirements": requirements,
                    "source_limitations": source_limitations,
                }.items()
                if value is not None
            }
            request = ProgramCriteriaPackageRequest.model_validate(flat_payload)
        elif any(value is not None for value in flat_arguments):
            raise ValueError("Use either flat arguments or the request envelope.")
        return compiler.compile(request)
