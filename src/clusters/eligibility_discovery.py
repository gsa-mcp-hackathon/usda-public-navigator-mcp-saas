"""Normalize fictional Eligibility profiles and list source-backed candidates.

This module performs catalog discovery only. It never evaluates program criteria
or infers profile facts from public or aggregate data.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from pydantic import Field, HttpUrl, ValidationError, model_validator

from clusters.eligibility_contracts import (
    NAVIGATOR_ATTRIBUTES,
    EligibilityAttribute,
    EligibilityCriterion,
    Jurisdiction,
    Profile,
    ProgramRecord,
    SourceBackedProgramDetail,
    SourceHealth,
    SourceReference,
    StrictModel,
)
from clusters.eligibility_geography import same_county
from clusters.eligibility_ingest import SourceSpec, load_source_specs
from clusters.eligibility_profile_readiness import (
    ProfileReadinessResult,
    build_profile_readiness,
)
from clusters.eligibility_versions import (
    INVENTORY_RECOVERY_VERSION,
    supports_eligibility_feature,
)

ActiveRulesetStatus = Literal[
    "active",
    "not_found",
    "unavailable",
    "source_mismatch",
    "invalid",
    "not_checked",
    "not_linked",
]

CANONICAL_TO_PROFILE_ATTRIBUTE = {
    "core.geography.state_code": "state_code",
    "core.geography.county_name": "county_name",
    "core.person.age_band": "age_band",
    "core.person.va_pension_status": "va_pension_status",
    "core.person.daily_care_need": "daily_care_need",
    "core.person.va_healthcare_status": "va_healthcare_status",
    "core.person.veteran.va_healthcare_status": "veteran_va_healthcare_status",
    "core.person.veteran.service_discharge_status": (
        "veteran_service_discharge_status"
    ),
    "core.person.veteran.service_connected_disability_rating_percent": (
        "veteran_service_connected_disability_rating_percent"
    ),
    "core.person.veteran.personal_care_need": "veteran_personal_care_need",
    "core.person.veteran.continuous_in_person_care_months": (
        "veteran_continuous_in_person_care_months"
    ),
    "core.person.caregiver.age_years": "caregiver_age_years",
    "core.person.caregiver.relationship_to_veteran": (
        "caregiver_relationship_to_veteran"
    ),
    "core.person.caregiver.lives_or_willing_to_live_full_time_with_veteran": (
        "caregiver_lives_or_willing_to_live_full_time_with_veteran"
    ),
    "core.farm_business.acreage": "acreage",
    "core.farm_business.commodities": "commodities",
    "core.farm_business.years_farming": "years_farming",
    "core.household.size": "household_size",
    "core.household.adults": "household_adults",
    "core.household.children": "household_children",
    "core.household.annual_income_usd": "annual_household_income_usd",
    "core.household.monthly_gross_income_usd": "monthly_gross_income_usd",
    "core.household.monthly_net_income_usd": "monthly_net_income_usd",
    # Canonical names for the remaining Navigator profile fields (core-v8, #975 B2).
    "core.geography.postal_code": "postal_code",
    "core.household.financial_resources_band": "financial_resources_band",
    "core.health.medicare_status": "medicare_status",
    "core.health.coverage_status": "health_coverage_status",
    "core.household.food_assistance_need": "food_assistance_need",
    "core.household.housing_status": "housing_status",
    "core.household.utility_assistance_need": "utility_assistance_need",
    "core.household.transportation_need": "transportation_need",
    "core.household.childcare_need": "childcare_need",
    "core.household.disaster_impact": "disaster_impact",
    "core.person.employment_status": "employment_status",
    "core.person.veteran_status": "veteran_status",
    "core.person.disability_status": "disability_status",
    "core.person.housebound_status": "housebound_status",
    "core.person.service_discharge_status": "service_discharge_status",
    "core.person.service_connected_disability_status": (
        "service_connected_disability_status"
    ),
    "core.person.caregiver.relationship": "caregiver_relationship",
    "core.farm_business.beginning_farmer_status": "beginning_farmer_status",
    "core.farm_business.land_tenure": "land_tenure",
    "core.farm_business.operation_type": "operation_type",
    "core.farm_business.operation_goal": "operation_goal",
    "core.farm_business.loss_type": "loss_type",
    "core.farm_business.loss_date": "loss_date",
    "core.farm_business.resource_concerns": "resource_concerns",
    "core.farm_business.current_practices": "current_practices",
    "core.farm_business.farm_type": "farm_type",
    "core.farm_business.crop_year": "crop_year",
    "core.farm_business.coverage_type": "coverage_type",
}
PROFILE_TO_CANONICAL_ATTRIBUTE = {
    profile_attribute: canonical_attribute
    for canonical_attribute, profile_attribute in CANONICAL_TO_PROFILE_ATTRIBUTE.items()
}

_SEARCH_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "for",
        "help",
        "i",
        "in",
        "me",
        "my",
        "of",
        "paying",
        "the",
        "to",
        "what",
        "with",
    }
)
_GENERIC_RELEVANCE_TERMS = frozenset(
    {"assistance", "help", "need", "on", "program", "programs", "support"}
)
# Broad domain words can occur in source audience labels without describing the
# need itself (for example, EQIP's ``water_management_entities`` audience).
_GENERIC_AUDIENCE_RELEVANCE_TERMS = frozenset({"water"})
_SEARCH_TERM_CANONICAL = {
    "heat": "energy",
    "heating": "energy",
    "farm": "farmer",
    "farming": "farmer",
    "utilities": "energy",
    "utility": "energy",
    "farmers": "farmer",
    "ranchers": "rancher",
    "veterans": "veteran",
    "caregivers": "caregiver",
    "producers": "producer",
    "landowners": "landowner",
    "senior": "older",
    "seniors": "older",
    "buy": "food_assistance",
    "buying": "food_assistance",
    "groceries": "food_assistance",
    "grocery": "food_assistance",
    "snap": "food_assistance",
    "ebt": "food_assistance",
    "pasture": "forage",
}
_QUERY_NEED_PHRASE_TERMS = {
    "elder care": "caregiver",
    "lights on": "energy",
    "electric bill": "energy",
    "power bill": "energy",
    "behind on rent": "housing",
    "rent assistance": "housing",
    "rental assistance": "housing",
    "eviction notice": "housing",
}
_FOOD_SUPPORT_CONTEXT_TERMS = frozenset(
    {"assistance", "benefit", "benefits", "program", "programs", "wic"}
)
_EXPLICIT_PERIOD_END_YEAR = re.compile(
    r"\b(?:before|prior\s+to|through|until)\s+"
    r"(?:[A-Za-z]+\s+\d{1,2},?\s+)?(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_SOURCE_YEAR = re.compile(r"\b(?P<year>(?:19|20)\d{2})\b")


class ProfileSubmission(StrictModel):
    """Validated synthetic profile plus explicitly reported ambiguities."""

    profile: Profile
    ambiguous_attributes: set[EligibilityAttribute] = Field(default_factory=set)

    @model_validator(mode="after")
    def ambiguities_belong_to_navigator(self) -> ProfileSubmission:
        """Keep ambiguity handling inside the Navigator contract."""
        return self


class NormalizedProfile(StrictModel):
    """Profile values separated from facts that remain unknown."""

    profile_id: str
    navigator: str
    schema_version: str
    known_facts: dict[str, object]
    unknown_facts: dict[str, Literal["missing", "ambiguous", "explicit_unknown"]]


class DiscoveryScope(StrictModel):
    """Tenant response context; it is not a catalog or jurisdiction allowlist."""

    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")


class ProgramCandidate(StrictModel):
    """Source-backed candidate metadata without an eligibility determination."""

    program_id: str
    navigator: str
    title: str
    catalog_version: str
    jurisdiction: Jurisdiction
    jurisdiction_match: Literal["confirmed", "unknown"]
    sources: list[SourceReference]
    source_health: list[SourceHealth]
    linked_listing_ids: list[str] = Field(default_factory=list)
    active_ruleset_status: ActiveRulesetStatus = Field(
        default="not_checked",
        description=(
            "Active persisted-rule read state. This is separate from source status "
            "and whether profile evaluation was performed."
        ),
    )
    active_ruleset_package_id: str | None = None
    active_ruleset_content_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )
    evaluation_available: bool = Field(
        default=False,
        description=(
            "True when the source-backed catalog program has mapped criteria "
            "or a source-matched active ruleset has an executable eligibility "
            "clause; this does not imply full criteria coverage."
        ),
    )
    coverage: str


class PublicListingRecord(StrictModel):
    """Source-backed discovery listing with no asserted eligibility criteria."""

    listing_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._:-]{2,160}$")
    record_role: Literal["program_listing", "referral"] = "program_listing"
    schema_version: Literal[
        "public-listing-v1",
        "public-listing-v2",
        "public-listing-v3",
        "public-listing-v4",
    ] = "public-listing-v1"
    record_kind: Literal[
        "funding_opportunity", "assistance_program", "referral_resource"
    ] = "funding_opportunity"
    program_number: str | None = None
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(default="", max_length=1200)
    agency: str = Field(min_length=1, max_length=300)
    applicant_types: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=40
    )
    status: str | None = Field(default=None, min_length=1, max_length=100)
    open_date: date | None = None
    close_date: date | None = None
    opportunity_url: HttpUrl | None = Field(default=None, max_length=2048)
    assistance_type: str | None = Field(default=None, max_length=600)
    applicant_description: str | None = Field(default=None, max_length=600)
    beneficiary_description: str | None = Field(default=None, max_length=600)
    program_url: HttpUrl | None = Field(default=None, max_length=2048)
    website_url: HttpUrl | None = Field(default=None, max_length=2048)
    published_date: date | None = None
    jurisdiction: Jurisdiction
    source: SourceReference
    criteria_status: Literal["not_assessed", "unavailable"]
    source_notice: str | None = Field(default=None, max_length=1200)
    related_program_ids: list[str] = Field(default_factory=list, max_length=20)
    active_ruleset_status: ActiveRulesetStatus = "not_checked"
    active_ruleset_package_id: str | None = None
    active_ruleset_content_sha256: str | None = Field(
        default=None, pattern=r"^[a-f0-9]{64}$"
    )

    @model_validator(mode="after")
    def listing_shape_is_valid(self) -> PublicListingRecord:
        """Check the listing record matches its declared schema."""
        if self.schema_version == "public-listing-v1":
            if self.record_kind != "funding_opportunity":
                raise ValueError("public-listing-v1 is for funding opportunities.")
            if self.status is None or self.opportunity_url is None:
                raise ValueError(
                    "Funding opportunities require status and an opportunity URL."
                )
        elif self.schema_version == "public-listing-v2":
            if self.record_kind != "assistance_program":
                raise ValueError("public-listing-v2 is for assistance programs.")
            if (
                self.status != "unknown"
                or self.program_number is None
                or self.opportunity_url is not None
            ):
                raise ValueError(
                    "Assistance programs require a program number, unknown status, "
                    "and no opportunity URL."
                )
        elif not any((self.opportunity_url, self.program_url, self.website_url)):
            raise ValueError("Generic public listings require a source-backed URL.")
        if any(
            link is not None and link.scheme != "https"
            for link in (self.opportunity_url, self.program_url, self.website_url)
        ):
            raise ValueError("Public listing links must use HTTPS.")
        return self


class UnassessedListingCandidate(StrictModel):
    """Shared listing surfaced to a tenant without asserting mapped criteria."""

    listing_id: str
    record_kind: Literal[
        "funding_opportunity", "assistance_program", "referral_resource"
    ]
    program_number: str | None = None
    title: str
    summary: str
    agency: str
    jurisdiction: Jurisdiction
    jurisdiction_match: Literal["confirmed", "unknown"]
    source: SourceReference
    source_health: SourceHealth
    related_program_ids: list[str] = Field(default_factory=list, max_length=20)
    criteria_status: Literal["not_assessed", "unavailable"]
    criteria_reference_status: Literal[
        "not_linked", "linked_review_only", "unavailable"
    ] = "not_linked"
    criteria_references: list[ProgramCriteriaReference] = Field(default_factory=list)
    active_ruleset_status: ActiveRulesetStatus = "not_checked"
    evaluation_available: bool = False


class CatalogReadFailure(StrictModel):
    """A Data API/catalog projection failure, distinct from source reachability."""

    dataset_id: str
    source_id: str
    reason_code: Literal[
        "data_api_read_failed",
        "invalid_catalog_response",
        "inconsistent_catalog_binding",
    ]


class DiscoveryResult(StrictModel):
    """Candidate discovery with mapped programs and unassessed listings visible."""

    contract_version: Literal[
        "eligibility-discovery-v2", "eligibility-discovery-v3"
    ] = "eligibility-discovery-v2"
    status: Literal["ok", "unavailable"]
    tenant_id: str
    navigator: str
    profile: NormalizedProfile
    catalog_versions: list[str]
    candidates: list[ProgramCandidate]
    unassessed_listings: list[UnassessedListingCandidate] = Field(default_factory=list)
    profile_readiness: ProfileReadinessResult | None = None
    source_health: list[SourceHealth]
    unavailable_sources: list[str]
    catalog_read_failures: list[CatalogReadFailure] = Field(default_factory=list)
    stale_sources: list[str]
    coverage: Literal["partial", "unavailable"]
    evaluation_performed: Literal[False] = False


class DatasetCatalog(StrictModel):
    """Validated records and source-health state for one active dataset."""

    dataset_id: str
    source_id: str
    programs: list[ProgramRecord]
    source_health: SourceHealth
    listings: list[PublicListingRecord] = Field(default_factory=list)
    criteria_references: list[ProgramCriteriaReference] = Field(default_factory=list)


class CriteriaSourceLink(StrictModel):
    """An explicitly reviewed source identity linked to a criteria program."""

    source: SourceReference
    basis: Literal["curated_source_crosswalk"]
    reviewed_at: str = Field(min_length=1, max_length=100)
    evidence_urls: list[HttpUrl] = Field(min_length=1, max_length=10)


class ProgramCriteriaReference(StrictModel):
    """Read-only pointer and cited details from a stored criteria record."""

    program_id: str = Field(min_length=1, max_length=200)
    dataset_id: str = Field(min_length=1, max_length=200)
    source_record_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=500)
    source_id: str = Field(min_length=1, max_length=128)
    source_url: HttpUrl
    source_version: str = Field(min_length=1, max_length=500)
    retrieved_at: str = Field(min_length=1, max_length=100)
    coverage: str = Field(min_length=1, max_length=100)
    details: list[SourceBackedProgramDetail] = Field(default_factory=list)
    assistance_listing_ids: list[str] = Field(default_factory=list, max_length=20)
    criteria_source_links: list[CriteriaSourceLink] = Field(default_factory=list)
    identity_link_basis: Literal["curated_source_crosswalk"] | None = None
    identity_link_reviewed_at: str | None = Field(default=None, max_length=100)
    identity_link_evidence_urls: list[HttpUrl] = Field(
        default_factory=list, max_length=10
    )
    evaluation_available: Literal[False] = Field(
        default=False,
        description=(
            "The catalog citation record itself is review-only; check the parent "
            "active_ruleset_status for separately persisted rules."
        ),
    )


class ClusterTenantBinding(StrictModel):
    """One tenant's interpretation of a shared public program record."""

    tenant_id: str
    pattern_version: str
    criteria_mapping: str | None = None
    criteria_comparison_available: bool


class ClusterInventoryCandidate(StrictModel):
    """Public program listing returned before tenant-specific rule evaluation."""

    record_type: Literal["eligibility_program", "public_listing"] = (
        "eligibility_program"
    )
    dataset_id: str
    source_record_id: str
    program_id: str | None = None
    listing_id: str | None = None
    linked_listing_ids: list[str] = Field(default_factory=list)
    record_role: Literal["program_listing", "referral"] | None = None
    listing_kind: (
        Literal["funding_opportunity", "assistance_program", "referral_resource"] | None
    ) = None
    program_number: str | None = None
    related_program_ids: list[str] = Field(default_factory=list, max_length=20)
    title: str
    description: str
    jurisdiction: Jurisdiction
    jurisdiction_match: Literal["confirmed", "unknown"]
    sources: list[SourceReference]
    source_health: list[SourceHealth]
    matching_terms: list[str]
    criteria_available: bool
    criteria_status: Literal["available", "not_assessed", "unavailable"]
    criteria_reference_status: Literal[
        "not_linked", "linked_review_only", "unavailable"
    ] = "not_linked"
    criteria_references: list[ProgramCriteriaReference] = Field(default_factory=list)
    active_ruleset_status: ActiveRulesetStatus = Field(
        default="not_checked",
        description=(
            "Active persisted-rule read state. This is separate from criteria "
            "assessment and source reachability."
        ),
    )
    evaluation_available: bool = Field(
        default=False,
        description=(
            "True when this source-backed program has executable catalog criteria "
            "or a source-matched active ruleset has an executable eligibility "
            "clause; this does not imply full criteria coverage."
        ),
    )
    listing_status: str | None = None
    applicant_types: list[str] = Field(default_factory=list)
    assistance_type: str | None = None
    applicant_description: str | None = None
    beneficiary_description: str | None = None
    open_date: str | None = None
    close_date: str | None = None
    opportunity_url: str | None = None
    program_url: str | None = None
    website_url: str | None = None
    published_date: str | None = None
    source_notice: str | None = None
    tenant_bindings: list[ClusterTenantBinding]

    @model_validator(mode="after")
    def validate_record_kind(self) -> ClusterInventoryCandidate:
        """Reject records whose kind and identifiers disagree."""
        if self.record_type == "eligibility_program":
            if self.program_id is None or self.listing_id is not None:
                raise ValueError(
                    "Eligibility program results require only a program ID."
                )
        elif (
            self.listing_id is None
            or self.program_id is not None
            or self.criteria_available
            or self.criteria_status == "available"
        ):
            raise ValueError(
                "Public listings require only a listing ID and cannot claim criteria."
            )
        return self


class InventorySearchNextAction(StrictModel):
    """Executable client action when inventory cannot support a relevant match."""

    operation_id: Literal["program.discovery.external.search"]
    need_query: str = Field(min_length=1, max_length=500)
    jurisdiction_code: str = Field(pattern=r"^US(?:-[A-Z]{2})?$")
    reason: Literal[
        "no_relevant_inventory_match",
        "catalog_coverage_incomplete",
        "inventory_unavailable",
    ]


class ClusterInventoryResult(StrictModel):
    """Bounded cluster-wide discovery result with incomplete coverage explicit."""

    contract_version: Literal["program-inventory-search-v2"] = (
        "program-inventory-search-v2"
    )
    status: Literal[
        "ok",
        "no_match",
        "no_coverage",
        "unavailable",
        "unsupported_jurisdiction",
        "invalid_request",
    ]
    cluster_id: str
    pattern_version: str
    candidates: list[ClusterInventoryCandidate]
    source_health: list[SourceHealth]
    unavailable_sources: list[str]
    catalog_read_failures: list[CatalogReadFailure] = Field(default_factory=list)
    coverage: Literal["partial", "unavailable"]
    catalog_truncated: bool = False
    evaluation_performed: Literal[False] = False
    relevance_status: Literal[
        "relevant_matches",
        "weak_matches",
        "no_relevant_match",
        "browse",
        "inventory_unavailable",
    ] = "no_relevant_match"
    weak_match_count: int = Field(default=0, ge=0)
    next_action: InventorySearchNextAction | None = None
    explanation: str


def normalize_profile(submission: ProfileSubmission) -> NormalizedProfile:
    """Preserve absent, ambiguous, and explicit-unknown facts as unknown."""
    profile = submission.profile
    known: dict[str, object] = {}
    unknown: dict[str, Literal["missing", "ambiguous", "explicit_unknown"]] = {}
    for attribute in sorted(NAVIGATOR_ATTRIBUTES.get(profile.navigator, set())):
        aliases = {attribute}
        if attribute in PROFILE_TO_CANONICAL_ATTRIBUTE:
            aliases.add(PROFILE_TO_CANONICAL_ATTRIBUTE[attribute])
        if attribute in CANONICAL_TO_PROFILE_ATTRIBUTE:
            aliases.add(CANONICAL_TO_PROFILE_ATTRIBUTE[attribute])
        if not hasattr(profile, attribute):
            unknown.update(dict.fromkeys(aliases, "missing"))
            continue
        value = getattr(profile, attribute)
        if attribute not in profile.model_fields_set:
            unknown.update(dict.fromkeys(aliases, "missing"))
        elif value is None or value == "unknown":
            unknown.update(dict.fromkeys(aliases, "explicit_unknown"))
        else:
            known.update(dict.fromkeys(aliases, value))

    profile_attributes = getattr(profile, "attributes", None)
    if isinstance(profile_attributes, Mapping):
        for attribute, value in profile_attributes.items():
            aliases = {attribute}
            if attribute in PROFILE_TO_CANONICAL_ATTRIBUTE:
                aliases.add(PROFILE_TO_CANONICAL_ATTRIBUTE[attribute])
            if attribute in CANONICAL_TO_PROFILE_ATTRIBUTE:
                aliases.add(CANONICAL_TO_PROFILE_ATTRIBUTE[attribute])
            for alias in aliases:
                known.pop(alias, None)
                unknown.pop(alias, None)
            if value is None or value == "unknown":
                unknown.update(dict.fromkeys(aliases, "explicit_unknown"))
            else:
                known.update(dict.fromkeys(aliases, value))

    for attribute in submission.ambiguous_attributes:
        aliases = {attribute}
        if attribute in PROFILE_TO_CANONICAL_ATTRIBUTE:
            aliases.add(PROFILE_TO_CANONICAL_ATTRIBUTE[attribute])
        if attribute in CANONICAL_TO_PROFILE_ATTRIBUTE:
            aliases.add(CANONICAL_TO_PROFILE_ATTRIBUTE[attribute])
        for alias in aliases:
            known.pop(alias, None)
        unknown.update(dict.fromkeys(aliases, "ambiguous"))
    return NormalizedProfile(
        profile_id=profile.profile_id,
        navigator=profile.navigator,
        schema_version=profile.schema_version,
        known_facts=known,
        unknown_facts=unknown,
    )


def search_cluster_program_inventory(
    need_query: str,
    state_code: str | None,
    *,
    cluster_id: str,
    pattern_version: str,
    catalogs: Sequence[DatasetCatalog],
    bindings: Sequence[Mapping[str, object]],
    unavailable_source_ids: Sequence[str] = (),
    catalog_read_failures: Sequence[CatalogReadFailure] = (),
    catalog_truncated: bool = False,
) -> ClusterInventoryResult:
    """Search public program metadata without a profile or eligibility decision.

    Matching is lexical against source-backed titles, descriptions, and applicant
    categories. Coverage
    remains partial because an empty result cannot establish that no program exists.
    """
    query_terms = _search_terms(need_query)
    rank_terms = _search_rank_terms(need_query, is_query=True)
    browse_all = isinstance(need_query, str) and not need_query.strip()
    if (not query_terms and not browse_all) or (
        state_code is not None and not re.fullmatch(r"[A-Z]{2}", state_code)
    ):
        return ClusterInventoryResult(
            status="invalid_request",
            cluster_id=cluster_id,
            pattern_version=pattern_version,
            candidates=[],
            source_health=[],
            unavailable_sources=[],
            catalog_read_failures=[],
            coverage="unavailable",
            catalog_truncated=catalog_truncated,
            explanation=(
                "Provide searchable words or leave the query empty to browse; "
                "an optional state code narrows state programs while federal "
                "programs remain available."
            ),
        )

    bindings_by_dataset: dict[str, list[dict[str, object]]] = {}
    supported_states: set[str] = set()
    for binding in bindings:
        dataset_id = binding.get("dataset_id")
        context = binding.get("binding_context")
        tenant_id = binding.get("tenant_id")
        if (
            isinstance(dataset_id, str)
            and isinstance(context, Mapping)
            and context.get("catalog_namespace") == "programs"
            and isinstance(tenant_id, str)
        ):
            bindings_by_dataset.setdefault(dataset_id, []).append(
                {
                    "tenant_id": tenant_id,
                    "pattern_version": binding.get("pattern_version"),
                    "criteria_mapping": context.get("semantic_mapping", {}).get(
                        "eligibility.program.criteria"
                    )
                    if isinstance(context.get("semantic_mapping"), Mapping)
                    else None,
                }
            )

    candidates: list[ClusterInventoryCandidate] = []
    mapped_program_ids = {
        program.program_id
        for catalog in catalogs
        for program in catalog.programs
        if program.data_origin == "public_source"
    }
    criteria_by_program_id: dict[str, list[ProgramCriteriaReference]] = {}
    criteria_by_assistance_listing: dict[str, list[ProgramCriteriaReference]] = {}
    for catalog in catalogs:
        for reference in catalog.criteria_references:
            criteria_by_program_id.setdefault(reference.program_id, []).append(
                reference
            )
            for listing_id in reference.assistance_listing_ids:
                criteria_by_assistance_listing.setdefault(listing_id, []).append(
                    reference
                )

    def linked_program_ids(listing: PublicListingRecord) -> set[str]:
        """Resolve explicit and source-crosswalked identities for one listing."""
        listing_keys = {listing.listing_id}
        if listing.program_number:
            listing_keys.add(listing.program_number)
        linked_ids = set(listing.related_program_ids)
        linked_ids.update(mapped_program_ids & listing_keys)
        linked_ids.update(
            reference.program_id
            for listing_key in listing_keys
            for reference in criteria_by_assistance_listing.get(listing_key, [])
        )
        return linked_ids

    linked_listings_by_program: dict[str, list[PublicListingRecord]] = defaultdict(list)
    for catalog in catalogs:
        for listing in catalog.listings:
            for program_id in linked_program_ids(listing) & mapped_program_ids:
                linked_listings_by_program[program_id].append(listing)

    def criteria_links(
        program_ids: Sequence[str],
        assistance_listing_id: str | None = None,
    ) -> tuple[
        list[ProgramCriteriaReference],
        Literal["not_linked", "linked_review_only", "unavailable"],
    ]:
        references = [
            reference
            for program_id in program_ids
            for reference in criteria_by_program_id.get(program_id, [])
        ]
        if assistance_listing_id is not None:
            references.extend(
                criteria_by_assistance_listing.get(assistance_listing_id, [])
            )
        references = list(
            {
                (reference.dataset_id, reference.source_record_id): reference
                for reference in references
            }.values()
        )
        if references:
            return references, "linked_review_only"
        # A missing relationship is a catalog-linkage gap, not evidence that the
        # criteria source failed or is unavailable.
        return [], "not_linked"

    for catalog in catalogs:
        for program in catalog.programs:
            if (
                program.jurisdiction.level in {"state", "territory", "local"}
                and program.jurisdiction.state_code
            ):
                supported_states.add(program.jurisdiction.state_code)
        for listing in catalog.listings:
            if (
                listing.listing_id in mapped_program_ids
                or listing.program_number in mapped_program_ids
                or mapped_program_ids.intersection(linked_program_ids(listing))
            ):
                # The mapped program is the canonical search result. Its
                # criteria references and source evidence already carry the
                # linked listing's identity; emitting a second candidate makes
                # agents choose between two records for the same program.
                continue
            if (
                listing.jurisdiction.level in {"state", "territory"}
                and listing.jurisdiction.state_code
            ):
                supported_states.add(listing.jurisdiction.state_code)
        matching_bindings = bindings_by_dataset.get(catalog.dataset_id, [])
        for program in catalog.programs:
            if program.data_origin != "public_source":
                continue
            jurisdiction_match: Literal["confirmed", "unknown"] = "confirmed"
            if program.jurisdiction.level in {"state", "territory", "local"}:
                if state_code is None:
                    jurisdiction_match = "unknown"
                elif program.jurisdiction.state_code != state_code:
                    continue
                elif program.jurisdiction.level == "local":
                    # Search accepts only a state filter, so it cannot confirm
                    # a county-scoped program match.
                    jurisdiction_match = "unknown"
            linked_listings = linked_listings_by_program.get(program.program_id, [])
            linked_listing_text = " ".join(
                _public_listing_search_text(listing) for listing in linked_listings
            )
            search_text = " ".join(
                value
                for value in (program.title, program.description, linked_listing_text)
                if value
            )
            searchable = _search_rank_terms(search_text)
            matched_terms = sorted(query_terms & _search_terms(search_text))
            if not browse_all and not (rank_terms & searchable):
                continue
            tenant_binding_results = [
                ClusterTenantBinding(
                    **item,
                    criteria_comparison_available=bool(program.criteria)
                    and isinstance(item.get("criteria_mapping"), str)
                    and bool(item["criteria_mapping"]),
                )
                for item in matching_bindings
            ]
            primary_listing = next(
                (
                    listing
                    for listing in sorted(
                        linked_listings,
                        key=lambda item: (
                            item.record_role != "program_listing",
                            item.listing_id,
                        ),
                    )
                ),
                None,
            )
            program_criteria_references, program_criteria_status = criteria_links(
                [program.program_id]
            )
            candidates.append(
                ClusterInventoryCandidate(
                    record_type="eligibility_program",
                    dataset_id=catalog.dataset_id,
                    source_record_id=program.program_id,
                    program_id=program.program_id,
                    title=(
                        primary_listing.title
                        if primary_listing is not None
                        else program.title
                    ),
                    description=" ".join(
                        value
                        for value in (
                            primary_listing.summary if primary_listing else "",
                            program.description,
                        )
                        if value
                    ),
                    jurisdiction=program.jurisdiction,
                    jurisdiction_match=jurisdiction_match,
                    sources=sorted(
                        {
                            source.source_id: source
                            for source in [
                                *program.sources,
                                *(listing.source for listing in linked_listings),
                            ]
                        }.values(),
                        key=lambda source: source.source_id,
                    ),
                    source_health=sorted(
                        {
                            health.source_id: health
                            for health in [
                                catalog.source_health,
                                *(
                                    next(
                                        (
                                            item.source_health
                                            for item in catalogs
                                            if (
                                                item.source_id
                                                == listing.source.source_id
                                            )
                                        ),
                                        SourceHealth(
                                            source_id=listing.source.source_id,
                                            status="unknown",
                                        ),
                                    )
                                    for listing in linked_listings
                                ),
                            ]
                        }.values(),
                        key=lambda health: health.source_id,
                    ),
                    linked_listing_ids=sorted(
                        {listing.listing_id for listing in linked_listings}
                    ),
                    matching_terms=matched_terms,
                    applicant_types=sorted(
                        {
                            applicant_type
                            for listing in linked_listings
                            for applicant_type in listing.applicant_types
                        }
                    ),
                    criteria_available=bool(program.criteria),
                    criteria_status="available" if program.criteria else "not_assessed",
                    criteria_references=program_criteria_references,
                    criteria_reference_status=program_criteria_status,
                    tenant_bindings=tenant_binding_results,
                )
            )
        for listing in catalog.listings:
            if (
                listing.listing_id in mapped_program_ids
                or listing.program_number in mapped_program_ids
                or mapped_program_ids.intersection(linked_program_ids(listing))
            ):
                continue
            jurisdiction_match: Literal["confirmed", "unknown"] = "confirmed"
            if listing.jurisdiction.level in {"state", "territory", "local"}:
                if state_code is None:
                    jurisdiction_match = "unknown"
                elif listing.jurisdiction.state_code != state_code:
                    continue
                elif listing.jurisdiction.level == "local":
                    jurisdiction_match = "unknown"
            search_text = _public_listing_search_text(listing)
            searchable = _search_rank_terms(search_text)
            matched_terms = sorted(query_terms & _search_terms(search_text))
            if not browse_all and not (rank_terms & searchable):
                continue
            tenant_binding_results = [
                ClusterTenantBinding(
                    **item,
                    criteria_comparison_available=False,
                )
                for item in matching_bindings
            ]
            listing_criteria_references, listing_criteria_status = criteria_links(
                listing.related_program_ids,
                listing.program_number,
            )
            candidates.append(
                ClusterInventoryCandidate(
                    record_type="public_listing",
                    dataset_id=catalog.dataset_id,
                    source_record_id=listing.listing_id,
                    listing_id=listing.listing_id,
                    record_role=listing.record_role,
                    listing_kind=listing.record_kind,
                    program_number=listing.program_number,
                    related_program_ids=listing.related_program_ids,
                    title=listing.title,
                    description=listing.summary,
                    jurisdiction=listing.jurisdiction,
                    jurisdiction_match=jurisdiction_match,
                    sources=[listing.source],
                    source_health=[catalog.source_health],
                    matching_terms=matched_terms,
                    criteria_available=False,
                    # Listings are discovery records. Even when they point to
                    # review-only criteria, no comparison has been performed.
                    # Source health is reported separately and must not be
                    # inferred from criteria mapping state.
                    criteria_status="not_assessed",
                    criteria_references=listing_criteria_references,
                    criteria_reference_status=listing_criteria_status,
                    listing_status=listing.status,
                    applicant_types=listing.applicant_types,
                    assistance_type=listing.assistance_type,
                    applicant_description=listing.applicant_description,
                    beneficiary_description=listing.beneficiary_description,
                    open_date=(
                        listing.open_date.isoformat() if listing.open_date else None
                    ),
                    close_date=(
                        listing.close_date.isoformat() if listing.close_date else None
                    ),
                    opportunity_url=(
                        str(listing.opportunity_url)
                        if listing.opportunity_url
                        else None
                    ),
                    program_url=(
                        str(listing.program_url) if listing.program_url else None
                    ),
                    website_url=(
                        str(listing.website_url) if listing.website_url else None
                    ),
                    published_date=(
                        listing.published_date.isoformat()
                        if listing.published_date
                        else None
                    ),
                    source_notice=listing.source_notice,
                    tenant_bindings=tenant_binding_results,
                )
            )
    rank_terms = _search_rank_terms(need_query, is_query=True)

    def rank_candidate(
        item: ClusterInventoryCandidate,
    ) -> tuple[int, int, int, int, int, int, int, int, int, int, str, str]:
        linked_listings = (
            linked_listings_by_program.get(item.program_id, [])
            if item.program_id is not None
            else []
        )
        title_text = " ".join(
            [item.title, *(listing.title for listing in linked_listings)]
        )
        applicant_text = " ".join(
            [
                *item.applicant_types,
                *(
                    applicant_type
                    for listing in linked_listings
                    for applicant_type in listing.applicant_types
                ),
            ]
        )
        detail_text = " ".join(
            value or ""
            for value in (
                item.description,
                item.assistance_type,
                item.applicant_description,
                item.beneficiary_description,
                *(listing.summary for listing in linked_listings),
                *(listing.assistance_type for listing in linked_listings),
                *(listing.applicant_description for listing in linked_listings),
                *(listing.beneficiary_description for listing in linked_listings),
            )
        )
        title_terms = _search_rank_terms(title_text)
        applicant_terms = _search_rank_terms(applicant_text)
        detail_terms = _search_rank_terms(detail_text)
        state_match = (
            state_code is not None
            and item.jurisdiction.level in {"state", "territory", "local"}
            and item.jurisdiction.state_code == state_code
        )
        title_overlap = len(rank_terms & title_terms)
        applicant_overlap = len(rank_terms & applicant_terms)
        detail_overlap = len(rank_terms & detail_terms)
        query_term_coverage = len(
            rank_terms & (title_terms | applicant_terms | detail_terms)
        )
        food_program_title_match = "food_assistance" in (rank_terms & title_terms)
        food_intent_search = "food_assistance" in rank_terms
        historical_period_match = _has_expired_source_period(
            item.description, rank_terms
        )
        weighted_relevance = 3 * title_overlap + (
            0 if food_intent_search else 4 * applicant_overlap
        )
        return (
            # A named food-support program is stronger evidence for a grocery
            # need than incidental family or generic assistance wording.
            -int(food_program_title_match),
            # A source-explicit past period is a weaker default match unless the
            # query itself names a year from that period. Keep the listing and
            # its published status unchanged.
            int(historical_period_match),
            # Preserve an exact jurisdiction match for public state programs
            # when a national record matches more generic food-related terms.
            -int(food_intent_search and state_match),
            # Prefer records whose source-backed metadata covers more of the
            # request, so generic audience matches do not crowd out specific needs.
            -query_term_coverage,
            -weighted_relevance,
            -applicant_overlap,
            -detail_overlap,
            -title_overlap,
            -len(item.matching_terms),
            -int(not food_intent_search and state_match),
            item.jurisdiction.level,
            item.program_id or item.listing_id or "",
        )

    candidates.sort(key=rank_candidate)
    candidates = _merge_duplicate_program_candidates(candidates)
    all_match_count = len(candidates)
    weak_match_count = 0
    if browse_all:
        relevance_status: Literal[
            "relevant_matches",
            "weak_matches",
            "no_relevant_match",
            "browse",
            "inventory_unavailable",
        ] = "browse"
    elif supports_eligibility_feature(pattern_version, INVENTORY_RECOVERY_VERSION):
        relevant_candidates = [
            item
            for item in candidates
            if _has_specific_inventory_relevance(
                item,
                rank_terms=rank_terms,
                linked_listings=linked_listings_by_program.get(
                    item.program_id or "", []
                ),
            )
        ]
        weak_match_count = all_match_count - len(relevant_candidates)
        candidates = relevant_candidates
        relevance_status = (
            "relevant_matches"
            if relevant_candidates
            else "weak_matches"
            if weak_match_count
            else "no_relevant_match"
        )
    else:
        relevance_status = "relevant_matches" if candidates else "no_relevant_match"
    source_health_by_id = {
        catalog.source_health.source_id: catalog.source_health for catalog in catalogs
    }
    source_health = [source_health_by_id[key] for key in sorted(source_health_by_id)]
    health_ids = {item.source_id for item in source_health}
    source_health.extend(
        SourceHealth(source_id=source_id, status="unavailable")
        for source_id in sorted(set(unavailable_source_ids) - health_ids)
    )
    unavailable = sorted(
        set(unavailable_source_ids)
        | {item.source_id for item in source_health if item.status == "unavailable"}
    )
    next_action: InventorySearchNextAction | None = None
    if not catalogs and (unavailable or catalog_read_failures):
        status = "unavailable"
        relevance_status = "inventory_unavailable"
        coverage: Literal["partial", "unavailable"] = "unavailable"
        explanation = (
            "No shared catalog records could be read; this is a catalog read "
            "failure, not evidence that source websites are unavailable. No "
            "eligibility conclusion was made."
            if catalog_read_failures
            else "The shared program inventory is unavailable; no eligibility "
            "conclusion was made."
        )
    elif candidates:
        status = "ok"
        coverage = "partial"
        explanation = (
            "Candidates come from the available shared inventory; coverage is "
            "partial and criteria evaluation was not performed."
        )
        if state_code is not None and state_code not in supported_states:
            explanation += (
                f" No state-specific catalog is currently normalized for "
                f"{state_code}; the federal candidates shown are partial "
                "discovery only."
            )
    elif not catalogs:
        status: Literal[
            "ok",
            "no_match",
            "no_coverage",
            "unavailable",
            "unsupported_jurisdiction",
            "invalid_request",
        ] = "no_coverage"
        coverage = "partial"
        explanation = (
            "No shared public inventory is currently available; no eligibility "
            "conclusion was made."
        )
    elif state_code is not None and state_code not in supported_states:
        status = "no_coverage"
        coverage = "partial"
        explanation = (
            f"No matching federal candidate was found, and no state-specific "
            f"catalog is currently normalized for {state_code}; this does not "
            "mean no program exists or that the user is ineligible."
        )
    else:
        status = "no_match"
        coverage = "partial"
        explanation = (
            "No matching listing was found in the currently available inventory; "
            "this does not mean no program exists or that the user is ineligible."
        )
    if not browse_all and status != "invalid_request":
        if status == "unavailable":
            action_reason: Literal[
                "no_relevant_inventory_match",
                "catalog_coverage_incomplete",
                "inventory_unavailable",
            ] = "inventory_unavailable"
        elif (
            status == "no_coverage"
            or catalog_truncated
            or (state_code is not None and state_code not in supported_states)
        ):
            action_reason = "catalog_coverage_incomplete"
        else:
            action_reason = "no_relevant_inventory_match"
        if (
            status in {"unavailable", "no_coverage", "no_match"}
            or catalog_truncated
            or (state_code is not None and state_code not in supported_states)
        ):
            next_action = InventorySearchNextAction(
                operation_id="program.discovery.external.search",
                need_query=need_query.strip(),
                jurisdiction_code=f"US-{state_code}" if state_code else "US",
                reason=action_reason,
            )
    if catalog_truncated:
        explanation += (
            " The active binding list exceeded the read cap, so this result "
            "covers only the returned bindings."
        )
    return ClusterInventoryResult(
        status=status,
        cluster_id=cluster_id,
        pattern_version=pattern_version,
        candidates=candidates,
        source_health=source_health,
        unavailable_sources=unavailable,
        catalog_read_failures=list(catalog_read_failures),
        coverage=coverage,
        catalog_truncated=catalog_truncated,
        relevance_status=relevance_status,
        weak_match_count=weak_match_count,
        next_action=next_action,
        explanation=explanation,
    )


def _has_specific_inventory_relevance(
    candidate: ClusterInventoryCandidate,
    *,
    rank_terms: set[str],
    linked_listings: Sequence[PublicListingRecord],
) -> bool:
    """Require source-backed, specific overlap before presenting a result as relevant.

    A named program or audience match is sufficient. Otherwise, one incidental
    detail-word overlap (for example, ``on`` or ``energy`` in generic guidance)
    remains a weak hit and triggers the external-discovery handoff.
    """
    specific_terms = rank_terms - _GENERIC_RELEVANCE_TERMS
    title_text = " ".join([candidate.title, *(item.title for item in linked_listings)])
    applicant_text = " ".join(
        [
            *candidate.applicant_types,
            *(value for item in linked_listings for value in item.applicant_types),
        ]
    )
    detail_text = " ".join(
        value or ""
        for value in (
            candidate.description,
            candidate.assistance_type,
            candidate.applicant_description,
            candidate.beneficiary_description,
            *(item.summary for item in linked_listings),
            *(item.assistance_type for item in linked_listings),
            *(item.applicant_description for item in linked_listings),
            *(item.beneficiary_description for item in linked_listings),
        )
    )
    title_overlap = specific_terms & _search_rank_terms(title_text)
    applicant_overlap = specific_terms & _search_rank_terms(applicant_text)
    applicant_overlap -= _GENERIC_AUDIENCE_RELEVANCE_TERMS
    detail_overlap = specific_terms & _search_rank_terms(detail_text)
    return bool(title_overlap or applicant_overlap or len(detail_overlap) >= 2)


def _public_listing_search_text(listing: PublicListingRecord) -> str:
    """Return source-backed listing fields used for shared search matching."""
    return " ".join(
        value
        for value in (
            listing.title,
            listing.summary,
            listing.agency,
            listing.assistance_type,
            listing.applicant_description,
            listing.beneficiary_description,
            *listing.applicant_types,
        )
        if value
    )


def _search_terms(value: str) -> set[str]:
    """Return bounded lexical terms while dropping common request filler words.

    Search requests are schema-bounded to 500 characters, but normalized source
    descriptions can be longer. Truncate only exceptionally large text so one
    long record cannot be silently removed from inventory search.
    """
    if not isinstance(value, str):
        return set()
    return {
        term
        for term in re.findall(r"[a-z0-9]+", value[:10_000].casefold())
        if len(term) > 1 and term not in _SEARCH_STOP_WORDS
    }


def _search_rank_terms(value: str, *, is_query: bool = False) -> set[str]:
    """Normalize a small set of common-language equivalents for discovery and ranking.

    Grocery synonyms collapse to one food-assistance intent. Generic food or
    nutrition wording joins that intent only when the source or query also names
    a program/support context; this prevents unrelated food research from
    matching on the word "food" alone.
    """
    terms = _search_terms(value)
    normalized = {_SEARCH_TERM_CANONICAL.get(term, term) for term in terms}
    if is_query:
        query_text = value[:10_000].casefold()
        normalized.update(
            intent
            for phrase, intent in _QUERY_NEED_PHRASE_TERMS.items()
            if re.search(rf"\b{re.escape(phrase)}\b", query_text)
        )
    has_food_support_context = bool(
        terms & {"food", "nutrition"} and terms & _FOOD_SUPPORT_CONTEXT_TERMS
    )
    if has_food_support_context:
        normalized.add("food_assistance")
    if "food_assistance" in normalized:
        normalized.discard("assistance")
        if is_query:
            # Generic food words and family context should not turn unrelated
            # records into matches for a grocery-assistance request.
            normalized.discard("food")
            normalized.discard("nutrition")
            normalized.discard("family")
    return normalized


def _has_expired_source_period(summary: str, query_terms: set[str]) -> bool:
    """Flag an explicit past period for ranking without changing listing status.

    The source must provide a period-ending phrase and year. A query naming any
    year in that summary keeps the record eligible for its normal relevance rank.
    """
    end_years = {
        int(match.group("year"))
        for match in _EXPLICIT_PERIOD_END_YEAR.finditer(summary)
    }
    if not end_years or max(end_years) >= date.today().year:
        return False
    source_years = {match.group("year") for match in _SOURCE_YEAR.finditer(summary)}
    return not bool(source_years & query_terms)


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _source_identity(source: SourceReference) -> dict[str, Any]:
    """Source fields that must agree; retrieval provenance may differ."""
    return source.model_dump(
        mode="json",
        exclude={"retrieved_at", "effective_date", "publisher_version"},
    )


def merge_same_program_records(programs: list[ProgramRecord]) -> list[ProgramRecord]:
    """Merge records that describe one program across catalogs.

    A criteria record and an application-details record for the same program
    may live in different datasets (details need the v4 record; some criteria
    were registered earlier). When every record agrees on identity, jurisdiction
    and on every shared criterion or source, the records are one program: the
    criteria, sources and cited details are unioned. Any disagreement leaves
    the list unchanged so the caller still reports the ID as ambiguous.
    """
    if len(programs) < 2:
        return programs
    first = programs[0]
    if any(
        program.program_id != first.program_id
        or program.navigator != first.navigator
        or program.jurisdiction != first.jurisdiction
        for program in programs[1:]
    ):
        return programs

    criteria: dict[str, EligibilityCriterion] = {}
    sources: dict[str, SourceReference] = {}
    details: dict[tuple[str, str, str], SourceBackedProgramDetail] = {}
    limitations: list[str] = []
    for program in programs:
        for criterion in program.criteria:
            known = criteria.setdefault(criterion.criterion_id, criterion)
            if known != criterion:
                return programs
        for source in program.sources:
            known_source = sources.setdefault(source.source_id, source)
            if _source_identity(known_source) != _source_identity(source):
                return programs
            if (source.retrieved_at or _EPOCH) > (known_source.retrieved_at or _EPOCH):
                sources[source.source_id] = source
        for detail in program.details:
            details.setdefault((detail.kind, detail.text, detail.source_id), detail)
        for limitation in program.source_limitations:
            if limitation not in limitations:
                limitations.append(limitation)

    base = max(programs, key=lambda program: len(program.details))
    merged = base.model_dump(mode="python")
    merged["criteria"] = [c.model_dump(mode="python") for c in criteria.values()]
    merged["sources"] = [s.model_dump(mode="python") for s in sources.values()]
    merged["details"] = [d.model_dump(mode="python") for d in details.values()]
    merged["source_limitations"] = limitations
    try:
        return [ProgramRecord.model_validate(merged)]
    except ValueError:
        return programs


def approved_source_specs() -> dict[str, SourceSpec]:
    """Return optional strict validators for sources with explicit contracts."""
    return {spec.dataset_id: spec for spec in load_source_specs()}


def dataset_catalog_from_payload(
    payload: Mapping[str, object],
    *,
    spec: SourceSpec | None = None,
    expected_dataset_id: str | None = None,
    expected_source_id: str | None = None,
) -> DatasetCatalog:
    """Validate and project one live Data API dataset response into pattern records."""
    dataset = payload.get("dataset")
    records = payload.get("records")
    freshness = payload.get("freshness")
    if not isinstance(dataset, Mapping) or not isinstance(records, list):
        raise ValueError("Public dataset response has an invalid structure.")
    dataset_id = dataset.get("dataset_id")
    source_id = dataset.get("source_id")
    schema_version = dataset.get("schema_version")
    expected_dataset = spec.dataset_id if spec else expected_dataset_id
    expected_source = spec.source_id if spec else expected_source_id
    if (
        not isinstance(dataset_id, str)
        or not isinstance(source_id, str)
        or dataset_id != expected_dataset
        or source_id != expected_source
    ):
        raise ValueError("Public dataset identity does not match its active binding.")
    metadata = dataset.get("metadata")
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("catalog_namespace") != "programs"
    ):
        raise ValueError("Public dataset is not registered in the Eligibility catalog.")

    source_health = source_health_from_payload(source_id, freshness)
    criteria_references: list[ProgramCriteriaReference] = []
    if schema_version in {
        "eligibility-program-v1",
        "eligibility-program-v2",
        "eligibility-program-v3",
        "eligibility-program-v4",
    }:
        programs = []
        listings: list[PublicListingRecord] = []
        for item in records:
            if spec is not None:
                programs.append(source_program_from_record(item, spec=spec))
                continue
            role = _normalized_record_role(item)
            if role == "criteria":
                references = criteria_references_from_record(
                    item, dataset_id=dataset_id, expected_source_id=source_id
                )
                criteria_references.extend(references)
            elif (
                isinstance(item, Mapping)
                and item.get("record_type") == "eligibility_program"
            ):
                programs.append(
                    source_program_from_registered_record(
                        item, expected_source_id=source_id
                    )
                )
            elif role not in {"context", "history"}:
                raise ValueError(
                    "Eligibility dataset contains an unsupported record role."
                )
    elif spec is None and schema_version == "public-data-v1":
        if not all(
            _is_contextual_data_resource_record(item, expected_source_id=source_id)
            for item in records
        ):
            raise ValueError(
                "Public data catalog contains a non-context resource record."
            )
        programs = []
        listings = []
    elif (
        schema_version in {"public-listing-v3", "public-listing-v4"}
        or (
            spec is None
            and schema_version in {"public-listing-v1", "public-listing-v2"}
        )
        or (
            spec is None
            and isinstance(schema_version, str)
            and schema_version.startswith("eligibility-program-")
            and schema_version != "eligibility-program-v1"
        )
    ):
        programs = []
        listings = []
        for item in records:
            role = _normalized_record_role(item)
            if role == "criteria":
                references = criteria_references_from_record(
                    item, dataset_id=dataset_id, expected_source_id=source_id
                )
                criteria_references.extend(references)
                continue
            if role in {"context", "history"}:
                continue
            listings.append(
                public_listing_from_record(
                    item,
                    expected_source_id=source_id,
                )
            )
    else:
        raise ValueError("Public dataset schema is not supported by this projection.")
    return DatasetCatalog(
        dataset_id=dataset_id,
        source_id=source_id,
        programs=programs,
        source_health=source_health,
        listings=listings,
        criteria_references=criteria_references,
    )


def _is_contextual_data_resource_record(
    record: object, *, expected_source_id: str
) -> bool:
    """Keep public dataset metadata readable without treating it as a program."""
    if not isinstance(record, Mapping):
        return False
    attributes = record.get("canonical_attributes")
    semantic_context = record.get("semantic_context")
    source_metadata = record.get("source_metadata")
    source_url = record.get("source_url")
    if not all(
        isinstance(value, Mapping)
        for value in (attributes, semantic_context, source_metadata)
    ):
        return False
    assert isinstance(attributes, Mapping)
    assert isinstance(semantic_context, Mapping)
    assert isinstance(source_metadata, Mapping)
    return (
        record.get("record_type") == "catalog_metadata"
        and record.get("schema_version") == "public-data-v1"
        and semantic_context.get("catalog_namespace") == "programs"
        and semantic_context.get("record_role") in {"context", "history"}
        and source_metadata.get("source_id") == expected_source_id
        and isinstance(source_metadata.get("source_version"), str)
        and isinstance(source_metadata.get("retrieved_at"), str)
        and isinstance(source_url, str)
        and source_url.startswith("https://")
        and isinstance(attributes.get("core.dataset.title"), str)
        and isinstance(attributes.get("core.dataset.content_type"), str)
        and all(
            isinstance(key, str) and key.startswith("core.dataset.")
            for key in attributes
        )
    )


def criteria_references_from_record(
    record: object,
    *,
    dataset_id: str,
    expected_source_id: str,
) -> list[ProgramCriteriaReference]:
    """Project source-backed criteria pointers, honoring explicit program links."""
    if not isinstance(record, Mapping):
        return []
    attributes = record.get("canonical_attributes")
    semantic_context = record.get("semantic_context")
    source_metadata = record.get("source_metadata")
    if not all(
        isinstance(value, Mapping)
        for value in (attributes, semantic_context, source_metadata)
    ):
        return []
    assert isinstance(attributes, Mapping)
    assert isinstance(semantic_context, Mapping)
    assert isinstance(source_metadata, Mapping)
    program_id = attributes.get("eligibility.program.program_id")
    if not isinstance(program_id, str) or not program_id.strip():
        return []
    linked_program_ids = _criteria_related_program_ids(semantic_context, program_id)
    if linked_program_ids is None:
        return []
    source_id = source_metadata.get("source_id")
    source_url = record.get("source_url")
    source_version = source_metadata.get("source_version")
    retrieved_at = source_metadata.get("retrieved_at")
    title = attributes.get("eligibility.program.title")
    record_id = record.get("source_record_id")
    coverage = semantic_context.get("criteria_coverage")
    details_payload = attributes.get("eligibility.program.details", [])
    assistance_listing_ids = semantic_context.get("assistance_listing_ids", [])
    criteria_source_links = semantic_context.get("criteria_source_links", [])
    identity_link = semantic_context.get("assistance_listing_crosswalk", {})
    if not isinstance(identity_link, Mapping):
        return []
    identity_link_basis = identity_link.get("basis")
    identity_link_reviewed_at = identity_link.get("reviewed_at")
    identity_link_evidence_urls = identity_link.get("evidence_urls", [])
    if (
        source_id != expected_source_id
        or not isinstance(source_url, str)
        or not source_url.startswith("https://")
        or not isinstance(source_version, str)
        or not isinstance(retrieved_at, str)
        or not isinstance(title, str)
        or not isinstance(record_id, str)
        or not isinstance(coverage, str)
        or not isinstance(details_payload, list)
        or not isinstance(assistance_listing_ids, list)
        or not isinstance(criteria_source_links, list)
        or any(
            not isinstance(item, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}", item)
            for item in assistance_listing_ids
        )
        or identity_link_basis not in {None, "curated_source_crosswalk"}
        or (
            identity_link_reviewed_at is not None
            and not isinstance(identity_link_reviewed_at, str)
        )
        or not isinstance(identity_link_evidence_urls, list)
        or any(
            not isinstance(item, str) or not item.startswith("https://")
            for item in identity_link_evidence_urls
        )
        or (assistance_listing_ids and identity_link_basis is None)
    ):
        return []
    try:
        details = [
            SourceBackedProgramDetail.model_validate(item) for item in details_payload
        ]
        if any(detail.source_id != source_id for detail in details):
            return []
        source_links = [
            CriteriaSourceLink.model_validate(item) for item in criteria_source_links
        ]
        return [
            ProgramCriteriaReference(
                program_id=linked_program_id,
                dataset_id=dataset_id,
                source_record_id=record_id,
                title=title,
                source_id=source_id,
                source_url=source_url,
                source_version=source_version,
                retrieved_at=retrieved_at,
                coverage=coverage,
                details=details,
                assistance_listing_ids=list(dict.fromkeys(assistance_listing_ids)),
                criteria_source_links=source_links,
                identity_link_basis=identity_link_basis,
                identity_link_reviewed_at=identity_link_reviewed_at,
                identity_link_evidence_urls=identity_link_evidence_urls,
            )
            for linked_program_id in linked_program_ids
        ]
    except ValidationError:
        return []


def _criteria_related_program_ids(
    semantic_context: Mapping[str, object], canonical_program_id: str
) -> list[str] | None:
    """Return curated criteria links, or the legacy canonical ID when absent."""
    if "criteria_program_ids" not in semantic_context:
        return [canonical_program_id]
    value = semantic_context.get("criteria_program_ids")
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 20
        or any(
            not isinstance(item, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", item)
            for item in value
        )
    ):
        return None
    return list(dict.fromkeys(value))


def public_listing_from_record(
    record: object, *, expected_source_id: str
) -> PublicListingRecord:
    """Validate a source-backed listing without inventing a Navigator or criteria."""
    if not isinstance(record, Mapping):
        raise ValueError("Public listing record has an invalid structure.")
    attributes = record.get("canonical_attributes")
    source_metadata = record.get("source_metadata")
    semantic_context = record.get("semantic_context")
    role = _normalized_record_role(record)
    if role is not None and role not in {
        "program_listing",
        "referral",
        "criteria",
        "context",
        "history",
    }:
        raise ValueError("Public record uses an unsupported catalog role.")
    if (
        isinstance(attributes, Mapping)
        and isinstance(source_metadata, Mapping)
        and isinstance(semantic_context, Mapping)
        and record.get("record_type") != "public_listing"
        and role in {"program_listing", "referral"}
    ):
        return _normalized_public_record_from_record(
            record,
            attributes=attributes,
            source_metadata=source_metadata,
            semantic_context=semantic_context,
            expected_source_id=expected_source_id,
            role=role,
        )
    if (
        not isinstance(attributes, Mapping)
        or not isinstance(source_metadata, Mapping)
        or not isinstance(semantic_context, Mapping)
        or record.get("record_type") != "public_listing"
        or record.get("schema_version")
        not in {
            "public-listing-v1",
            "public-listing-v2",
            "public-listing-v3",
            "public-listing-v4",
        }
        or semantic_context.get("catalog_namespace") != "programs"
        or source_metadata.get("source_id") != expected_source_id
        or source_metadata.get("source_type") != "public_source"
        or source_metadata.get("approval_status") != "approved"
        or source_metadata.get("criteria_status") not in {"not_assessed", "unavailable"}
    ):
        raise ValueError("Public listing record failed source and schema validation.")
    if any(str(key).startswith("eligibility.program.") for key in attributes):
        raise ValueError("Criteria-free listings cannot contain Eligibility rules.")
    publisher = source_metadata.get("publisher")
    source_title = source_metadata.get("source_title")
    if not isinstance(publisher, str) or not isinstance(source_title, str):
        raise ValueError("Public listing provenance is missing publisher identity.")

    jurisdiction = jurisdiction_from_attributes(attributes)
    schema_version = record["schema_version"]
    if schema_version == "public-listing-v1":
        expected_kind = "funding_opportunity"
    elif schema_version == "public-listing-v2":
        expected_kind = "assistance_program"
    else:
        expected_kind = semantic_context.get("listing_kind")
        if expected_kind not in {"funding_opportunity", "assistance_program"}:
            raise ValueError("Generic public listing kind is missing or invalid.")
        if attributes.get("listing.record_kind") != expected_kind:
            raise ValueError("Generic listing kind disagrees with its attributes.")
    if semantic_context.get("listing_kind", expected_kind) != expected_kind:
        raise ValueError("Public listing kind does not match its schema version.")
    source = SourceReference(
        source_id=expected_source_id,
        source_type="public_source",
        approval_status="approved",
        agency=publisher,
        title=source_title,
        url=record.get("source_url"),
        jurisdiction=jurisdiction,
        retrieved_at=source_metadata.get("retrieved_at"),
        publisher_version=source_metadata.get("source_version"),
    )
    applicant_types = attributes.get("listing.applicant_types", [])
    if not isinstance(applicant_types, list):
        raise ValueError("Public listing applicant types must be a list.")
    record_id = record.get("source_record_id")
    if not isinstance(record_id, str):
        raise ValueError("Public listing record is missing its source identity.")
    try:
        return PublicListingRecord(
            listing_id=record_id,
            record_role=(
                role if role in {"program_listing", "referral"} else "program_listing"
            ),
            schema_version=schema_version,
            title=attributes["listing.title"],
            summary=attributes.get("listing.summary", ""),
            agency=attributes["listing.agency"],
            applicant_types=applicant_types,
            status=attributes.get("listing.status"),
            open_date=attributes.get("listing.open_date"),
            close_date=attributes.get("listing.close_date"),
            opportunity_url=attributes.get("listing.opportunity_url"),
            record_kind=expected_kind,
            program_number=attributes.get("listing.program_number"),
            assistance_type=attributes.get("listing.assistance_type"),
            applicant_description=attributes.get("listing.applicant_description"),
            beneficiary_description=attributes.get("listing.beneficiary_description"),
            program_url=attributes.get("listing.program_url"),
            website_url=attributes.get("listing.website_url"),
            published_date=attributes.get("listing.published_date"),
            jurisdiction=jurisdiction,
            source=source,
            criteria_status=source_metadata["criteria_status"],
            source_notice=source_metadata.get("source_notice"),
            related_program_ids=_related_program_ids(semantic_context),
        )
    except (KeyError, ValidationError, TypeError) as exc:
        raise ValueError(
            "Public listing record is missing valid source fields."
        ) from exc


def _normalized_record_role(record: object) -> str | None:
    """Return the declared catalog role, if the normalized record has one."""
    if not isinstance(record, Mapping):
        return None
    semantic_context = record.get("semantic_context")
    role = (
        semantic_context.get("record_role")
        if isinstance(semantic_context, Mapping)
        else None
    )
    return role if isinstance(role, str) else None


def _related_program_ids(semantic_context: Mapping[str, object]) -> list[str]:
    """Read explicit program identity links from source-record semantic metadata."""
    value = semantic_context.get("criteria_program_ids", [])
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError("Listing related program IDs must be non-empty strings.")
    return list(dict.fromkeys(value))


def _normalized_public_record_from_record(
    record: Mapping[str, object],
    *,
    attributes: Mapping[str, object],
    source_metadata: Mapping[str, object],
    semantic_context: Mapping[str, object],
    expected_source_id: str,
    role: Literal["program_listing", "referral"],
) -> PublicListingRecord:
    """Project a registered normalized listing/referral role into search results."""
    if (
        semantic_context.get("catalog_namespace") != "programs"
        or source_metadata.get("source_id") != expected_source_id
        or record.get("schema_version")
        not in {
            "public-listing-v1",
            "public-listing-v2",
            "public-listing-v3",
            "public-listing-v4",
        }
        or any(str(key).startswith("eligibility.program.") for key in attributes)
    ):
        raise ValueError("Normalized public record failed source and role validation.")
    record_id = record.get("source_record_id")
    title = attributes.get("listing.title")
    agency = attributes.get("listing.agency")
    source_url = record.get("source_url")
    retrieved_at = source_metadata.get("retrieved_at")
    if (
        not isinstance(record_id, str)
        or not isinstance(title, str)
        or not isinstance(agency, str)
        or not isinstance(source_url, str)
        or not source_url.startswith("https://")
        or not isinstance(retrieved_at, str)
    ):
        raise ValueError("Normalized public record is missing source-backed fields.")
    jurisdiction_payload = semantic_context.get("jurisdiction")
    if isinstance(jurisdiction_payload, Mapping):
        level = jurisdiction_payload.get("level")
        attributes_for_jurisdiction = {
            "core.geography.jurisdiction_level": (
                "federal" if level == "national" else level
            ),
            "core.geography.country_code": jurisdiction_payload.get(
                "country_code", "US"
            ),
        }
        for key in ("state_code", "county_name"):
            value = jurisdiction_payload.get(key)
            if value is not None:
                attributes_for_jurisdiction[f"core.geography.{key}"] = value
    else:
        attributes_for_jurisdiction = attributes
    jurisdiction = jurisdiction_from_attributes(attributes_for_jurisdiction)
    criteria_status = (
        "unavailable"
        if semantic_context.get("criteria_coverage") == "unavailable"
        else "not_assessed"
    )
    source = SourceReference(
        source_id=expected_source_id,
        source_type="public_source",
        approval_status="approved",
        agency=agency,
        title="Official public source record",
        url=source_url,
        jurisdiction=jurisdiction,
        retrieved_at=retrieved_at,
        publisher_version=source_metadata.get("source_version"),
    )
    summary = str(attributes.get("listing.summary", ""))
    if len(summary) > 1200:
        summary = summary[:1199].rstrip() + "…"
    try:
        return PublicListingRecord(
            listing_id=record_id,
            schema_version=(
                "public-listing-v4"
                if record.get("schema_version") == "public-listing-v4"
                else "public-listing-v3"
            ),
            record_role=role,
            record_kind=(
                "assistance_program"
                if role == "program_listing"
                else "referral_resource"
            ),
            title=title,
            summary=summary,
            agency=agency,
            applicant_types=attributes.get("listing.applicant_types", []),
            status="unknown",
            program_url=source_url,
            jurisdiction=jurisdiction,
            source=source,
            criteria_status=criteria_status,
            source_notice=(
                "Source discovery only. Published text is not a determination "
                "of eligibility or current availability."
            ),
            related_program_ids=_related_program_ids(semantic_context),
        )
    except (ValidationError, TypeError) as exc:
        raise ValueError(
            "Normalized public record has invalid listing fields."
        ) from exc


def source_health_from_payload(source_id: str, freshness: object) -> SourceHealth:
    """Translate the Data API health vocabulary without inventing age thresholds."""
    data = freshness if isinstance(freshness, Mapping) else {}
    health = data.get("health_status")
    freshness_status = data.get("freshness_status")
    if health == "healthy" and freshness_status == "fresh_as_of_last_check":
        status: Literal["current", "stale", "unavailable", "unknown"] = "current"
    elif freshness_status == "stale" or health == "degraded":
        status = "stale"
    elif freshness_status == "unavailable":
        status = "unavailable"
    else:
        status = "unknown"
    return SourceHealth(
        source_id=source_id,
        status=status,
        checked_at=_aware_datetime(
            data.get("last_attempt_completed_at") or data.get("last_attempt_at")
        ),
        last_successful_retrieval=_aware_datetime(
            data.get("last_successful_retrieval_at")
        ),
    )


def source_program_from_record(record: object, *, spec: SourceSpec) -> ProgramRecord:
    """Rebuild a typed pattern record from one persisted normalized source record."""
    if not isinstance(record, Mapping):
        raise ValueError("Public dataset record has an invalid structure.")
    attributes = record.get("canonical_attributes")
    source_metadata = record.get("source_metadata")
    if not isinstance(attributes, Mapping) or not isinstance(source_metadata, Mapping):
        raise ValueError("Public dataset record is missing normalized provenance.")
    if (
        record.get("record_type") != "eligibility_program"
        or record.get("source_record_id") != spec.program_id
        or source_metadata.get("source_id") != spec.source_id
        or record.get("source_url") != spec.url
        or record.get("schema_version")
        not in {
            "eligibility-program-v1",
            "eligibility-program-v2",
            "eligibility-program-v3",
            "eligibility-program-v4",
        }
        or attributes.get("eligibility.program.data_origin") != "public_source"
        or attributes.get("eligibility.program.program_id") != spec.program_id
        or attributes.get("eligibility.program.navigator") != spec.navigator
        or attributes.get("eligibility.program.title") != spec.program_title
    ):
        raise ValueError("Public dataset record does not match its approved source.")
    semantic_context = record.get("semantic_context")
    if (
        not isinstance(semantic_context, Mapping)
        or semantic_context.get("catalog_namespace") != "programs"
    ):
        raise ValueError("Public dataset record has the wrong catalog namespace.")

    jurisdiction = jurisdiction_from_attributes(attributes)
    criteria_payload = attributes.get("eligibility.program.criteria")
    if not isinstance(criteria_payload, list):
        raise ValueError("Public program record has no typed criteria list.")
    criteria: list[EligibilityCriterion] = []
    for criterion in criteria_payload:
        if not isinstance(criterion, Mapping):
            raise ValueError("Public program criterion has an invalid structure.")
        canonical_attribute = criterion.get("attribute")
        profile_attribute = CANONICAL_TO_PROFILE_ATTRIBUTE.get(
            str(canonical_attribute), str(canonical_attribute)
        )
        criteria.append(
            EligibilityCriterion.model_validate(
                {**criterion, "attribute": profile_attribute}
            )
        )

    retrieved_at = source_metadata.get("retrieved_at")
    source = SourceReference(
        source_id=spec.source_id,
        source_type="public_source",
        approval_status="approved",
        agency=spec.agency,
        title=spec.expected_title,
        url=spec.url,
        jurisdiction=jurisdiction,
        retrieved_at=retrieved_at,
        effective_date=source_metadata.get("effective_date"),
        publisher_version=source_metadata.get("source_version"),
    )
    sources = _additional_source_references(
        attributes,
        primary_source=source,
        schema_version=record.get("schema_version"),
    )
    details_payload = attributes.get("eligibility.program.details", [])
    if not isinstance(details_payload, list):
        raise ValueError("Public program details have an invalid structure.")
    limitations = source_metadata.get("limitations", [])
    if not isinstance(limitations, list) or any(
        not isinstance(item, str) for item in limitations
    ):
        raise ValueError("Public program source limitations have an invalid structure.")
    try:
        return ProgramRecord(
            catalog_version=attributes["eligibility.program.catalog_version"],
            program_id=attributes["eligibility.program.program_id"],
            navigator=attributes["eligibility.program.navigator"],
            title=attributes["eligibility.program.title"],
            description=attributes["eligibility.program.description"],
            jurisdiction=jurisdiction,
            data_origin="public_source",
            sources=sources,
            criteria=criteria,
            details=[
                SourceBackedProgramDetail.model_validate(item)
                for item in details_payload
            ],
            source_limitations=limitations,
        )
    except (KeyError, ValidationError) as exc:
        raise ValueError(
            "Public program record failed Eligibility validation."
        ) from exc


def _additional_source_references(
    attributes: Mapping[str, object],
    *,
    primary_source: SourceReference,
    schema_version: object,
) -> list[SourceReference]:
    """Validate v4 secondary citations alongside the record's primary source."""
    payload = attributes.get("eligibility.program.source_references", [])
    if not isinstance(payload, list):
        raise ValueError("Public program source references have an invalid structure.")
    if schema_version != "eligibility-program-v4" and payload:
        raise ValueError("Additional source references require Eligibility schema v4.")
    try:
        additional_sources = [SourceReference.model_validate(item) for item in payload]
    except ValidationError as exc:
        raise ValueError("Public program source reference is invalid.") from exc
    sources = [primary_source, *additional_sources]
    sources_by_id = {source.source_id: source for source in sources}
    if len(sources_by_id) != len(sources):
        raise ValueError("Public program source IDs must be unique.")
    return list(sources_by_id.values())


def source_program_from_registered_record(
    record: object, *, expected_source_id: str
) -> ProgramRecord:
    """Project a registered program record without a demo-specific SourceSpec."""
    if not isinstance(record, Mapping):
        raise ValueError("Public dataset record has an invalid structure.")
    attributes = record.get("canonical_attributes")
    source_metadata = record.get("source_metadata")
    semantic_context = record.get("semantic_context")
    source_url = record.get("source_url")
    source_record_id = record.get("source_record_id")
    if not all(
        isinstance(item, Mapping)
        for item in (attributes, source_metadata, semantic_context)
    ):
        raise ValueError("Public program record is missing normalized provenance.")
    assert isinstance(attributes, Mapping)
    assert isinstance(source_metadata, Mapping)
    assert isinstance(semantic_context, Mapping)
    if (
        record.get("record_type") != "eligibility_program"
        or record.get("schema_version")
        not in {
            "eligibility-program-v1",
            "eligibility-program-v2",
            "eligibility-program-v3",
            "eligibility-program-v4",
        }
        or semantic_context.get("catalog_namespace") != "programs"
        or source_metadata.get("source_id") != expected_source_id
        or source_metadata.get("source_type") != "public_source"
        or source_metadata.get("approval_status") != "approved"
        or not isinstance(source_url, str)
        or not source_url.startswith("https://")
        or not isinstance(source_record_id, str)
        or attributes.get("eligibility.program.data_origin") != "public_source"
        or attributes.get("eligibility.program.program_id") != source_record_id
    ):
        raise ValueError("Public program record failed source approval validation.")

    criteria_payload = attributes.get("eligibility.program.criteria")
    details_payload = attributes.get("eligibility.program.details", [])
    limitations = source_metadata.get("limitations", [])
    agency = source_metadata.get("publisher") or source_metadata.get("agency")
    source_title = source_metadata.get("source_title") or source_metadata.get("title")
    if (
        not isinstance(criteria_payload, list)
        or not isinstance(details_payload, list)
        or not isinstance(limitations, list)
        or any(not isinstance(item, str) for item in limitations)
        or not isinstance(agency, str)
        or not isinstance(source_title, str)
    ):
        raise ValueError("Public program record lacks typed criteria or provenance.")

    jurisdiction = jurisdiction_from_attributes(attributes)
    retrieved_at = source_metadata.get("retrieved_at")
    primary_source = SourceReference(
        source_id=expected_source_id,
        source_type="public_source",
        approval_status="approved",
        agency=agency,
        title=source_title,
        url=source_url,
        jurisdiction=jurisdiction,
        retrieved_at=retrieved_at,
        effective_date=source_metadata.get("effective_date"),
        publisher_version=source_metadata.get("source_version"),
    )
    sources = _additional_source_references(
        attributes,
        primary_source=primary_source,
        schema_version=record.get("schema_version"),
    )
    criteria: list[EligibilityCriterion] = []
    for item in criteria_payload:
        if not isinstance(item, Mapping):
            raise ValueError("Public program criterion has an invalid structure.")
        canonical_attribute = item.get("attribute")
        profile_attribute = CANONICAL_TO_PROFILE_ATTRIBUTE.get(
            str(canonical_attribute), str(canonical_attribute)
        )
        criteria.append(
            EligibilityCriterion.model_validate(
                {**item, "attribute": profile_attribute}
            )
        )
    try:
        return ProgramRecord(
            catalog_version=attributes["eligibility.program.catalog_version"],
            program_id=attributes["eligibility.program.program_id"],
            navigator=attributes.get("eligibility.program.navigator", "general"),
            title=attributes["eligibility.program.title"],
            description=attributes["eligibility.program.description"],
            jurisdiction=jurisdiction,
            data_origin="public_source",
            sources=sources,
            criteria=criteria,
            details=[
                SourceBackedProgramDetail.model_validate(item)
                for item in details_payload
            ],
            source_limitations=limitations,
        )
    except (KeyError, ValidationError, TypeError) as exc:
        raise ValueError(
            "Public program record failed Eligibility validation."
        ) from exc


def discover_program_candidates(
    submission: ProfileSubmission,
    scope: DiscoveryScope,
    catalogs: Sequence[DatasetCatalog],
    unavailable_source_ids: Sequence[str] = (),
    catalog_read_failures: Sequence[CatalogReadFailure] = (),
    *,
    selected_program_id: str | None = None,
    need_query: str | None = None,
) -> DiscoveryResult:
    """List shared programs and optionally derive readiness for one selection."""
    profile = normalize_profile(submission)
    health_by_source = {
        catalog.source_id: catalog.source_health for catalog in catalogs
    }
    programs = [program for catalog in catalogs for program in catalog.programs]
    listings = [listing for catalog in catalogs for listing in catalog.listings]
    mapped_program_ids = {
        program.program_id
        for program in programs
        if program.data_origin == "public_source"
    }
    linked_listings: dict[str, list[PublicListingRecord]] = defaultdict(list)
    for listing in listings:
        linked_ids = set(listing.related_program_ids)
        linked_ids.update(
            program_id
            for program_id in mapped_program_ids
            if program_id in {listing.listing_id, listing.program_number}
        )
        for program_id in linked_ids & mapped_program_ids:
            linked_listings[program_id].append(listing)
    candidates: list[ProgramCandidate] = []
    unassessed_listings: list[UnassessedListingCandidate] = []
    for program in programs:
        if program.data_origin != "public_source":
            continue
        jurisdiction_match = _profile_location_match(profile, program.jurisdiction)
        if jurisdiction_match == "excluded":
            continue
        candidates.append(
            ProgramCandidate(
                program_id=program.program_id,
                navigator=program.navigator,
                title=program.title,
                catalog_version=program.catalog_version,
                jurisdiction=program.jurisdiction,
                jurisdiction_match=jurisdiction_match,
                sources=sorted(
                    {
                        source.source_id: source
                        for source in [
                            *program.sources,
                            *(
                                listing.source
                                for listing in linked_listings.get(
                                    program.program_id, []
                                )
                            ),
                        ]
                    }.values(),
                    key=lambda source: source.source_id,
                ),
                source_health=sorted(
                    {
                        source_id: health_by_source.get(
                            source_id,
                            SourceHealth(source_id=source_id, status="unknown"),
                        )
                        for source_id in {
                            source.source_id
                            for source in [
                                *program.sources,
                                *(
                                    listing.source
                                    for listing in linked_listings.get(
                                        program.program_id, []
                                    )
                                ),
                            ]
                        }
                    }.values(),
                    key=lambda health: health.source_id,
                ),
                linked_listing_ids=sorted(
                    {
                        listing.listing_id
                        for listing in linked_listings.get(program.program_id, [])
                    }
                ),
                evaluation_available=bool(program.criteria),
                coverage="partial_source_backed_screening",
            )
        )
    for listing in listings:
        if (
            listing.listing_id in mapped_program_ids
            or listing.program_number in mapped_program_ids
            or mapped_program_ids.intersection(listing.related_program_ids)
        ):
            continue
        jurisdiction_match = _profile_location_match(profile, listing.jurisdiction)
        if jurisdiction_match == "excluded":
            continue
        linked_criteria = [
            reference
            for catalog in catalogs
            for reference in catalog.criteria_references
            if reference.program_id in listing.related_program_ids
            or listing.program_number in reference.assistance_listing_ids
        ]
        linked_criteria = list(
            {
                (reference.dataset_id, reference.source_record_id): reference
                for reference in linked_criteria
            }.values()
        )
        unassessed_listings.append(
            UnassessedListingCandidate(
                listing_id=listing.listing_id,
                record_kind=listing.record_kind,
                program_number=listing.program_number,
                title=listing.title,
                summary=listing.summary,
                agency=listing.agency,
                jurisdiction=listing.jurisdiction,
                jurisdiction_match=jurisdiction_match,
                source=listing.source,
                source_health=health_by_source.get(
                    listing.source.source_id,
                    SourceHealth(source_id=listing.source.source_id, status="unknown"),
                ),
                related_program_ids=listing.related_program_ids,
                # Public listings never represent an evaluated criteria result.
                # A linked review-only reference remains not_assessed until it
                # has a reviewed executable mapping; source health is separate.
                criteria_status="not_assessed",
                criteria_reference_status=(
                    "linked_review_only" if linked_criteria else "not_linked"
                ),
                criteria_references=linked_criteria,
            )
        )
    if need_query and selected_program_id is None:
        query_terms = _search_rank_terms(need_query, is_query=True)
        if query_terms:
            program_by_id = {program.program_id: program for program in programs}
            candidates = [
                candidate
                for candidate in candidates
                if _discovery_candidate_matches_need(
                    query_terms,
                    title_text=candidate.title,
                    applicant_text=" ".join(
                        applicant_type
                        for listing in linked_listings.get(candidate.program_id, [])
                        for applicant_type in listing.applicant_types
                    ),
                    detail_text=" ".join(
                        [
                            program_by_id[candidate.program_id].description,
                            *(
                                detail.text
                                for detail in program_by_id[
                                    candidate.program_id
                                ].details
                            ),
                            *(
                                text
                                for listing in linked_listings.get(
                                    candidate.program_id, []
                                )
                                for text in (
                                    listing.summary,
                                    listing.assistance_type or "",
                                    listing.applicant_description or "",
                                    listing.beneficiary_description or "",
                                )
                            ),
                        ]
                    ),
                )
            ]
            listing_by_id = {listing.listing_id: listing for listing in listings}
            unassessed_listings = [
                candidate
                for candidate in unassessed_listings
                if _discovery_candidate_matches_need(
                    query_terms,
                    title_text=candidate.title,
                    applicant_text=" ".join(
                        listing_by_id[candidate.listing_id].applicant_types
                    ),
                    detail_text=" ".join(
                        [
                            candidate.summary,
                            candidate.agency,
                            listing_by_id[candidate.listing_id].assistance_type or "",
                            listing_by_id[candidate.listing_id].applicant_description
                            or "",
                            listing_by_id[candidate.listing_id].beneficiary_description
                            or "",
                        ]
                    ),
                )
            ]
    candidates = _merge_duplicate_discovery_candidates(candidates)
    candidates.sort(key=lambda item: (item.jurisdiction.level, item.program_id))
    unassessed_listings.sort(
        key=lambda item: (
            item.jurisdiction.level,
            item.title.casefold(),
            item.listing_id,
        )
    )
    source_health = [catalog.source_health for catalog in catalogs]
    reported_health_ids = {item.source_id for item in source_health}
    source_health.extend(
        SourceHealth(source_id=source_id, status="unavailable")
        for source_id in sorted(set(unavailable_source_ids) - reported_health_ids)
    )
    unavailable = sorted(
        set(unavailable_source_ids)
        | {item.source_id for item in source_health if item.status == "unavailable"}
    )
    stale = sorted(item.source_id for item in source_health if item.status == "stale")
    if selected_program_id is not None:
        candidates = [
            candidate
            for candidate in candidates
            if candidate.program_id == selected_program_id
        ]
        unassessed_listings = [
            listing
            for listing in unassessed_listings
            if listing.listing_id == selected_program_id
            or listing.program_number == selected_program_id
            or selected_program_id in listing.related_program_ids
        ]
    return DiscoveryResult(
        status="ok" if catalogs else "unavailable",
        tenant_id=scope.tenant_id,
        navigator=profile.navigator,
        profile=profile,
        catalog_versions=sorted({item.catalog_version for item in programs}),
        candidates=candidates,
        unassessed_listings=unassessed_listings,
        profile_readiness=build_profile_readiness(
            submission,
            catalogs,
            selected_program_id=selected_program_id,
            candidate_program_ids={candidate.program_id for candidate in candidates},
            unavailable_source_ids=unavailable_source_ids,
            catalog_read_failure_source_ids=[
                item.source_id for item in catalog_read_failures
            ],
        ),
        source_health=source_health,
        unavailable_sources=unavailable,
        catalog_read_failures=list(catalog_read_failures),
        stale_sources=stale,
        coverage="partial" if catalogs else "unavailable",
    )


def _discovery_candidate_matches_need(
    query_terms: set[str],
    *,
    title_text: str,
    applicant_text: str,
    detail_text: str,
) -> bool:
    """Keep direct discovery results only when source-backed text is relevant."""
    specific_terms = query_terms - _GENERIC_RELEVANCE_TERMS
    if not specific_terms:
        return True
    title_overlap = specific_terms & _search_rank_terms(title_text)
    applicant_overlap = specific_terms & _search_rank_terms(applicant_text)
    applicant_overlap -= _GENERIC_AUDIENCE_RELEVANCE_TERMS
    detail_overlap = specific_terms & _search_rank_terms(detail_text)
    return bool(title_overlap or applicant_overlap or len(detail_overlap) >= 2)


def _merge_duplicate_program_candidates(
    candidates: Sequence[ClusterInventoryCandidate],
) -> list[ClusterInventoryCandidate]:
    """Project one canonical candidate per mapped program ID, retaining evidence."""
    merged: dict[tuple[str, str], ClusterInventoryCandidate] = {}
    for candidate in candidates:
        identity = (
            ("program", candidate.program_id)
            if candidate.record_type == "eligibility_program"
            else ("listing", candidate.listing_id)
        )
        current = merged.get(identity)
        if current is None or candidate.record_type != "eligibility_program":
            merged.setdefault(identity, candidate)
            continue

        sources = {
            (source.source_id, str(source.url)): source
            for source in [*current.sources, *candidate.sources]
        }
        health_priority = {"current": 0, "unknown": 1, "stale": 2, "unavailable": 3}
        source_health: dict[str, SourceHealth] = {}
        for health in [*current.source_health, *candidate.source_health]:
            previous = source_health.get(health.source_id)
            if (
                previous is None
                or health_priority[health.status] > health_priority[previous.status]
            ):
                source_health[health.source_id] = health
        references = {
            (reference.dataset_id, reference.source_record_id): reference
            for reference in [
                *current.criteria_references,
                *candidate.criteria_references,
            ]
        }
        bindings = {
            (binding.tenant_id, binding.pattern_version): binding
            for binding in [*current.tenant_bindings, *candidate.tenant_bindings]
        }
        merged[identity] = current.model_copy(
            update={
                "sources": sorted(sources.values(), key=lambda item: item.source_id),
                "source_health": sorted(
                    source_health.values(), key=lambda item: item.source_id
                ),
                "linked_listing_ids": sorted(
                    set(current.linked_listing_ids + candidate.linked_listing_ids)
                ),
                "criteria_references": sorted(
                    references.values(),
                    key=lambda item: (item.dataset_id, item.source_record_id),
                ),
                "criteria_available": current.criteria_available
                or candidate.criteria_available,
                "criteria_status": (
                    "available"
                    if current.criteria_available or candidate.criteria_available
                    else "not_assessed"
                ),
                "criteria_reference_status": (
                    "linked_review_only" if references else "not_linked"
                ),
                "tenant_bindings": sorted(
                    bindings.values(),
                    key=lambda item: (item.tenant_id, item.pattern_version),
                ),
            }
        )
    return list(merged.values())


def _merge_duplicate_discovery_candidates(
    candidates: Sequence[ProgramCandidate],
) -> list[ProgramCandidate]:
    """Collapse duplicate program IDs while retaining all linked sources."""
    merged: dict[str, ProgramCandidate] = {}
    health_priority = {"current": 0, "unknown": 1, "stale": 2, "unavailable": 3}
    for candidate in candidates:
        current = merged.get(candidate.program_id)
        if current is None:
            merged[candidate.program_id] = candidate
            continue
        sources = {
            (source.source_id, str(source.url)): source
            for source in [*current.sources, *candidate.sources]
        }
        source_health = {item.source_id: item for item in current.source_health}
        for item in candidate.source_health:
            previous = source_health.get(item.source_id)
            if (
                previous is None
                or health_priority[item.status] > health_priority[previous.status]
            ):
                source_health[item.source_id] = item
        merged[candidate.program_id] = current.model_copy(
            update={
                "sources": sorted(sources.values(), key=lambda item: item.source_id),
                "source_health": sorted(
                    source_health.values(), key=lambda item: item.source_id
                ),
                "linked_listing_ids": sorted(
                    set(current.linked_listing_ids + candidate.linked_listing_ids)
                ),
            }
        )
    return list(merged.values())


def jurisdiction_from_attributes(attributes: Mapping[str, object]) -> Jurisdiction:
    """Validate the limited geographic fields persisted by the approved sources."""
    values: dict[str, object] = {
        "level": attributes.get("core.geography.jurisdiction_level"),
        "country_code": attributes.get("core.geography.country_code", "US"),
    }
    if "core.geography.state_code" in attributes:
        values["state_code"] = attributes["core.geography.state_code"]
    if "core.geography.county_name" in attributes:
        values["county_name"] = attributes["core.geography.county_name"]
    return Jurisdiction.model_validate(values)


def _profile_location_match(
    profile: NormalizedProfile, jurisdiction: Jurisdiction
) -> Literal["confirmed", "unknown", "excluded"]:
    if jurisdiction.level == "federal":
        return "confirmed"
    state_code = profile.known_facts.get("state_code")
    if not isinstance(state_code, str):
        return "unknown"
    if state_code != jurisdiction.state_code:
        return "excluded"
    if jurisdiction.level == "local":
        county_name = profile.known_facts.get("county_name")
        if not isinstance(county_name, str):
            return "unknown"
        if not same_county(county_name, jurisdiction.county_name):
            return "excluded"
    return "confirmed"


def _aware_datetime(value: object) -> object | None:
    if value is None:
        return None
    from datetime import datetime

    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.utcoffset() is None:
        raise ValueError("Data API timestamps must be timezone-aware.")
    return parsed
