"""Derive sparse profile-readiness guidance from mapped source criteria."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from pydantic import Field

from clusters.eligibility_contracts import ProgramRecord, SourceReference, StrictModel

if TYPE_CHECKING:
    from clusters.eligibility_discovery import (
        DatasetCatalog,
        NormalizedProfile,
        ProfileSubmission,
    )

MAX_BASELINE_FACTS = 20
MAX_PROGRAM_EXAMPLES_PER_FACT = 5

ReadinessState = Literal["known", "missing", "ambiguous", "explicit_unknown"]
ReadinessReasonCode = Literal[
    "profile_fact_present",
    "profile_fact_missing",
    "profile_fact_ambiguous",
    "profile_fact_explicit_unknown",
]
SelectedProgramState = Literal[
    "not_selected",
    "mapped",
    "mapping_required",
    "record_absent",
    "catalog_unavailable",
    "source_unavailable",
    "conflicting_records",
]
SelectedProgramReasonCode = Literal[
    "mapped_criteria_available",
    "criteria_mapping_required",
    "record_absent",
    "catalog_read_incomplete",
    "source_fetch_unavailable",
    "conflicting_program_records",
]
SelectedProgramAction = Literal[
    "compare_selected_program",
    "prepare_criteria_mapping",
    "search_inventory_for_program",
    "retry_catalog_read",
    "retry_source_fetch",
    "review_conflicting_records",
]


class ProgramFactUse(StrictModel):
    """Source and program lineage for one normalized profile fact."""

    program_id: str
    program_title: str
    criterion_ids: list[str]
    source_references: list[SourceReference]


class ProfileFactReadiness(StrictModel):
    """Readiness state for a fact used by one or more mapped programs."""

    attribute: str
    profile_attribute: str = Field(
        description=(
            "Canonical registered attribute ID to ask for and write into the profile."
        )
    )
    state: ReadinessState
    reason_code: ReadinessReasonCode
    explanation: str
    program_count: int = Field(ge=1)
    criteria_count: int = Field(ge=1)
    programs: list[ProgramFactUse]
    additional_program_count: int = Field(ge=0)
    ask_only_after_program_selection: Literal[True] = True
    next_question_hint: str | None = None


class ProfileReadinessResult(StrictModel):
    """Return broad baseline or selected-program fact gaps, never both."""

    status: Literal["ok", "partial", "unavailable"]
    profile_id: str
    navigator: str
    profile_schema_version: str
    baseline_facts: list[ProfileFactReadiness]
    baseline_fact_count: int = Field(ge=0)
    baseline_truncated: bool = False
    incomplete_source_ids: list[str] = Field(default_factory=list)
    selected_program_id: str | None = None
    selected_program_status: SelectedProgramState = "not_selected"
    selected_program_reason_code: SelectedProgramReasonCode | None = None
    selected_program_explanation: str | None = None
    selected_program_next_action: SelectedProgramAction | None = None
    selected_program_source_references: list[SourceReference] = Field(
        default_factory=list
    )
    selected_program_facts: list[ProfileFactReadiness] = Field(default_factory=list)
    evaluation_performed: Literal[False] = False
    explanation: str


def build_profile_readiness(
    submission: ProfileSubmission,
    catalogs: Sequence[DatasetCatalog],
    *,
    selected_program_id: str | None = None,
    candidate_program_ids: set[str] | None = None,
    unavailable_source_ids: Sequence[str] = (),
    catalog_read_failure_source_ids: Sequence[str] = (),
) -> ProfileReadinessResult:
    """Summarize fields used by mapped criteria without imposing completeness.

    Baseline guidance is limited to the profile's Navigator and, when supplied,
    the program IDs retained by discovery's jurisdiction filter. Cross-Navigator
    programs remain selectable, but contribute readiness facts only after selection.
    This function never scores eligibility.
    """
    from clusters.eligibility_discovery import normalize_profile

    profile = normalize_profile(submission)
    programs = [
        program
        for catalog in catalogs
        for program in catalog.programs
        if program.data_origin == "public_source"
    ]
    baseline_programs = programs
    if selected_program_id is None:
        baseline_programs = [
            program
            for program in programs
            if program.navigator == profile.navigator
            and (
                candidate_program_ids is None
                or program.program_id in candidate_program_ids
            )
        ]
    all_baseline = _facts_for_programs(profile, baseline_programs)
    baseline = all_baseline[:MAX_BASELINE_FACTS] if selected_program_id is None else []
    selected_status: SelectedProgramState = "not_selected"
    selected_reason_code: SelectedProgramReasonCode | None = None
    selected_explanation: str | None = None
    selected_next_action: SelectedProgramAction | None = None
    selected_sources: dict[str, SourceReference] = {}
    selected_facts: list[ProfileFactReadiness] = []

    if selected_program_id is not None:
        selected_programs = [
            program for program in programs if program.program_id == selected_program_id
        ]
        listing_matches = [
            listing
            for catalog in catalogs
            for listing in catalog.listings
            if listing.listing_id == selected_program_id
            or listing.program_number == selected_program_id
            or selected_program_id in listing.related_program_ids
        ]
        if len(selected_programs) > 1 and _program_rules_conflict(selected_programs):
            selected_status = "conflicting_records"
            selected_reason_code = "conflicting_program_records"
            selected_explanation = (
                "Multiple source-backed records share this program ID but publish "
                "different mapped criteria; readiness is withheld until the record "
                "conflict is reviewed."
            )
            selected_next_action = "review_conflicting_records"
            selected_sources = {
                source.source_id: source
                for program in selected_programs
                for source in program.sources
            }
        elif selected_programs and any(
            program.criteria for program in selected_programs
        ):
            executable_programs = [
                program for program in selected_programs if program.criteria
            ]
            selected_status = "mapped"
            selected_reason_code = "mapped_criteria_available"
            selected_explanation = (
                "The selected program has active mapped criteria; readiness lists "
                "only the profile facts those criteria use."
            )
            selected_next_action = "compare_selected_program"
            selected_sources = {
                source.source_id: source
                for program in executable_programs
                for source in program.sources
            }
            selected_facts = _facts_for_programs(
                profile, executable_programs, selected_program=True
            )
        elif selected_programs:
            selected_status = "mapping_required"
            selected_reason_code = "criteria_mapping_required"
            selected_explanation = (
                "The selected source-backed program record has no executable "
                "criteria. Prepare a criteria mapping before evaluation; no "
                "eligibility result is inferred from the listing."
            )
            selected_next_action = "prepare_criteria_mapping"
            selected_sources = {
                source.source_id: source
                for program in selected_programs
                for source in program.sources
            }
        elif listing_matches:
            selected_status = "mapping_required"
            selected_reason_code = "criteria_mapping_required"
            selected_explanation = (
                "The selected source-backed program has no active mapped criteria. "
                "Its source evidence is available; prepare a criteria mapping before "
                "evaluation. No eligibility result is inferred from the listing."
            )
            selected_next_action = "prepare_criteria_mapping"
            selected_sources = {
                listing.source.source_id: listing.source for listing in listing_matches
            }
        else:
            source_fetch_failure_ids = {
                catalog.source_id
                for catalog in catalogs
                if catalog.source_health.status == "unavailable"
            }
            if source_fetch_failure_ids:
                selected_status = "source_unavailable"
                selected_reason_code = "source_fetch_unavailable"
                selected_explanation = (
                    "The program was not found in readable records, and one or "
                    "more source checks failed. This does not establish that the "
                    "program is absent. Retry source reading or search again."
                )
                selected_next_action = "retry_source_fetch"
            elif unavailable_source_ids or catalog_read_failure_source_ids:
                selected_status = "catalog_unavailable"
                selected_reason_code = "catalog_read_incomplete"
                selected_explanation = (
                    "The selected program was not found in the records that could be "
                    "read. Some catalog sources could not be read, so absence is "
                    "not confirmed. Retry the catalog read before concluding it is "
                    "absent."
                )
                selected_next_action = "retry_catalog_read"
            else:
                selected_status = "record_absent"
                selected_reason_code = "record_absent"
                selected_explanation = (
                    "The selected program ID is not present in the current shared "
                    "inventory, so no readiness facts can be derived."
                )
                selected_next_action = "search_inventory_for_program"

    incomplete_sources = sorted(
        set(unavailable_source_ids)
        | set(catalog_read_failure_source_ids)
        | {
            catalog.source_id
            for catalog in catalogs
            if catalog.source_health.status == "unavailable"
        }
        | {
            catalog.source_id
            for catalog in catalogs
            if catalog.source_health.status != "current"
        }
    )
    status: Literal["ok", "partial", "unavailable"] = (
        "unavailable"
        if not catalogs and (unavailable_source_ids or catalog_read_failure_source_ids)
        else "partial"
        if incomplete_sources
        else "ok"
    )
    return ProfileReadinessResult(
        status=status,
        profile_id=profile.profile_id,
        navigator=profile.navigator,
        profile_schema_version=profile.schema_version,
        baseline_facts=baseline,
        baseline_fact_count=len(baseline),
        baseline_truncated=(
            selected_program_id is None and len(all_baseline) > len(baseline)
        ),
        incomplete_source_ids=incomplete_sources,
        selected_program_id=selected_program_id,
        selected_program_status=selected_status,
        selected_program_reason_code=selected_reason_code,
        selected_program_explanation=selected_explanation,
        selected_program_next_action=selected_next_action,
        selected_program_source_references=sorted(
            selected_sources.values(), key=lambda source: source.source_id
        ),
        selected_program_facts=selected_facts,
        explanation=(
            "The selected-program response contains only that program's mapped "
            "criterion facts; unrelated baseline fields are omitted."
            if selected_program_id is not None
            else "Baseline facts summarize fields used by currently mapped programs; "
            "they are not required to complete discovery. After program selection, "
            "only that program's criterion fields are shown as readiness gaps."
        ),
    )


def _facts_for_programs(
    profile: NormalizedProfile,
    programs: Sequence[ProgramRecord],
    *,
    selected_program: bool = False,
) -> list[ProfileFactReadiness]:
    uses: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for program in programs:
        for criterion in program.criteria:
            attributes = {criterion.attribute}
            if criterion.context_attribute is not None:
                attributes.add(criterion.context_attribute)
            for attribute in attributes:
                entry = uses[attribute].setdefault(
                    program.program_id,
                    {
                        "program_id": program.program_id,
                        "program_title": program.title,
                        "criterion_ids": set(),
                        "source_references": {},
                    },
                )
                criterion_ids = entry["criterion_ids"]
                if isinstance(criterion_ids, set):
                    criterion_ids.add(criterion.criterion_id)
                source_references = entry["source_references"]
                if isinstance(source_references, dict):
                    source = next(
                        item
                        for item in program.sources
                        if item.source_id == criterion.source_id
                    )
                    source_references[source.source_id] = source

    # Defer this import because discovery imports this module to build readiness.
    from clusters.eligibility_discovery import PROFILE_TO_CANONICAL_ATTRIBUTE

    facts: list[ProfileFactReadiness] = []
    for attribute, program_map in uses.items():
        programs_for_fact = [
            ProgramFactUse(
                program_id=str(item["program_id"]),
                program_title=str(item["program_title"]),
                criterion_ids=sorted(item["criterion_ids"]),
                source_references=sorted(
                    item["source_references"].values(),
                    key=lambda source: source.source_id,
                ),
            )
            for item in program_map.values()
        ]
        state = _fact_state(profile, attribute)
        programs_for_fact.sort(key=lambda item: item.program_id)
        programs_to_show = programs_for_fact[:MAX_PROGRAM_EXAMPLES_PER_FACT]
        facts.append(
            ProfileFactReadiness(
                attribute=attribute,
                profile_attribute=PROFILE_TO_CANONICAL_ATTRIBUTE.get(
                    attribute, attribute
                ),
                state=state,
                reason_code=_fact_reason_code(state),
                explanation=_fact_explanation(attribute, state),
                program_count=len(program_map),
                criteria_count=sum(
                    len(item.criterion_ids) for item in programs_for_fact
                ),
                programs=programs_to_show,
                additional_program_count=len(programs_for_fact) - len(programs_to_show),
                next_question_hint=(
                    _next_question_hint(attribute, state) if selected_program else None
                ),
            )
        )
    return sorted(facts, key=lambda item: (-item.program_count, item.attribute))


def build_selected_ruleset_facts(
    profile: NormalizedProfile,
    ruleset_facts: Sequence[dict[str, object]],
) -> list[ProfileFactReadiness]:
    """Expose only missing/present fact names from verified active rulesets."""
    from clusters.eligibility_discovery import PROFILE_TO_CANONICAL_ATTRIBUTE

    uses: dict[str, dict[str, dict[str, object]]] = defaultdict(dict)
    for fact in ruleset_facts:
        attribute = fact.get("attribute")
        program_id = fact.get("program_id")
        program_title = fact.get("program_title")
        criterion_id = fact.get("criterion_id")
        source_payload = fact.get("source_reference")
        if not all(
            isinstance(value, str) and value
            for value in (attribute, program_id, program_title, criterion_id)
        ) or not isinstance(source_payload, dict):
            continue
        try:
            source = SourceReference.model_validate(source_payload)
        except (TypeError, ValueError):
            continue
        entry = uses[attribute].setdefault(
            program_id,
            {
                "program_id": program_id,
                "program_title": program_title,
                "criterion_ids": set(),
                "source_references": {},
            },
        )
        criterion_ids = entry["criterion_ids"]
        if isinstance(criterion_ids, set):
            criterion_ids.add(criterion_id)
        source_references = entry["source_references"]
        if isinstance(source_references, dict):
            source_references[source.source_id] = source

    facts: list[ProfileFactReadiness] = []
    for attribute, program_map in uses.items():
        program_uses = [
            ProgramFactUse(
                program_id=str(item["program_id"]),
                program_title=str(item["program_title"]),
                criterion_ids=sorted(item["criterion_ids"]),
                source_references=sorted(
                    item["source_references"].values(),
                    key=lambda source: source.source_id,
                ),
            )
            for item in program_map.values()
        ]
        program_uses.sort(key=lambda item: item.program_id)
        state = _fact_state(profile, attribute)
        facts.append(
            ProfileFactReadiness(
                attribute=attribute,
                profile_attribute=PROFILE_TO_CANONICAL_ATTRIBUTE.get(
                    attribute, attribute
                ),
                state=state,
                reason_code=_fact_reason_code(state),
                explanation=_fact_explanation(attribute, state),
                program_count=len(program_map),
                criteria_count=sum(len(item.criterion_ids) for item in program_uses),
                programs=program_uses[:MAX_PROGRAM_EXAMPLES_PER_FACT],
                additional_program_count=max(
                    0, len(program_uses) - MAX_PROGRAM_EXAMPLES_PER_FACT
                ),
                next_question_hint=_next_question_hint(attribute, state),
            )
        )
    return sorted(facts, key=lambda item: (-item.program_count, item.attribute))


def _fact_state(profile: NormalizedProfile, attribute: str) -> ReadinessState:
    """Resolve a canonical or projected alias without reading profile values."""
    if attribute in profile.known_facts:
        return "known"
    return profile.unknown_facts.get(attribute, "missing")


def _fact_reason_code(state: ReadinessState) -> ReadinessReasonCode:
    return {
        "known": "profile_fact_present",
        "missing": "profile_fact_missing",
        "ambiguous": "profile_fact_ambiguous",
        "explicit_unknown": "profile_fact_explicit_unknown",
    }[state]


def _fact_explanation(attribute: str, state: ReadinessState) -> str:
    label = _fact_label(attribute)
    return {
        "known": f"The profile contains {label}; the value is not repeated here.",
        "missing": f"The profile has no {label} value for this mapped criterion.",
        "ambiguous": (
            f"The profile marks {label} as ambiguous and it needs clarification."
        ),
        "explicit_unknown": (
            f"The profile explicitly records {label} as unknown; it is not treated "
            "as missing or as a negative answer."
        ),
    }[state]


def _next_question_hint(attribute: str, state: ReadinessState) -> str | None:
    """Suggest collection only for selected-program facts needing clarification."""
    if state not in {"missing", "ambiguous", "explicit_unknown"}:
        return None
    label = _fact_label(attribute)
    action = "provide" if state == "missing" else "clarify"
    return (
        f"Please {action} {label} ({attribute}) so the selected program's criteria "
        "can be compared."
    )


def _fact_label(attribute: str) -> str:
    """Name PCAFC facts by the person whose answer the program requires."""
    role_labels = {
        "core.person.veteran.va_healthcare_status": (
            "Veteran's VA health care enrollment"
        ),
        "core.person.veteran.service_discharge_status": (
            "Veteran's military discharge status"
        ),
        "core.person.veteran.service_connected_disability_rating_percent": (
            "Veteran's VA disability rating"
        ),
        "core.person.veteran.personal_care_need": (
            "Veteran's need for in-person personal care"
        ),
        "core.person.veteran.continuous_in_person_care_months": (
            "months of the Veteran's continuous in-person care need"
        ),
        "core.person.caregiver.age_years": "caregiver's age",
        "core.person.caregiver.relationship_to_veteran": (
            "caregiver's relationship to the Veteran"
        ),
        "core.person.caregiver.lives_or_willing_to_live_full_time_with_veteran": (
            "whether the caregiver lives or is willing to live full time with "
            "the Veteran"
        ),
    }
    return role_labels.get(attribute, attribute.rsplit(".", 1)[-1].replace("_", " "))


def _program_rules_conflict(programs: Sequence[ProgramRecord]) -> bool:
    """Do not ask from an ambiguous multi-source record with different rules."""
    signatures = {
        tuple(
            sorted(
                criterion.model_dump_json(exclude_none=True)
                for criterion in program.criteria
            )
        )
        for program in programs
    }
    return len(signatures) > 1


__all__ = [
    "ProfileFactReadiness",
    "ProfileReadinessResult",
    "ProgramFactUse",
    "build_profile_readiness",
]
