"""Versioned, pattern-owned contracts for the Eligibility Navigator.

These models define data boundaries only. They do not retrieve sources, ingest
catalogs, evaluate eligibility, register a cluster, or onboard a tenant.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

EligibilityAttribute = Annotated[
    str,
    Field(
        min_length=2,
        max_length=160,
        pattern=r"^[a-z][a-z0-9_-]*(?:\.[a-z][a-z0-9_-]*)*$",
    ),
]

NavigatorId = Annotated[
    str,
    Field(
        min_length=2,
        max_length=80,
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
    ),
]
Scalar = str | int | float | bool
ProfileAttributeValue = Scalar | list[Scalar | None] | None
CriterionValue = Scalar | list[Scalar] | dict[str, int | float]
CriterionOperator = Literal[
    "eq", "ne", "gt", "gte", "lt", "lte", "lte_by", "one_of", "exists"
]
ProgramDetailKind = Literal["application_step", "form", "required_document", "deadline"]


class StrictModel(BaseModel):
    """Base for shared contracts that reject undeclared fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Jurisdiction(StrictModel):
    """Geographic scope attached to a source or public program record."""

    level: Literal["federal", "state", "territory", "local", "tribal"]
    country_code: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    state_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    county_name: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def require_scope_fields(self) -> Jurisdiction:
        """Require a state for state and local scopes, and a county for local."""
        if self.level in {"state", "territory", "local"} and self.state_code is None:
            raise ValueError(
                "State, territory, and local jurisdiction require state_code."
            )
        if self.level == "local" and self.county_name is None:
            raise ValueError("Local jurisdiction requires county_name.")
        return self


class SourceReference(StrictModel):
    """Public source identity and observed provenance, without source payloads."""

    contract_version: Literal["source-reference-v1"] = "source-reference-v1"
    source_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    source_type: Literal["public_source", "synthetic_test_fixture"]
    approval_status: Literal["approved", "unverified", "not_applicable"] = "unverified"
    agency: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: HttpUrl | None = None
    jurisdiction: Jurisdiction
    retrieved_at: datetime | None = None
    effective_date: date | None = None
    publisher_version: str | None = None

    @model_validator(mode="after")
    def validate_provenance_boundary(self) -> SourceReference:
        """Require a URL and an approved source for public references, over HTTPS."""
        if self.source_type == "public_source":
            if (
                self.approval_status not in {"approved", "unverified"}
                or self.url is None
            ):
                raise ValueError(
                    "Public source references need a URL and an approved or "
                    "unverified status."
                )
            if self.url.scheme != "https":
                raise ValueError("Public catalog source URLs must use HTTPS.")
            if self.retrieved_at is None or self.retrieved_at.utcoffset() is None:
                raise ValueError(
                    "Public source retrieval time must include a timezone."
                )
        elif self.approval_status != "not_applicable" or self.url is not None:
            raise ValueError(
                "Synthetic test sources cannot claim public approval or a source URL."
            )
        return self


class CriteriaMappingWorkflowAction(StrictModel):
    """Typed client workflow handoff when a selected program has no usable rules."""

    kind: Literal["workflow"] = "workflow"
    workflow_id: Literal["source_backed_program_criteria_preparation"] = (
        "source_backed_program_criteria_preparation"
    )
    reason_code: Literal["criteria_mapping_required"] = "criteria_mapping_required"
    program_id: str = Field(min_length=1, max_length=200)
    source_ids: list[str] = Field(default_factory=list, max_length=50)


class EligibilityCriterion(StrictModel):
    """One bounded, explainable comparison against a published criterion."""

    criterion_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]{1,79}$")
    attribute: EligibilityAttribute
    operator: CriterionOperator
    value: CriterionValue
    source_id: str = Field(min_length=1)
    logic_group_id: str | None = Field(default=None, min_length=1, max_length=160)
    logic_group_operator: Literal["all_of", "any_of"] | None = None
    context_attribute: EligibilityAttribute | None = None

    @model_validator(mode="after")
    def validate_operator_value(self) -> EligibilityCriterion:
        """Require a valid value for the operator; thresholds for lte_by only."""
        if self.operator == "lte_by":
            if (
                not isinstance(self.value, dict)
                or not self.value
                or self.context_attribute is None
                or any(
                    not key.isdigit()
                    or int(key) < 1
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value < 0
                    for key, value in self.value.items()
                )
            ):
                raise ValueError(
                    "lte_by requires nonnegative numeric thresholds keyed by "
                    "a positive integer context value and a context_attribute."
                )
        elif self.context_attribute is not None:
            raise ValueError("context_attribute is only valid with lte_by.")
        if self.operator == "one_of" and not isinstance(self.value, list):
            raise ValueError("one_of requires a list of allowed values.")
        if self.operator == "exists" and not isinstance(self.value, bool):
            raise ValueError("exists requires a boolean value.")
        if self.operator in {"gt", "gte", "lt", "lte"} and (
            isinstance(self.value, bool) or not isinstance(self.value, (int, float))
        ):
            raise ValueError("Numeric comparison operators require a numeric value.")
        if self.operator != "one_of" and isinstance(self.value, list):
            raise ValueError("Only one_of accepts a list value.")
        if (self.logic_group_id is None) != (self.logic_group_operator is None):
            raise ValueError("Grouped criteria need both a group ID and operator.")
        return self


class SourceBackedProgramDetail(StrictModel):
    """One program detail explicitly supported by a cited public source."""

    kind: ProgramDetailKind
    text: str = Field(min_length=1)
    source_id: str = Field(min_length=1)


class ProgramRecord(StrictModel):
    """Pattern-owned catalog record with a verifiable source boundary."""

    contract_version: Literal["eligibility-program-v1"] = "eligibility-program-v1"
    catalog_version: str = Field(min_length=1)
    program_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]{1,99}$")
    navigator: NavigatorId
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    jurisdiction: Jurisdiction
    data_origin: Literal["public_source", "synthetic_test_fixture"]
    sources: list[SourceReference] = Field(min_length=1)
    criteria: list[EligibilityCriterion] = Field(min_length=1)
    details: list[SourceBackedProgramDetail] = Field(default_factory=list)
    source_limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_links(self) -> ProgramRecord:
        """Require unique source ids and criteria that cite a source in the record."""
        source_by_id = {source.source_id: source for source in self.sources}
        if len(source_by_id) != len(self.sources):
            raise ValueError("Source IDs must be unique within a program record.")
        if any(criterion.source_id not in source_by_id for criterion in self.criteria):
            raise ValueError("Each criterion must reference a source in this record.")
        groups: dict[str, list[EligibilityCriterion]] = {}
        for criterion in self.criteria:
            if criterion.logic_group_id is not None:
                groups.setdefault(criterion.logic_group_id, []).append(criterion)
        for group_id, members in groups.items():
            if len(members) < 2:
                raise ValueError(
                    f"Logic group {group_id!r} must contain at least two criteria."
                )
            if len({member.logic_group_operator for member in members}) != 1:
                raise ValueError(
                    f"Logic group {group_id!r} must use one consistent operator."
                )
        if any(detail.source_id not in source_by_id for detail in self.details):
            raise ValueError("Each program detail must cite a source in this record.")

        expected_type = (
            "public_source"
            if self.data_origin == "public_source"
            else "synthetic_test_fixture"
        )
        if any(source.source_type != expected_type for source in self.sources):
            raise ValueError(
                "Program data origin must match every referenced source type."
            )
        if (
            self.data_origin == "public_source"
            and any(source.approval_status != "approved" for source in self.sources)
            and self.catalog_version != "request-scoped"
        ):
            raise ValueError(
                "Shared catalog program records require approved public sources; "
                "request-scoped candidates may retain unverified source status."
            )
        return self


class FarmerProducerProfile(StrictModel):
    """Normalized, non-identifying inputs for the farmer-producer Navigator."""

    profile_id: str = Field(pattern=r"^synthetic-[a-z0-9][a-z0-9-]{2,63}$")
    navigator: Literal["farmer_producer"] = "farmer_producer"
    schema_version: Literal["farmer-profile-v1", "farmer-profile-v2"] = (
        "farmer-profile-v1"
    )
    state_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    county_name: str | None = None
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{5}$")
    acreage: float | None = Field(default=None, ge=0)
    commodities: list[str] = Field(default_factory=list)
    years_farming: int | None = Field(default=None, ge=0)
    veteran_status: bool | None = None
    beginning_farmer_status: bool | None = None
    land_tenure: Literal["owned", "leased", "mixed", "unknown"] | None = None
    operation_type: str | None = None
    operation_goal: str | None = None
    loss_type: str | None = None
    loss_date: date | None = None
    resource_concerns: list[str] = Field(default_factory=list)
    current_practices: list[str] = Field(default_factory=list)
    farm_type: str | None = None
    crop_year: int | None = Field(default=None, ge=1900, le=2200)
    coverage_type: str | None = None
    attributes: dict[EligibilityAttribute, ProfileAttributeValue] = Field(
        default_factory=dict, max_length=100
    )


class SeniorCaregiverProfile(StrictModel):
    """Normalized, non-identifying inputs for the senior-caregiver Navigator."""

    profile_id: str = Field(pattern=r"^synthetic-[a-z0-9][a-z0-9-]{2,63}$")
    navigator: Literal["senior_caregiver"] = "senior_caregiver"
    schema_version: Literal[
        "senior-profile-v1", "senior-profile-v2", "senior-profile-v3"
    ] = "senior-profile-v1"
    state_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    county_name: str | None = None
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{5}$")
    age_band: (
        Literal["under_60", "60_64", "65_74", "75_plus", "65_plus", "adult", "unknown"]
        | None
    ) = None
    annual_household_income_usd: int | None = Field(default=None, ge=0)
    monthly_gross_income_usd: int | None = Field(default=None, ge=0)
    monthly_net_income_usd: int | None = Field(default=None, ge=0)
    financial_resources_band: Literal["low", "moderate", "high", "unknown"] | None = (
        None
    )
    household_size: int | None = Field(default=None, ge=1)
    medicare_status: Literal["enrolled", "not_enrolled", "unknown"] | None = None
    food_assistance_need: bool | None = None
    housing_status: Literal["renter", "owner", "unstable", "unknown"] | None = None
    utility_assistance_need: bool | None = None
    veteran_status: bool | None = None
    caregiver_relationship: str | None = None
    daily_care_need: bool | None = None
    housebound_status: bool | None = None
    va_healthcare_status: Literal["enrolled", "not_enrolled", "unknown"] | None = None
    va_pension_status: Literal["receiving", "not_receiving", "unknown"] | None = None
    service_discharge_status: (
        Literal["qualifying", "non_qualifying", "unknown"] | None
    ) = None
    service_connected_disability_status: bool | None = None
    caregiver_age_years: int | None = Field(
        default=None,
        ge=0,
        description="Age in years of the caregiver, distinct from the Veteran.",
    )
    caregiver_relationship_to_veteran: (
        Literal[
            "spouse",
            "son",
            "daughter",
            "parent",
            "stepfamily_member",
            "extended_family_member",
            "other",
            "unknown",
        ]
        | None
    ) = Field(
        default=None,
        description="Caregiver's relationship to the Veteran receiving care.",
    )
    caregiver_lives_or_willing_to_live_full_time_with_veteran: bool | None = Field(
        default=None,
        description=(
            "Whether the caregiver lives full time with, or is willing to live "
            "full time with, the Veteran if designated."
        ),
    )
    veteran_va_healthcare_status: (
        Literal["enrolled", "not_enrolled", "unknown"] | None
    ) = Field(
        default=None,
        description="VA health care enrollment of the Veteran receiving care.",
    )
    veteran_service_discharge_status: (
        Literal["qualifying", "non_qualifying", "unknown"] | None
    ) = Field(
        default=None,
        description=(
            "Whether the Veteran has a qualifying military discharge or medical "
            "discharge date."
        ),
    )
    veteran_service_connected_disability_rating_percent: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="Individual or combined VA disability rating of the Veteran.",
    )
    veteran_personal_care_need: bool | None = Field(
        default=None,
        description="Whether the Veteran needs in-person personal care services.",
    )
    veteran_continuous_in_person_care_months: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Continuous duration in months of the Veteran's need for in-person "
            "personal care services."
        ),
    )
    attributes: dict[EligibilityAttribute, ProfileAttributeValue] = Field(
        default_factory=dict, max_length=100
    )
    disability_status: bool | None = None
    transportation_need: bool | None = None

    @model_validator(mode="after")
    def role_facts_require_v3(self) -> SeniorCaregiverProfile:
        """Require the v3 senior profile for role-specific caregiver facts."""
        role_fields = {
            "caregiver_age_years",
            "caregiver_relationship_to_veteran",
            "caregiver_lives_or_willing_to_live_full_time_with_veteran",
            "veteran_va_healthcare_status",
            "veteran_service_discharge_status",
            "veteran_service_connected_disability_rating_percent",
            "veteran_personal_care_need",
            "veteran_continuous_in_person_care_months",
        }
        role_scoped_attributes = {
            attribute
            for attribute in self.attributes
            if attribute.startswith(("core.person.veteran.", "core.person.caregiver."))
        }
        if self.schema_version != "senior-profile-v3" and (
            role_fields.intersection(self.model_fields_set) or role_scoped_attributes
        ):
            raise ValueError("Role-specific caregiver facts require senior-profile-v3.")
        return self


class LowIncomeAssistanceProfile(StrictModel):
    """Normalized, non-identifying inputs for the low-income Navigator."""

    profile_id: str = Field(pattern=r"^synthetic-[a-z0-9][a-z0-9-]{2,63}$")
    navigator: Literal["low_income_assistance"] = "low_income_assistance"
    schema_version: Literal["household-profile-v1", "household-profile-v2"] = (
        "household-profile-v1"
    )
    state_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    county_name: str | None = None
    postal_code: str | None = Field(default=None, pattern=r"^[0-9]{5}$")
    annual_household_income_usd: int | None = Field(default=None, ge=0)
    monthly_gross_income_usd: int | None = Field(default=None, ge=0)
    monthly_net_income_usd: int | None = Field(default=None, ge=0)
    household_size: int | None = Field(default=None, ge=1)
    household_adults: int | None = Field(default=None, ge=0)
    household_children: int | None = Field(default=None, ge=0)
    employment_status: (
        Literal["employed", "unemployed", "part_time", "unknown"] | None
    ) = None
    housing_status: Literal["renter", "owner", "unstable", "unknown"] | None = None
    food_assistance_need: bool | None = None
    utility_assistance_need: bool | None = None
    health_coverage_status: Literal["insured", "uninsured", "unknown"] | None = None
    veteran_status: bool | None = None
    disability_status: bool | None = None
    childcare_need: bool | None = None
    transportation_need: bool | None = None
    disaster_impact: bool | None = None
    attributes: dict[EligibilityAttribute, ProfileAttributeValue] = Field(
        default_factory=dict, max_length=100
    )

    @model_validator(mode="after")
    def household_counts_match_size(self) -> LowIncomeAssistanceProfile:
        """Require household member counts to add up to the household size."""
        if self.household_size is not None:
            counts = (self.household_adults, self.household_children)
            if (
                all(count is not None for count in counts)
                and sum(counts) != self.household_size
            ):
                raise ValueError("Household member counts must equal household_size.")
        return self


class GeneralProfile(StrictModel):
    """Sparse profile for needs beyond the demo Navigator presets."""

    profile_id: str = Field(pattern=r"^synthetic-[a-z0-9][a-z0-9-]{2,63}$")
    navigator: Literal["general"] = "general"
    schema_version: str = Field(min_length=1, max_length=80)
    attributes: dict[EligibilityAttribute, ProfileAttributeValue] = Field(
        min_length=1, max_length=100
    )


Profile = Annotated[
    FarmerProducerProfile
    | SeniorCaregiverProfile
    | LowIncomeAssistanceProfile
    | GeneralProfile,
    Field(discriminator="navigator"),
]


class TenantFieldMapping(StrictModel):
    """Tenant vocabulary mapped to one Navigator's normalized profile fields."""

    contract_version: Literal["eligibility-mapping-v1"] = "eligibility-mapping-v1"
    tenant_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    navigator: NavigatorId
    profile_schema_version: str = Field(min_length=1)
    field_mappings: dict[str, EligibilityAttribute] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_mapping(self) -> TenantFieldMapping:
        """Require trimmed field names and each attribute mapped at most once."""
        if any(
            not source_name.strip() or source_name != source_name.strip()
            for source_name in self.field_mappings
        ):
            raise ValueError("Tenant source field names must be nonblank and trimmed.")
        if len(set(self.field_mappings.values())) != len(self.field_mappings):
            raise ValueError("Each normalized attribute can be mapped only once.")
        return self


NAVIGATOR_ATTRIBUTES: dict[str, set[str]] = {
    "farmer_producer": {
        "state_code",
        "county_name",
        "postal_code",
        "acreage",
        "commodities",
        "years_farming",
        "veteran_status",
        "beginning_farmer_status",
        "land_tenure",
        "operation_type",
        "loss_type",
        "loss_date",
        "resource_concerns",
        "operation_goal",
        "current_practices",
        "farm_type",
        "crop_year",
        "coverage_type",
    },
    "senior_caregiver": {
        "state_code",
        "county_name",
        "postal_code",
        "age_band",
        "annual_household_income_usd",
        "monthly_gross_income_usd",
        "monthly_net_income_usd",
        "household_size",
        "medicare_status",
        "financial_resources_band",
        "food_assistance_need",
        "housing_status",
        "utility_assistance_need",
        "veteran_status",
        "caregiver_relationship",
        "daily_care_need",
        "housebound_status",
        "va_healthcare_status",
        "va_pension_status",
        "service_discharge_status",
        "service_connected_disability_status",
        "caregiver_age_years",
        "caregiver_relationship_to_veteran",
        "caregiver_lives_or_willing_to_live_full_time_with_veteran",
        "veteran_va_healthcare_status",
        "veteran_service_discharge_status",
        "veteran_service_connected_disability_rating_percent",
        "veteran_personal_care_need",
        "veteran_continuous_in_person_care_months",
        "disability_status",
        "transportation_need",
    },
    "low_income_assistance": {
        "state_code",
        "county_name",
        "postal_code",
        "annual_household_income_usd",
        "monthly_gross_income_usd",
        "monthly_net_income_usd",
        "household_size",
        "household_adults",
        "household_children",
        "employment_status",
        "housing_status",
        "food_assistance_need",
        "utility_assistance_need",
        "health_coverage_status",
        "veteran_status",
        "disability_status",
        "childcare_need",
        "transportation_need",
        "disaster_impact",
    },
}

NAVIGATOR_PROFILE_VERSIONS: dict[str, set[str]] = {
    "farmer_producer": {"farmer-profile-v1", "farmer-profile-v2"},
    "senior_caregiver": {
        "senior-profile-v1",
        "senior-profile-v2",
        "senior-profile-v3",
    },
    "low_income_assistance": {"household-profile-v1", "household-profile-v2"},
}

ELIGIBILITY_ATTRIBUTES = set().union(*NAVIGATOR_ATTRIBUTES.values())


class CriterionEvidence(StrictModel):
    """Outcome and citations for one rule, without copying profile values."""

    criterion_id: str = Field(min_length=1)
    outcome: Literal["met", "not_met", "unknown"]
    source_ids: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1)


class ProgramCriterionEvidence(StrictModel):
    """outcome and citations without changing the later result contract."""

    criterion_id: str = Field(min_length=1)
    outcome: Literal["met", "unmet", "unknown", "not_applicable"]
    source_ids: list[str] = Field(min_length=1)
    explanation: str = Field(min_length=1)
    reason_code: (
        Literal[
            "profile_fact_missing",
            "profile_fact_ambiguous",
            "profile_fact_explicit_unknown",
            "profile_fact_insufficient_granularity",
            "profile_value_outside_rule_domain",
            "criterion_comparison_not_evaluable",
            "jurisdiction_mismatch",
            "source_catalog_conflict",
            "source_stale",
            "source_unavailable",
            "source_health_unknown",
        ]
        | None
    ) = None
    recommended_action: (
        Literal[
            "ask_user_for_profile_fact",
            "clarify_profile_fact",
            "ask_for_more_precise_profile_fact",
            "review_rule_coverage",
            "review_rule_mapping",
            "no_user_action",
            "review_source_conflict",
            "refresh_source",
            "verify_source_health",
        ]
        | None
    ) = None
    profile_attribute: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Canonical registered profile attribute ID to use when asking for and "
            "persisting this fact."
        ),
    )


class ProgramLogicGroupEvidence(StrictModel):
    """Tri-state rollup for one explicitly declared all/any criteria group."""

    logic_group_id: str = Field(min_length=1)
    operator: Literal["all_of", "any_of"]
    outcome: Literal["met", "unmet", "unknown", "not_applicable"]
    criterion_ids: list[str] = Field(min_length=2)
    explanation: str = Field(min_length=1)


class ProgramEvaluationResult(StrictModel):
    """Deterministic criterion outcomes with source citations, not agency decisions."""

    contract_version: Literal["eligibility-evaluation-v2"] = "eligibility-evaluation-v2"
    program_id: str = Field(min_length=1)
    navigator: NavigatorId
    profile_id: str = Field(min_length=1)
    profile_schema_version: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    evidence: list[ProgramCriterionEvidence] = Field(min_length=1)
    logic_groups: list[ProgramLogicGroupEvidence] = Field(default_factory=list)
    source_references: list[SourceReference] = Field(min_length=1)
    evaluation_scope: Literal["mapped_criteria_only"] = "mapped_criteria_only"
    official_eligibility_determination: Literal[False] = False


class ProgramComparisonSummary(StrictModel):
    """Explainable comparison-unit counts, never an eligibility probability."""

    status: Literal[
        "all_evaluable_met",
        "known_unmet",
        "mixed_known_and_unknown",
        "needs_information",
        "no_evaluable_units",
        "not_evaluated",
    ]
    matched_units: int = Field(ge=0)
    evaluable_units: int = Field(ge=0)
    unknown_units: int = Field(ge=0)
    not_applicable_units: int = Field(ge=0)
    matched_fraction: float | None = Field(default=None, ge=0, le=1)
    explanation: str = Field(min_length=1)


class ProgramResultEntry(StrictModel):
    """One source-backed program result with bounded comparison evidence."""

    status: Literal["current", "stale", "unknown", "unavailable", "conflicting"]
    program_id: str
    navigator: NavigatorId
    title: str
    description: str
    catalog_version: str
    jurisdiction: Jurisdiction
    criteria_evidence: list[ProgramCriterionEvidence]
    logic_groups: list[ProgramLogicGroupEvidence] = Field(default_factory=list)
    comparison: ProgramComparisonSummary
    comparison_rank: int | None = Field(default=None, ge=1)
    details: list[SourceBackedProgramDetail]
    unavailable_detail_kinds: list[ProgramDetailKind]
    source_references: list[SourceReference]
    source_health: list[SourceHealth]
    source_limitations: list[str]
    coverage: Literal["partial_source_backed_screening"] = (
        "partial_source_backed_screening"
    )
    evidence_completeness: Literal[
        "complete_for_cataloged_criteria", "incomplete_for_cataloged_criteria"
    ]
    evaluation_performed: bool
    official_eligibility_determination: Literal[False] = False


class ProgramResults(StrictModel):
    """Ranked, source-backed comparisons with independent coverage reporting."""

    contract_version: Literal["eligibility-program-results-v2"] = (
        "eligibility-program-results-v2"
    )
    status: Literal["ok", "partial", "unavailable"]
    tenant_id: str
    navigator: NavigatorId
    profile_id: str
    profile_schema_version: str
    catalog_versions: list[str]
    ordering_policy: Literal["matched-evaluable-fraction-v1"] = (
        "matched-evaluable-fraction-v1"
    )
    ordering_basis: Literal[
        "current-source_then_matched-fraction_desc_then_program-id"
    ] = "current-source_then_matched-fraction_desc_then_program-id"
    programs: list[ProgramResultEntry]
    source_health: list[SourceHealth]
    unavailable_sources: list[str]
    stale_sources: list[str]
    coverage: Literal["partial", "unavailable"]
    official_eligibility_determination: Literal[False] = False


class ScreeningResult(StrictModel):
    """Informational screening result with match and source health separated."""

    contract_version: Literal["eligibility-result-v1"] = "eligibility-result-v1"
    tenant_id: str = Field(min_length=1)
    navigator: NavigatorId
    profile_id: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    match_status: Literal[
        "strong_potential_match", "possible_match", "more_information", "no_match"
    ]
    source_health: Literal["current", "stale", "unavailable", "unknown"]
    agency_decision: Literal["not_provided"] = "not_provided"
    evidence: list[CriterionEvidence] = Field(default_factory=list)
    referred_source_ids: list[str] = Field(default_factory=list)


class SourceHealth(StrictModel):
    """Operational freshness state kept independent from a match outcome."""

    contract_version: Literal["source-health-v1"] = "source-health-v1"
    source_id: str = Field(min_length=1)
    status: Literal["current", "stale", "unavailable", "unknown"]
    checked_at: datetime | None = None
    last_successful_retrieval: datetime | None = None
    detail: str | None = None

    @model_validator(mode="after")
    def timestamps_are_timezone_aware(self) -> SourceHealth:
        """Require timezone-aware source-health timestamps."""
        for value in (self.checked_at, self.last_successful_retrieval):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Source-health timestamps must include a timezone.")
        return self
