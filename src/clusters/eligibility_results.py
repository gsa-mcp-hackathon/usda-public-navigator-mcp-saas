"""Assemble source-backed program details and criterion evidence."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from clusters.eligibility_contracts import (
    EligibilityCriterion,
    ProgramComparisonSummary,
    ProgramCriterionEvidence,
    ProgramDetailKind,
    ProgramLogicGroupEvidence,
    ProgramRecord,
    ProgramResultEntry,
    ProgramResults,
    SourceBackedProgramDetail,
    SourceHealth,
)
from clusters.eligibility_discovery import (
    DatasetCatalog,
    DiscoveryScope,
    ProfileSubmission,
    _profile_location_match,
    merge_same_program_records,
    normalize_profile,
)
from clusters.eligibility_evaluation import (
    evaluate_program_criteria,
    summarize_logic_groups,
)

DETAIL_KINDS: tuple[ProgramDetailKind, ...] = (
    "application_step",
    "form",
    "required_document",
    "deadline",
)


def assemble_program_results(
    submission: ProfileSubmission,
    scope: DiscoveryScope,
    catalogs: Sequence[DatasetCatalog],
    unavailable_source_ids: Sequence[str] = (),
) -> ProgramResults:
    """Return source-backed detail and evidence with a stable, non-score order.

    Stale or unknown source freshness prevents criterion evaluation and produces
    unknown evidence. Duplicate program IDs from separate catalogs are reported
    as conflicts rather than resolved by source order.
    """
    profile = normalize_profile(submission)
    matching: dict[str, list[tuple[ProgramRecord, DatasetCatalog]]] = defaultdict(list)
    for catalog in catalogs:
        for program in catalog.programs:
            if program.data_origin != "public_source":
                continue
            if _profile_location_match(profile, program.jurisdiction) == "excluded":
                continue
            matching[program.program_id].append((program, catalog))

    results = [
        _program_result(submission, program_group)
        for _, program_group in sorted(
            matching.items(), key=lambda item: (item[0].casefold(), item[0])
        )
    ]
    results = _rank_program_results(results)
    source_health = [catalog.source_health for catalog in catalogs]
    reported_source_ids = {item.source_id for item in source_health}
    source_health.extend(
        SourceHealth(source_id=source_id, status="unavailable")
        for source_id in sorted(set(unavailable_source_ids) - reported_source_ids)
    )
    unavailable = sorted(
        set(unavailable_source_ids)
        | {item.source_id for item in source_health if item.status == "unavailable"}
    )
    stale = sorted(item.source_id for item in source_health if item.status == "stale")
    versions = sorted(
        {
            program.catalog_version
            for catalog in catalogs
            for program in catalog.programs
        }
    )
    if not catalogs:
        status = "unavailable"
        coverage = "unavailable"
    elif (
        unavailable
        or any(item.status != "current" for item in source_health)
        or any(item.status != "current" for item in results)
    ):
        status = "partial"
        coverage = "partial"
    else:
        status = "ok"
        coverage = "partial"
    return ProgramResults(
        status=status,
        tenant_id=scope.tenant_id,
        navigator=profile.navigator,
        profile_id=profile.profile_id,
        profile_schema_version=profile.schema_version,
        catalog_versions=versions,
        programs=results,
        source_health=source_health,
        unavailable_sources=unavailable,
        stale_sources=stale,
        coverage=coverage,
    )


def _program_result(
    submission: ProfileSubmission,
    group: list[tuple[ProgramRecord, DatasetCatalog]],
) -> ProgramResultEntry:
    programs = merge_same_program_records([program for program, _ in group])
    health = [catalog.source_health for _, catalog in group]
    references = [source for program in programs for source in program.sources]
    limitations = list(
        dict.fromkeys(
            limitation
            for program in programs
            for limitation in program.source_limitations
        )
    )
    conflicting = len(programs) > 1
    freshness = "conflicting" if conflicting else _freshness_state(health)
    if conflicting:
        reason_code = "source_catalog_conflict"
        recommended_action = "review_source_conflict"
        explanation = (
            "Multiple source catalogs contain this program ID; resolve the "
            "catalog conflict before evaluation."
        )
        evidence = [
            ProgramCriterionEvidence(
                criterion_id=criterion.criterion_id,
                outcome="unknown",
                source_ids=[criterion.source_id],
                explanation=explanation,
                reason_code=reason_code,
                recommended_action=recommended_action,
            )
            for program in programs
            for criterion in program.criteria
        ]
        details: list[SourceBackedProgramDetail] = []
        unavailable_detail_kinds = list(DETAIL_KINDS)
        logic_groups = []
    elif freshness != "current":
        program = programs[0]
        reason_code, recommended_action = {
            "stale": ("source_stale", "refresh_source"),
            "unavailable": ("source_unavailable", "refresh_source"),
            "unknown": ("source_health_unknown", "verify_source_health"),
        }[freshness]
        evidence = [
            ProgramCriterionEvidence(
                criterion_id=criterion.criterion_id,
                outcome="unknown",
                source_ids=[criterion.source_id],
                explanation=(
                    f"Source freshness is {freshness}; the criterion was not evaluated."
                ),
                reason_code=reason_code,
                recommended_action=recommended_action,
            )
            for criterion in program.criteria
        ]
        details = program.details
        unavailable_detail_kinds = _unavailable_details(details)
        logic_groups = summarize_logic_groups(program.criteria, evidence)
    else:
        program = programs[0]
        evaluation = evaluate_program_criteria(submission, program)
        evidence = evaluation.evidence
        logic_groups = evaluation.logic_groups
        details = program.details
        unavailable_detail_kinds = _unavailable_details(details)
    program = programs[0]
    comparison = _comparison_summary(
        program.criteria,
        evidence,
        logic_groups,
        evaluated=(freshness == "current" and not conflicting),
    )
    determinate = all(item.outcome != "unknown" for item in evidence) and all(
        item.outcome != "unknown" for item in logic_groups
    )
    return ProgramResultEntry(
        status=freshness,
        program_id=program.program_id,
        navigator=program.navigator,
        title=program.title,
        description=program.description,
        catalog_version=program.catalog_version,
        jurisdiction=program.jurisdiction,
        criteria_evidence=evidence,
        logic_groups=logic_groups,
        comparison=comparison,
        details=details,
        unavailable_detail_kinds=unavailable_detail_kinds,
        source_references=references,
        source_health=health,
        source_limitations=limitations,
        evidence_completeness=(
            "complete_for_cataloged_criteria"
            if determinate
            else "incomplete_for_cataloged_criteria"
        ),
        evaluation_performed=(freshness == "current"),
    )


def _comparison_summary(
    criteria: Sequence[EligibilityCriterion],
    evidence: Sequence[ProgramCriterionEvidence],
    logic_groups: Sequence[ProgramLogicGroupEvidence],
    *,
    evaluated: bool,
) -> ProgramComparisonSummary:
    """Summarize independent clauses and declared compound groups once each."""
    if not evaluated:
        return ProgramComparisonSummary(
            status="not_evaluated",
            matched_units=0,
            evaluable_units=0,
            unknown_units=0,
            not_applicable_units=0,
            explanation=(
                "No comparison score was calculated because the source is not "
                "current or the program record is conflicting."
            ),
        )

    grouped_ids = {
        criterion.criterion_id
        for criterion in criteria
        if criterion.logic_group_id is not None
    }
    unit_outcomes = [
        item.outcome for item in evidence if item.criterion_id not in grouped_ids
    ]
    unit_outcomes.extend(item.outcome for item in logic_groups)
    matched = unit_outcomes.count("met")
    unmet = unit_outcomes.count("unmet")
    unknown = unit_outcomes.count("unknown")
    not_applicable = unit_outcomes.count("not_applicable")
    evaluable = matched + unmet
    fraction = matched / evaluable if evaluable else None

    if unknown and evaluable:
        status = "mixed_known_and_unknown"
    elif unknown:
        status = "needs_information"
    elif evaluable == 0:
        status = "no_evaluable_units"
    elif unmet:
        status = "known_unmet"
    else:
        status = "all_evaluable_met"

    fraction_text = (
        f"{matched}/{evaluable} evaluable comparison units met"
        if fraction is not None
        else "no comparison units were evaluable"
    )
    return ProgramComparisonSummary(
        status=status,
        matched_units=matched,
        evaluable_units=evaluable,
        unknown_units=unknown,
        not_applicable_units=not_applicable,
        matched_fraction=fraction,
        explanation=(
            f"{fraction_text}; {unknown} comparison units remain unknown and "
            f"{not_applicable} are not applicable. The fraction describes only "
            "cataloged mapped rules, excludes unknown units from its denominator, "
            "and is not an eligibility probability or agency decision."
        ),
    )


def _rank_program_results(
    results: Sequence[ProgramResultEntry],
) -> list[ProgramResultEntry]:
    """Order current scored comparisons first; leave unscored results explicit."""
    ordered = sorted(
        results,
        key=lambda item: (
            item.status != "current" or item.comparison.matched_fraction is None,
            -item.comparison.matched_fraction
            if item.comparison.matched_fraction is not None
            else 0,
            item.program_id.casefold(),
            item.program_id,
        ),
    )
    ranked: list[ProgramResultEntry] = []
    previous_fraction: float | None = None
    current_rank = 0
    scored_count = 0
    for item in ordered:
        fraction = item.comparison.matched_fraction
        if item.status == "current" and fraction is not None:
            scored_count += 1
            if fraction != previous_fraction:
                current_rank = scored_count
                previous_fraction = fraction
            ranked.append(item.model_copy(update={"comparison_rank": current_rank}))
        else:
            ranked.append(item.model_copy(update={"comparison_rank": None}))
    return ranked


def _freshness_state(health: Sequence[SourceHealth]) -> str:
    statuses = {item.status for item in health}
    if statuses == {"current"}:
        return "current"
    if "stale" in statuses:
        return "stale"
    if "unavailable" in statuses:
        return "unavailable"
    return "unknown"


def _unavailable_details(
    details: Sequence[SourceBackedProgramDetail],
) -> list[ProgramDetailKind]:
    present = {detail.kind for detail in details}
    return [kind for kind in DETAIL_KINDS if kind not in present]
