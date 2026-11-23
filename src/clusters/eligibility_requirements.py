"""Versioned, source-grounded Eligibility requirements packages."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from clusters.eligibility_contracts import (
    ProgramDetailKind,
    ProgramResultEntry,
    SourceBackedProgramDetail,
    SourceHealth,
    StrictModel,
)

PACKAGE_NAMESPACE = uuid.UUID("384e35d8-99fd-4e74-a4b4-c9c6bc542ecf")


class RequirementsPackage(StrictModel):
    """User-specific report input with normalized profile and source evidence."""

    contract_version: Literal["eligibility-requirements-package-v2"] = (
        "eligibility-requirements-package-v2"
    )
    package_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    tenant_id: str = Field(min_length=1)
    cluster_id: str = Field(min_length=1)
    pattern_version: str = Field(min_length=1)
    program_id: str = Field(min_length=1)
    program_title: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    required_documents: list[SourceBackedProgramDetail]
    forms: list[SourceBackedProgramDetail]
    deadlines: list[SourceBackedProgramDetail]
    procedural_steps: list[SourceBackedProgramDetail]
    unavailable_detail_kinds: list[ProgramDetailKind]
    source_references: list[dict[str, object]]
    source_health: list[SourceHealth]
    source_limitations: list[str]
    profile_snapshot: dict[str, object]
    evaluation: dict[str, object]
    created_at: datetime
    official_eligibility_determination: Literal[False] = False
    official_application_submitted: Literal[False] = False


def build_requirements_package(
    entry: ProgramResultEntry,
    *,
    tenant_id: str,
    cluster_id: str,
    pattern_version: str,
    profile_snapshot: dict[str, object],
    evaluation: dict[str, object],
) -> RequirementsPackage:
    """Build deterministic packet contents from one current program result."""
    if entry.status != "current":
        raise ValueError("Only current source-backed program results can be packaged.")
    if not entry.source_references:
        raise ValueError("A requirements package needs source provenance.")
    details = entry.details
    by_kind = {
        "required_documents": "required_document",
        "forms": "form",
        "deadlines": "deadline",
        "procedural_steps": "application_step",
    }
    grouped = {
        output: [item for item in details if item.kind == kind]
        for output, kind in by_kind.items()
    }
    body = {
        "contract_version": "eligibility-requirements-package-v2",
        "tenant_id": tenant_id,
        "cluster_id": cluster_id,
        "pattern_version": pattern_version,
        "program_id": entry.program_id,
        "program_title": entry.title,
        "catalog_version": entry.catalog_version,
        **{
            key: [item.model_dump(mode="json") for item in value]
            for key, value in grouped.items()
        },
        "unavailable_detail_kinds": entry.unavailable_detail_kinds,
        "source_references": [
            item.model_dump(mode="json") for item in entry.source_references
        ],
        "source_health": [item.model_dump(mode="json") for item in entry.source_health],
        "source_limitations": entry.source_limitations,
        "profile_snapshot": profile_snapshot,
        "evaluation": evaluation,
        "official_eligibility_determination": False,
        "official_application_submitted": False,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    package_id = str(
        uuid.uuid5(PACKAGE_NAMESPACE, hashlib.sha256(canonical.encode()).hexdigest())
    )
    return RequirementsPackage(
        package_id=package_id,
        tenant_id=tenant_id,
        cluster_id=cluster_id,
        pattern_version=pattern_version,
        program_id=entry.program_id,
        program_title=entry.title,
        catalog_version=entry.catalog_version,
        **grouped,
        unavailable_detail_kinds=entry.unavailable_detail_kinds,
        source_references=body["source_references"],
        source_health=entry.source_health,
        source_limitations=entry.source_limitations,
        profile_snapshot=profile_snapshot,
        evaluation=evaluation,
        created_at=datetime.now(timezone.utc),
    )


_PROFILE_LABELS = {
    "state_code": "State",
    "county_name": "County",
    "household_size": "Household size",
    "household_adults": "Adults in household",
    "household_children": "Children in household",
    "annual_household_income_usd": "Annual household income",
    "employment_status": "Employment",
    "housing_status": "Housing",
    "health_coverage_status": "Health coverage",
    "food_assistance_need": "Needs help with food",
    "childcare_need": "Needs child care",
    "utility_assistance_need": "Needs help with utilities",
}
_PROFILE_HIDDEN = {"navigator", "attributes", "profile_id", "schema_version"}


def _profile_value(key: str, value: object) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if key.endswith("_usd") and isinstance(value, (int, float)):
        return f"${value:,.0f}"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item).replace("_", " ") for item in value)
    if isinstance(value, str):
        return value.replace("_", " ")
    return str(value)


def _profile_lines(profile: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for key, value in profile.items():
        if value is None or key in _PROFILE_HIDDEN:
            continue
        if isinstance(value, (dict, list, tuple)) and not value:
            continue
        label = _PROFILE_LABELS.get(key, key.replace("_", " ").capitalize())
        lines.append(f"- **{label}:** {_profile_value(key, value)}")
    return lines


def _plain_labels(evaluation: dict[str, object]) -> dict[str, str]:
    """Map internal criterion/requirement IDs to short plain-word labels."""
    ids: list[str] = []

    def add(rows: object, *keys: str) -> None:
        if isinstance(rows, list):
            for item in rows:
                if isinstance(item, dict):
                    for key in keys:
                        if item.get(key):
                            ids.append(str(item[key]))
                            break

    add(evaluation.get("criteria_evidence"), "criterion_id", "criteria_program_id")
    linked = evaluation.get("criteria_evaluations")
    if isinstance(linked, list):
        for entry in linked:
            if isinstance(entry, dict):
                add(
                    entry.get("criteria_evidence", entry.get("evidence")),
                    "criterion_id",
                )
                add(entry.get("unresolved_requirements"), "requirement_id")
    split = {item: item.replace("_", "-").split("-") for item in set(ids)}
    prefix = 0
    if len(split) > 1:
        shortest = min(len(tokens) for tokens in split.values())
        while (
            prefix < shortest - 1
            and len({tokens[prefix] for tokens in split.values()}) == 1
        ):
            prefix += 1
    labels: dict[str, str] = {}
    for item, tokens in split.items():
        words = " ".join(tokens[prefix:]).strip()
        labels[item] = (words[:1].upper() + words[1:]) if words else item
    return labels


def _screening_items(
    evaluation: dict[str, object],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return (criterion, outcome) pairs and unresolved (requirement, text) pairs."""
    outcomes: list[tuple[str, str]] = []
    unresolved: list[tuple[str, str]] = []

    def take_evidence(rows: object) -> None:
        if isinstance(rows, list):
            for item in rows:
                if isinstance(item, dict):
                    outcomes.append(
                        (
                            str(item.get("criterion_id", "Criterion")),
                            str(item.get("outcome", item.get("status", "unknown"))),
                        )
                    )

    take_evidence(evaluation.get("criteria_evidence"))
    linked = evaluation.get("criteria_evaluations")
    if isinstance(linked, list):
        for entry in linked:
            if not isinstance(entry, dict):
                continue
            take_evidence(entry.get("criteria_evidence", entry.get("evidence")))
            rows = entry.get("unresolved_requirements")
            if isinstance(rows, list):
                for item in rows:
                    if isinstance(item, dict):
                        explanation = str(item.get("explanation", ""))
                        if "not an eligibility predicate" in explanation:
                            continue  # guidance text, not a requirement to check
                        unresolved.append(
                            (
                                str(item.get("requirement_id", "Requirement")),
                                explanation,
                            )
                        )
    for criterion, outcome in outcomes:
        if outcome in ("unknown", "partial"):
            unresolved.append((criterion, ""))
    return outcomes, unresolved


def _guidance_sections(package: dict[str, object], evaluation: dict[str, object]):
    outcomes, unresolved = _screening_items(evaluation)
    labels = _plain_labels(evaluation)
    lines: list[str] = ["## Screening summary", ""]
    if outcomes:
        counts: dict[str, int] = {}
        for _, outcome in outcomes:
            counts[outcome] = counts.get(outcome, 0) + 1
        lines.append(
            "Outcomes from the checked criteria: "
            + ", ".join(f"{n} {k}" for k, n in sorted(counts.items()))
            + "."
        )
    else:
        lines.append("No criteria were evaluable, so no screening outcome is shown.")
    lines.extend(["", "## What to prepare", ""])
    documents = package.get("required_documents", [])
    prepared = False
    if isinstance(documents, list):
        for row in documents:
            if isinstance(row, dict) and row.get("text"):
                lines.append(f"- {row['text']} (from the program source)")
                prepared = True
    for requirement, explanation in unresolved:
        requirement = labels.get(requirement, requirement)
        if "mapping gap" in explanation:
            lines.append(
                f"- Be ready to discuss {requirement}: this service could not "
                "check it automatically, so the program office will confirm it"
            )
        else:
            detail = f" — {explanation}" if explanation else ""
            lines.append(
                f"- Information still needed for {requirement}{detail} "
                "(still unknown in your profile)"
            )
        prepared = True
    if not prepared:
        lines.append("No preparation items are published in the checked source.")
    lines.extend(
        [
            "",
            "## Questions to ask",
            "",
            "General guidance, not taken from the program source:",
        ]
    )
    for requirement, _ in unresolved:
        requirement = labels.get(requirement, requirement)
        lines.append(
            f"- How does the program office confirm {requirement} for my situation?"
        )
    lines.append("- Where and how do I submit an application?")
    if not package.get("deadlines"):
        lines.append("- Is there a current application deadline?")
    lines.append("")
    return lines


def render_requirements_report(package: dict[str, object]) -> str:
    """Render a user-ready Markdown report from the persisted package JSON."""
    profile = package.get("profile_snapshot", {})
    evaluation = package.get("evaluation", {})
    if not isinstance(profile, dict) or not isinstance(evaluation, dict):
        raise ValueError("Requirements package is missing report inputs.")
    source_urls = {
        item.get("source_id"): item.get("url")
        for item in package.get("source_references", [])
        if isinstance(item, dict)
    }

    def section(title: str, key: str) -> list[str]:
        lines = [f"## {title}", ""]
        rows = package.get(key, [])
        if not isinstance(rows, list) or not rows:
            return [*lines, "None published in the checked source.", ""]
        for row in rows:
            if isinstance(row, dict):
                source_id = row.get("source_id", "")
                source_url = source_urls.get(source_id)
                source = (
                    f"[{source_id}](<{source_url}>)"
                    if isinstance(source_url, str) and source_url
                    else str(source_id)
                )
                lines.append(f"- {row.get('text', '')} (source: {source})")
        return [*lines, ""]

    program_title = package.get("program_title", package.get("program_id", "Program"))
    lines = [
        f"# Requirements report: {program_title}",
        "",
        f"Package ID: `{package.get('package_id', '')}`",
        "",
        "## Your profile used",
        "",
    ]
    lines.extend(_profile_lines(profile))
    lines.extend(["", "## Evaluation", ""])
    evidence = evaluation.get("criteria_evidence", [])
    linked_evaluations = evaluation.get("criteria_evaluations", [])
    if isinstance(evidence, list) and evidence:
        for item in evidence:
            if isinstance(item, dict):
                outcome = item.get("outcome", item.get("status", "unknown"))
                criterion = item.get(
                    "criterion_id", item.get("criteria_program_id", "Criterion")
                )
                explanation = item.get("explanation", "")
                lines.append(f"- **{criterion}:** {outcome} — {explanation}")
    elif isinstance(linked_evaluations, list) and linked_evaluations:
        rendered_linked_evidence = False
        for linked in linked_evaluations:
            if not isinstance(linked, dict):
                continue
            criteria_program_id = linked.get("criteria_program_id", "Criteria")
            linked_evidence = linked.get(
                "criteria_evidence", linked.get("evidence", [])
            )
            if isinstance(linked_evidence, list):
                for item in linked_evidence:
                    if isinstance(item, dict):
                        criterion = item.get("criterion_id", "Criterion")
                        outcome = item.get("outcome", "unknown")
                        explanation = item.get("explanation", "")
                        lines.append(
                            f"- **{criterion} ({criteria_program_id}):** "
                            f"{outcome} — {explanation}"
                        )
                        rendered_linked_evidence = True
            unresolved = linked.get("unresolved_requirements", [])
            if isinstance(unresolved, list):
                for item in unresolved:
                    if isinstance(item, dict):
                        requirement = item.get("requirement_id", "Requirement")
                        reason = item.get("reason_code", "evidence_incomplete")
                        explanation = item.get("explanation", "")
                        label = f"Unresolved — {requirement} ({criteria_program_id})"
                        lines.append(f"- **{label}:** {reason} — {explanation}")
                        rendered_linked_evidence = True
        if not rendered_linked_evidence:
            lines.append("No evaluable criteria were available.")
    else:
        comparison = evaluation.get("comparison")
        explanation = (
            comparison.get("explanation", "No evaluable criteria were available.")
            if isinstance(comparison, dict)
            else "No evaluable criteria were available."
        )
        lines.append(f"- {explanation}")
    lines.extend(
        [
            "",
            "This is an informational comparison, not an official eligibility "
            "determination or an application submission.",
            "",
        ]
    )
    lines.extend(_guidance_sections(package, evaluation))
    lines.extend(section("Required documents", "required_documents"))
    lines.extend(section("Forms", "forms"))
    lines.extend(section("Deadlines", "deadlines"))
    lines.extend(section("Application steps", "procedural_steps"))
    lines.extend(["## Sources and limitations", ""])
    sources = package.get("source_references", [])
    if isinstance(sources, list):
        for source in sources:
            if isinstance(source, dict):
                title = source.get("title", source.get("source_id", "Source"))
                url = source.get("url", "")
                lines.append(f"- {title}: {url}")
    limitations = package.get("source_limitations", [])
    if isinstance(limitations, list):
        lines.extend(f"- {item}" for item in limitations)
    return "\n".join(lines).rstrip() + "\n"
