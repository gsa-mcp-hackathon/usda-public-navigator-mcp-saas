"""Evaluate bounded public program criteria against fictional profiles."""

from __future__ import annotations

from collections.abc import Sequence

from clusters.eligibility_contracts import (
    EligibilityCriterion,
    Jurisdiction,
    ProgramCriterionEvidence,
    ProgramEvaluationResult,
    ProgramLogicGroupEvidence,
    ProgramRecord,
    SourceReference,
)
from clusters.eligibility_discovery import (
    PROFILE_TO_CANONICAL_ATTRIBUTE,
    ProfileSubmission,
    normalize_profile,
)
from clusters.eligibility_geography import same_county


class UnsupportedCriterionError(ValueError):
    """A validated rule cannot be compared with the selected profile value."""


def evaluate_program_criteria(
    submission: ProfileSubmission,
    program: ProgramRecord,
) -> ProgramEvaluationResult:
    """Return source-cited outcomes without ranking or deciding official eligibility.

    A known profile location outside the program jurisdiction makes each criterion
    not applicable. Missing or ambiguous location does not exclude the program;
    affected criteria remain unknown.
    """
    profile = normalize_profile(submission)
    sources_by_id = {source.source_id: source for source in program.sources}
    location_applies = _location_applies(
        profile.known_facts,
        program.jurisdiction,
    )
    evidence = []
    for criterion in program.criteria:
        cited_source = sources_by_id.get(criterion.source_id)
        if cited_source is None:
            raise UnsupportedCriterionError(
                f"Criterion {criterion.criterion_id} has no source reference."
            )
        reason_code = None
        recommended_action = None
        profile_attribute = None
        if location_applies is False:
            outcome = "not_applicable"
            reason_code = "jurisdiction_mismatch"
            recommended_action = "no_user_action"
            explanation = (
                "The known profile location is outside this program's jurisdiction."
            )
        elif location_applies is None:
            outcome = "unknown"
            location_attribute = _location_gap_attribute(
                profile.known_facts,
                program.jurisdiction,
            )
            reason = profile.unknown_facts.get(location_attribute, "missing")
            reason_code, recommended_action = _profile_gap_action(reason)
            profile_attribute = _reported_profile_attribute(location_attribute)
            explanation = (
                f"The profile value for {profile_attribute} is {reason}; "
                "this criterion's jurisdiction cannot be confirmed."
            )
        elif (
            criterion.attribute in profile.unknown_facts
            or criterion.attribute not in profile.known_facts
        ):
            outcome = "unknown"
            reason = profile.unknown_facts.get(criterion.attribute, "missing")
            reason_code, recommended_action = _profile_gap_action(reason)
            profile_attribute = _reported_profile_attribute(criterion.attribute)
            explanation = (
                f"The profile value is {reason}; this criterion cannot be compared."
            )
        elif criterion.operator == "lte_by" and (
            criterion.context_attribute in profile.unknown_facts
            or criterion.context_attribute not in profile.known_facts
        ):
            outcome = "unknown"
            reason = profile.unknown_facts.get(criterion.context_attribute, "missing")
            reason_code, recommended_action = _profile_gap_action(reason)
            profile_attribute = _reported_profile_attribute(criterion.context_attribute)
            explanation = (
                f"The profile value for {profile_attribute} is {reason}; "
                "the threshold row cannot be selected."
            )
        else:
            try:
                met = _criterion_matches(
                    profile.known_facts[criterion.attribute],
                    criterion,
                    profile.known_facts,
                )
            except UnsupportedCriterionError as exc:
                raise UnsupportedCriterionError(
                    f"Criterion {criterion.criterion_id} is unsupported: {exc}"
                ) from exc
            if met is None:
                outcome = "unknown"
                explanation = _unknown_criterion_explanation(criterion)
                if criterion.operator == "lte_by":
                    reason_code = "profile_value_outside_rule_domain"
                    recommended_action = "review_rule_coverage"
                    profile_attribute = _reported_profile_attribute(
                        criterion.context_attribute
                    )
                elif criterion.attribute == "age_band":
                    reason_code = "profile_fact_insufficient_granularity"
                    recommended_action = "ask_for_more_precise_profile_fact"
                    profile_attribute = _reported_profile_attribute(criterion.attribute)
                else:
                    reason_code = "criterion_comparison_not_evaluable"
                    recommended_action = "review_rule_mapping"
                    profile_attribute = _reported_profile_attribute(criterion.attribute)
            else:
                outcome = "met" if met else "unmet"
                explanation = (
                    "The known profile value satisfies the source-backed criterion."
                    if met
                    else (
                        "The known profile value does not satisfy the "
                        "source-backed criterion."
                    )
                )
        evidence.append(
            ProgramCriterionEvidence(
                criterion_id=criterion.criterion_id,
                outcome=outcome,
                source_ids=[cited_source.source_id],
                explanation=explanation,
                reason_code=reason_code,
                recommended_action=recommended_action,
                profile_attribute=profile_attribute,
            )
        )

    return ProgramEvaluationResult(
        program_id=program.program_id,
        navigator=program.navigator,
        profile_id=profile.profile_id,
        profile_schema_version=profile.schema_version,
        catalog_version=program.catalog_version,
        evidence=evidence,
        logic_groups=summarize_logic_groups(program.criteria, evidence),
        source_references=_criterion_sources(program.criteria, sources_by_id),
    )


def _reported_profile_attribute(attribute: str) -> str:
    """Return the registered profile key clients can persist as an answer."""
    return PROFILE_TO_CANONICAL_ATTRIBUTE.get(attribute, attribute)


def _profile_gap_action(
    reason: str,
) -> tuple[str, str]:
    """Choose a user question only when a profile answer can resolve the gap."""
    if reason == "missing":
        return "profile_fact_missing", "ask_user_for_profile_fact"
    if reason == "ambiguous":
        return "profile_fact_ambiguous", "clarify_profile_fact"
    return "profile_fact_explicit_unknown", "clarify_profile_fact"


def summarize_logic_groups(
    criteria: Sequence[EligibilityCriterion],
    evidence: Sequence[ProgramCriterionEvidence],
) -> list[ProgramLogicGroupEvidence]:
    """Roll up only explicitly declared all/any groups using conservative logic."""
    if len(criteria) != len(evidence):
        raise UnsupportedCriterionError(
            "Criterion evidence must align with the evaluated criteria."
        )

    members: dict[str, list[tuple[EligibilityCriterion, str]]] = {}
    for criterion, item in zip(criteria, evidence, strict=True):
        if item.criterion_id != criterion.criterion_id:
            raise UnsupportedCriterionError(
                "Criterion evidence order does not match evaluated criteria."
            )
        if criterion.logic_group_id is not None:
            members.setdefault(criterion.logic_group_id, []).append(
                (criterion, item.outcome)
            )
    summaries: list[ProgramLogicGroupEvidence] = []
    for group_id, group_members in members.items():
        group_criteria = [criterion for criterion, _ in group_members]
        operator = group_criteria[0].logic_group_operator
        if operator is None or len(group_criteria) < 2:
            raise UnsupportedCriterionError(
                f"Logic group {group_id!r} lacks a valid declared operator."
            )
        group_outcomes = [outcome for _, outcome in group_members]
        if all(outcome == "not_applicable" for outcome in group_outcomes):
            result = "not_applicable"
        elif operator == "all_of" and "unmet" in group_outcomes:
            result = "unmet"
        elif operator == "any_of" and "met" in group_outcomes:
            result = "met"
        elif all(outcome == "met" for outcome in group_outcomes):
            result = "met"
        elif all(outcome == "unmet" for outcome in group_outcomes):
            result = "unmet"
        else:
            result = "unknown"
        summaries.append(
            ProgramLogicGroupEvidence(
                logic_group_id=group_id,
                operator=operator,
                outcome=result,
                criterion_ids=[item.criterion_id for item in group_criteria],
                explanation=(
                    f"The explicitly declared {operator} group evaluates to "
                    f"{result}; individual cited clause outcomes remain available."
                ),
            )
        )
    return summaries


def _criterion_sources(
    criteria: Sequence[EligibilityCriterion],
    sources_by_id: dict[str, SourceReference],
) -> list[SourceReference]:
    """Return each cited source once in the catalog's stable order."""
    cited_ids = {criterion.source_id for criterion in criteria}
    return [
        source for source in sources_by_id.values() if source.source_id in cited_ids
    ]


def _location_applies(
    known_facts: dict[str, object],
    jurisdiction: Jurisdiction,
) -> bool | None:
    """Return false only for a known conflicting state or local jurisdiction."""
    if jurisdiction.level == "federal":
        return True
    state_code = known_facts.get("state_code")
    if not isinstance(state_code, str):
        return None
    if state_code != jurisdiction.state_code:
        return False
    if jurisdiction.level != "local":
        return True
    county_name = known_facts.get("county_name")
    if not isinstance(county_name, str):
        return None
    return same_county(county_name, jurisdiction.county_name)


def _location_gap_attribute(
    known_facts: dict[str, object],
    jurisdiction: Jurisdiction,
) -> str:
    """Name the minimum missing location fact needed to apply the program rule."""
    state_code = known_facts.get("state_code")
    if not isinstance(state_code, str):
        return "state_code"
    if jurisdiction.level == "local":
        return "county_name"
    return "state_code"


def _criterion_matches(
    actual: object,
    criterion: EligibilityCriterion,
    known_facts: dict[str, object],
) -> bool | None:
    """Apply one supported operator after validating its value domain."""
    operator = criterion.operator
    expected = criterion.value
    if operator == "lte_by":
        if not isinstance(expected, dict) or criterion.context_attribute is None:
            raise UnsupportedCriterionError(
                "lte_by requires a threshold table and context attribute."
            )
        context_value = known_facts.get(criterion.context_attribute)
        if context_value is None:
            return None
        if (
            not isinstance(context_value, int)
            or isinstance(context_value, bool)
            or context_value < 1
        ):
            raise UnsupportedCriterionError(
                "lte_by requires a positive integer threshold context value."
            )
        threshold = expected.get(str(context_value))
        if threshold is None:
            return None
        if (
            not _is_number(actual)
            or isinstance(threshold, bool)
            or not _is_number(threshold)
        ):
            raise UnsupportedCriterionError(
                "lte_by requires numeric profile and threshold values."
            )
        return actual <= threshold
    if operator == "exists":
        if not isinstance(expected, bool):
            raise UnsupportedCriterionError("exists requires a boolean value.")
        return expected
    if operator == "one_of":
        if not isinstance(expected, list):
            raise UnsupportedCriterionError("one_of requires a list value.")
        if criterion.attribute == "age_band" and (
            actual == "unknown" or _is_age_band_comparison(actual, expected)
        ):
            return _age_band_membership(actual, expected)
        actual_values = actual if isinstance(actual, list) else [actual]
        if not all(
            _compatible(value, allowed)
            for value in actual_values
            for allowed in expected
        ):
            if actual_values and expected:
                raise UnsupportedCriterionError(
                    "one_of values have incompatible types."
                )
        return any(
            _equal(value, allowed) for value in actual_values for allowed in expected
        )
    if isinstance(expected, list) or not _compatible(actual, expected):
        raise UnsupportedCriterionError(
            "profile and criterion value types are incompatible."
        )
    if operator == "eq":
        return _equal(actual, expected)
    if operator == "ne":
        return not _equal(actual, expected)
    if operator in {"gt", "gte", "lt", "lte"}:
        if not _is_number(actual) or not _is_number(expected):
            raise UnsupportedCriterionError("numeric operators require numeric values.")
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "lt":
            return actual < expected
        return actual <= expected
    raise UnsupportedCriterionError(f"operator {operator!r} is not supported.")


def _unknown_criterion_explanation(criterion: EligibilityCriterion) -> str:
    """Explain which supporting profile fact prevented a comparison."""
    if criterion.operator == "lte_by":
        return (
            "The income or household-size fact is missing, ambiguous, or outside "
            "the source threshold table, so this criterion remains unknown."
        )
    if criterion.attribute == "age_band":
        return (
            "The profile age information is too coarse or unknown to classify "
            "this published criterion."
        )
    return "A required profile fact is unknown, so this criterion cannot be compared."


AGE_BAND_RANGES: dict[str, tuple[int, int | None]] = {
    "under_60": (0, 59),
    "60_64": (60, 64),
    "65_74": (65, 74),
    "75_plus": (75, None),
    "65_plus": (65, None),
    "adult": (18, None),
}


def _is_age_band_comparison(actual: object, expected: list[object]) -> bool:
    return (
        isinstance(actual, str)
        and actual in AGE_BAND_RANGES
        and bool(expected)
        and all(
            isinstance(value, str) and value in AGE_BAND_RANGES for value in expected
        )
    )


def _age_band_membership(actual: object, expected: list[object]) -> bool | None:
    """Compare age ranges conservatively when a profile band is coarse."""
    if not isinstance(actual, str) or not _is_age_band_comparison(actual, expected):
        return None

    actual_start, actual_end = AGE_BAND_RANGES[actual]
    accepted = sorted(
        (AGE_BAND_RANGES[value] for value in set(expected)),
        key=lambda bounds: (
            bounds[0],
            float("inf") if bounds[1] is None else bounds[1],
        ),
    )
    merged: list[tuple[int, int | None]] = []
    for start, end in accepted:
        if not merged:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        if previous_end is None or start <= previous_end + 1:
            merged[-1] = (
                previous_start,
                None if previous_end is None or end is None else max(previous_end, end),
            )
        else:
            merged.append((start, end))
    if any(
        start <= actual_start
        and (end is None or (actual_end is not None and end >= actual_end))
        for start, end in merged
    ):
        return True
    intersects = any(
        (end is None or end >= actual_start)
        and (actual_end is None or start <= actual_end)
        for start, end in merged
    )
    return None if intersects else False


def _compatible(left: object, right: object) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool)
    if _is_number(left) or _is_number(right):
        return _is_number(left) and _is_number(right)
    return type(left) is type(right)


def _equal(left: object, right: object) -> bool:
    return _compatible(left, right) and left == right


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
