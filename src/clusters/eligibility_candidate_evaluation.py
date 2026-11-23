"""Request-scoped evaluation and requirements previews for selected leads."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastmcp import FastMCP
from pydantic import Field, model_validator

from clusters.eligibility_candidate import (
    CandidateCitation,
    ProgramCriteriaPackageCompiler,
    ProgramCriteriaPackageRequest,
    RequirementCandidate,
)
from clusters.eligibility_contracts import (
    CriteriaMappingWorkflowAction,
    ProgramCriterionEvidence,
    ProgramDetailKind,
    ProgramLogicGroupEvidence,
    ProgramRecord,
    SourceReference,
    StrictModel,
)
from clusters.eligibility_discovery import ProfileSubmission
from clusters.eligibility_evaluation import (
    UnsupportedCriterionError,
    evaluate_program_criteria,
)
from clusters.eligibility_profile_projection import (
    synthetic_profile_id_from_reference_hash,
)
from clusters.eligibility_requirements import (
    PACKAGE_NAMESPACE,
    RequirementsPackage,
    render_requirements_report,
)

DETAIL_OUTPUTS = {
    "application_step": "procedural_steps",
    "form": "forms",
    "required_document": "required_documents",
    "deadline": "deadlines",
}


class RequestScopedCandidateCriteriaPackage(ProgramCriteriaPackageRequest):
    """Typed criteria mapping for a candidate with no shared-catalog version."""

    catalog_version: Literal["request-scoped"] = "request-scoped"


def as_request_scoped_candidate_package(
    criteria_package: ProgramCriteriaPackageRequest,
) -> RequestScopedCandidateCriteriaPackage:
    """Adapt a mapped package to the request-scoped evaluation contract.

    The source snapshot's catalog version is useful provenance for persistence,
    but candidate evaluation is deliberately independent of shared catalog
    versioning.
    """
    payload = criteria_package.model_dump(mode="python")
    payload["catalog_version"] = "request-scoped"
    return RequestScopedCandidateCriteriaPackage.model_validate(payload)


class ProgramCandidateEvaluationRequest(StrictModel):
    """Evaluate an explicitly mapped source candidate against synthetic facts."""

    criteria_package: RequestScopedCandidateCriteriaPackage
    profile_submission: ProfileSubmission


class RequirementsDetailCitation(CandidateCitation):
    """Citation fields required to append a verified package detail."""

    source_id: str = Field(min_length=2, max_length=80)
    source_version: str = Field(min_length=1, max_length=500)
    source_content_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    locator: str = Field(min_length=1, max_length=300)
    excerpt: str = Field(min_length=1, max_length=500)


class RequestScopedProgramDetail(StrictModel):
    """One requirements detail with a precise citation from the selected source."""

    kind: ProgramDetailKind
    text: str = Field(min_length=1)
    citation: RequirementsDetailCitation

    @model_validator(mode="after")
    def require_precise_source_locator(self) -> RequestScopedProgramDetail:
        """Require source id, version, hash and locator on each detail."""
        if not all(
            (
                self.citation.source_id,
                self.citation.source_version,
                self.citation.source_content_sha256,
                self.citation.locator,
                self.citation.excerpt,
            )
        ):
            raise ValueError(
                "Requirements details need source ID, version, hash, locator, "
                "and excerpt."
            )
        return self


class ProgramCandidateRequirementsPreviewRequest(StrictModel):
    """Build a non-persistent client-side packet from one selected public source."""

    criteria_package: RequestScopedCandidateCriteriaPackage
    details: list[RequestScopedProgramDetail] = Field(max_length=100)
    profile_id: str | None = Field(
        default=None,
        pattern=r"^synthetic-[a-z0-9][a-z0-9-]{2,63}$",
    )

    @model_validator(mode="after")
    def details_use_declared_sources(
        self,
    ) -> ProgramCandidateRequirementsPreviewRequest:
        """Require each detail to cite a supplied source with matching lineage."""
        source_by_id = {
            source.source_id: source for source in self.criteria_package.sources
        }
        for detail in self.details:
            citation = detail.citation
            source = source_by_id.get(citation.source_id or "")
            if source is None:
                raise ValueError("Each packet detail must cite a supplied source.")
            if (
                source.url != citation.source_url
                or source.retrieved_at != citation.retrieved_at
                or source.publisher_version != citation.source_version
            ):
                raise ValueError(
                    "Packet detail citation does not match supplied source lineage."
                )
        return self


class ProgramCandidateRequirementsWriteRequest(
    ProgramCandidateRequirementsPreviewRequest
):
    """Save a selected-program packet with its deterministic profile evaluation."""

    profile_submission: ProfileSubmission

    @model_validator(mode="after")
    def profile_matches_program_and_reference(
        self,
    ) -> ProgramCandidateRequirementsWriteRequest:
        """Require the package profile reference to match the scored profile."""
        if (
            self.profile_id is not None
            and self.profile_id != self.profile_submission.profile.profile_id
        ):
            raise ValueError(
                "Package profile reference does not match the scored profile."
            )
        return self


class RequirementsPackageListRequest(StrictModel):
    """List saved packages scoped by a profile ID or Core-resolved profile."""

    profile_id: str | None = Field(
        default=None, pattern=r"^(?:demo|synthetic)-[a-z0-9][a-z0-9-]{0,62}$"
    )
    shared_profile: dict[str, object] | None = None
    limit: int = Field(default=25, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def require_one_profile_scope(self) -> RequirementsPackageListRequest:
        """Require exactly one valid profile scope."""
        if (self.profile_id is None) == (self.shared_profile is None):
            raise ValueError("Supply exactly one profile scope.")
        if self.profile_id is None and _shared_profile_id(self.shared_profile) is None:
            raise ValueError("The resolved profile scope is invalid.")
        return self


class RequirementsPackageReadRequest(StrictModel):
    """Read one saved package only within its tenant and profile scope."""

    package_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    profile_id: str | None = Field(
        default=None, pattern=r"^(?:demo|synthetic)-[a-z0-9][a-z0-9-]{0,62}$"
    )
    shared_profile: dict[str, object] | None = None

    @model_validator(mode="after")
    def require_one_profile_scope(self) -> RequirementsPackageReadRequest:
        """Require exactly one valid profile scope."""
        if (self.profile_id is None) == (self.shared_profile is None):
            raise ValueError("Supply exactly one profile scope.")
        if self.profile_id is None and _shared_profile_id(self.shared_profile) is None:
            raise ValueError("The resolved profile scope is invalid.")
        return self


class RequirementsPackageEnrichmentRequest(StrictModel):
    """Add source-backed binder details to an existing saved package revision."""

    package_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    expected_revision: int = Field(ge=1)
    details: list[RequestScopedProgramDetail] = Field(min_length=1, max_length=100)
    profile_id: str | None = Field(
        default=None, pattern=r"^(?:demo|synthetic)-[a-z0-9][a-z0-9-]{0,62}$"
    )
    shared_profile: dict[str, object] | None = None

    @model_validator(mode="after")
    def require_one_profile_scope(self) -> RequirementsPackageEnrichmentRequest:
        """Require exactly one valid profile scope."""
        if (self.profile_id is None) == (self.shared_profile is None):
            raise ValueError("Supply exactly one profile scope.")
        if self.profile_id is None and _shared_profile_id(self.shared_profile) is None:
            raise ValueError("The resolved profile scope is invalid.")
        return self


def _shared_profile_id(shared_profile: object) -> str | None:
    """Derive the same tenant-local scope ID used by profile projection."""
    if (
        not isinstance(shared_profile, dict)
        or shared_profile.get("status") != "available"
    ):
        return None
    return synthetic_profile_id_from_reference_hash(
        shared_profile.get("profile_ref_sha256")
    )


def evaluate_request_scoped_candidate(
    request: ProgramCandidateEvaluationRequest,
) -> dict[str, object]:
    """Evaluate only mapped clauses and preserve every unresolved clause as unknown."""
    compiler = ProgramCriteriaPackageCompiler()
    try:
        prepared = compiler.compile(request.criteria_package)
        record_payload = prepared.get("program_record_preview")
        if not isinstance(record_payload, dict):
            return _no_mapped_criteria_result(
                prepared,
                request.criteria_package.sources,
                request.criteria_package.program_id,
            )
        program = ProgramRecord.model_validate(record_payload)
        result = evaluate_program_criteria(request.profile_submission, program)
    except (TypeError, ValueError, UnsupportedCriterionError):
        return {
            "status": "invalid_candidate",
            "evaluation_scope": "request_scoped",
            "program_id": request.criteria_package.program_id,
            "evaluation_performed": False,
            "error": (
                "The selected candidate or mapped criteria do not meet "
                "the active contract."
            ),
            **_no_write_effects(),
        }

    diagnostics = _compiler_diagnostics_by_requirement(prepared)
    unresolved = [
        _unresolved_evidence(requirement, diagnostics.get(requirement.requirement_id))
        for requirement in request.criteria_package.requirements
        if requirement.requirement_id in diagnostics
        or requirement.mapping_status != "mapped"
        or requirement.requirement_type != "eligibility"
    ]
    mapped_fraction, counts = _mapped_rule_fraction(
        program, result.evidence, result.logic_groups
    )
    mapped_count = len(program.criteria)
    unresolved_count = len(unresolved)
    unresolved_rule_count = sum(
        requirement.requirement_type == "eligibility"
        and (
            requirement.mapping_status != "mapped"
            or requirement.requirement_id in diagnostics
        )
        for requirement in request.criteria_package.requirements
    )
    return {
        "status": "ok",
        "evaluation_scope": "request_scoped_candidate",
        "criteria_package_id": prepared["criteria_package_id"],
        "program_id": program.program_id,
        "title": program.title,
        "navigator": program.navigator,
        "profile_id": result.profile_id,
        "profile_schema_version": result.profile_schema_version,
        "criteria_status": (
            "partial" if unresolved_rule_count else "mapped_for_candidate"
        ),
        "evaluation_performed": mapped_count > 0,
        "criteria_evidence": [item.model_dump(mode="json") for item in result.evidence],
        "logic_groups": [item.model_dump(mode="json") for item in result.logic_groups],
        "unresolved_requirements": unresolved,
        "coverage": {
            "source_completeness": "not_proven",
            "mapped_requirement_count": mapped_count,
            "unresolved_requirement_count": unresolved_count,
            "unresolved_eligibility_rule_count": unresolved_rule_count,
            "mapped_rule_match_fraction": mapped_fraction,
            "mapped_rule_counts": counts,
            "score_meaning": (
                "Fraction of evaluable mapped rule units marked met; not an "
                "eligibility probability. Unresolved clauses are excluded."
            ),
        },
        "source_references": [item.model_dump(mode="json") for item in program.sources],
        "source_limitations": list(program.source_limitations),
        "source_validation": {
            "status": "caller_supplied_lineage_consistency_checked",
            "independently_revalidated": False,
        },
        "official_eligibility_determination": False,
        **_no_write_effects(),
    }


def preview_request_scoped_requirements(
    request: ProgramCandidateRequirementsPreviewRequest,
) -> dict[str, object]:
    """Build a cited JSON packet without requiring a shared-catalog record."""
    prepared = ProgramCriteriaPackageCompiler().compile(request.criteria_package)
    details_by_kind = {
        output_name: [
            item.model_dump(mode="json")
            for item in request.details
            if item.kind == kind
        ]
        for kind, output_name in DETAIL_OUTPUTS.items()
    }
    present = {item.kind for item in request.details}
    diagnostics = _compiler_diagnostics_by_requirement(prepared)
    unresolved = [
        _unresolved_evidence(requirement, diagnostics.get(requirement.requirement_id))
        for requirement in request.criteria_package.requirements
        if requirement.requirement_id in diagnostics
        or requirement.mapping_status != "mapped"
        or requirement.requirement_type != "eligibility"
    ]
    body = {
        "program_id": request.criteria_package.program_id,
        "criteria_package_id": prepared["criteria_package_id"],
        "navigator": request.criteria_package.navigator,
        "title": request.criteria_package.title,
        "description": request.criteria_package.description,
        "jurisdiction": request.criteria_package.jurisdiction.model_dump(mode="json"),
        "profile_id": request.profile_id,
        "requirements": details_by_kind,
        "criteria": {
            "compiled_rules": prepared["compiled_rules"],
            "unresolved_requirements": unresolved,
            "active_rule_count": 0,
        },
        "source_references": [
            item.model_dump(mode="json") for item in request.criteria_package.sources
        ],
        "source_limitations": list(request.criteria_package.source_limitations),
        "unavailable_detail_kinds": [
            kind for kind in DETAIL_OUTPUTS if kind not in present
        ],
        "source_completeness": "not_proven",
        "source_validation": {
            "status": "caller_supplied_lineage_consistency_checked",
            "independently_revalidated": False,
        },
    }
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    preview_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "contract_version": "request-scoped-requirements-preview-v1",
        "status": "preview",
        "preview_id": preview_id,
        "request_scoped": True,
        "written": False,
        "package": body,
        **_no_write_effects(),
    }


def register_request_scoped_candidate_tools(
    app: FastMCP,
    *,
    catalog_repository: object | None = None,
    tenant_id: str = "",
    cluster_id: str = "",
    pattern_version: str = "",
    register_package_writer: bool = True,
) -> None:
    """Expose evaluation that returns an actionable handoff instead of auto-mapping."""

    @app.tool(
        name="evaluate_program_candidate",
        annotations={
            "title": "Evaluate selected program candidate",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def evaluate_program_candidate_tool(
        criteria_package: RequestScopedCandidateCriteriaPackage,
        profile_submission: ProfileSubmission,
    ) -> dict[str, object]:
        """Evaluate mapped clauses or return a client criteria-mapping workflow.

        Missing rules are never mapped inline during scoring. The caller can use
        the published source-backed workflow and explicitly rescore after the
        mapping persistence operation confirms readback.
        """
        request = ProgramCandidateEvaluationRequest(
            criteria_package=criteria_package,
            profile_submission=profile_submission,
        )
        return evaluate_request_scoped_candidate(request)

    @app.tool(name="preview_program_candidate_requirements")
    def preview_program_candidate_requirements_tool(
        criteria_package: RequestScopedCandidateCriteriaPackage,
        details: list[RequestScopedProgramDetail],
        profile_id: str | None = None,
    ) -> dict[str, object]:
        """Return a cited preview from supplied source lineage and details.

        Core checks citation consistency but does not independently fetch the
        source. The preview is request-scoped and non-persistent.
        """
        request = ProgramCandidateRequirementsPreviewRequest(
            criteria_package=criteria_package,
            details=details,
            profile_id=profile_id,
        )
        try:
            return preview_request_scoped_requirements(request)
        except (TypeError, ValueError):
            return {
                "status": "invalid_candidate",
                "request_scoped": True,
                "written": False,
                "error": (
                    "The selected candidate or cited requirements do not meet "
                    "the active contract."
                ),
                **_no_write_effects(),
            }

    if not register_package_writer:
        return

    @app.tool(
        name="list_saved_requirements_packages",
        annotations={
            "title": "List Saved Requirements Packages",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def list_saved_requirements_packages_tool(
        profile_id: str | None = None,
        shared_profile: dict[str, object] | None = None,
        limit: int = 25,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """List saved package summaries for the current synthetic profile.

        Use the profile ID returned by the profile service or the same synthetic
        profile used in the screening request. The response contains no profile
        values; use the package ID with the read operation to retrieve the report
        and current revision before requesting an enrichment.
        """
        listing = getattr(catalog_repository, "list_requirements_packages", None)
        if not callable(listing):
            return {
                "status": "unavailable",
                "reason_code": "requirements_package_store_not_configured",
            }
        try:
            request = RequirementsPackageListRequest(
                profile_id=profile_id,
                shared_profile=shared_profile,
                limit=limit,
                cursor=cursor,
            )
            scoped_profile_id = request.profile_id or _shared_profile_id(
                request.shared_profile
            )
            if scoped_profile_id is None:
                return {
                    "status": "invalid_request",
                    "reason_code": "invalid_package_query",
                }
            result = listing(
                tenant_id,
                scoped_profile_id,
                limit=request.limit,
                cursor=request.cursor,
            )
            return result
        except (TypeError, ValueError):
            return {"status": "invalid_request", "reason_code": "invalid_package_query"}
        except RuntimeError:
            return {"status": "unavailable", "reason_code": "package_store_unavailable"}

    @app.tool(
        name="read_saved_requirements_package",
        annotations={
            "title": "Read Saved Requirements Package",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    def read_saved_requirements_package_tool(
        package_id: str,
        profile_id: str | None = None,
        shared_profile: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Retrieve a saved package and its Markdown report for client download.

        The package ID must belong to the supplied synthetic profile and this
        tenant. The response includes the current revision for a later
        compare-and-set enrichment. The Markdown is a user-ready document with
        source citations.
        """
        state_reader = getattr(
            catalog_repository, "read_scoped_requirements_package_state", None
        )
        if not callable(state_reader):
            return {
                "status": "unavailable",
                "reason_code": "requirements_package_store_not_configured",
            }
        try:
            request = RequirementsPackageReadRequest(
                package_id=package_id,
                profile_id=profile_id,
                shared_profile=shared_profile,
            )
            scoped_profile_id = request.profile_id or _shared_profile_id(
                request.shared_profile
            )
            if scoped_profile_id is None:
                return {
                    "status": "invalid_request",
                    "reason_code": "invalid_package_query",
                }
            package, revision = state_reader(
                request.package_id, tenant_id, scoped_profile_id
            )
            if not isinstance(package, dict):
                return {"status": "package_not_found"}
            if package.get("tenant_id") != tenant_id:
                return {"status": "package_not_found"}
            profile = package.get("profile_snapshot")
            if (
                not isinstance(profile, dict)
                or profile.get("profile_id") != scoped_profile_id
            ):
                return {"status": "package_not_found"}
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 1
            ):
                return {
                    "status": "unavailable",
                    "reason_code": "invalid_package_revision",
                }
            title = str(package.get("program_title", "requirements"))
            filename = re.sub(r"[^a-z0-9-]+", "-", title.casefold()).strip("-")
            filename = f"{filename or 'program'}-requirements.md"
            return {
                "status": "available",
                "package_id": request.package_id,
                "program_id": package.get("program_id"),
                "program_title": title,
                "created_at": package.get("created_at"),
                "revision": revision,
                "package": package,
                "report_markdown": render_requirements_report(package),
                "document_filename": filename,
                "document_content_type": "text/markdown; charset=utf-8",
            }
        except (TypeError, ValueError):
            return {"status": "invalid_request", "reason_code": "invalid_package_query"}
        except RuntimeError:
            return {"status": "unavailable", "reason_code": "package_store_unavailable"}

    @app.tool(
        name="enrich_saved_requirements_package",
        annotations={
            "title": "Add Cited Details to a Saved Requirements Package",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def enrich_saved_requirements_package_tool(
        package_id: str,
        expected_revision: int,
        details: list[RequestScopedProgramDetail],
        profile_id: str | None = None,
        shared_profile: dict[str, object] | None = None,
    ) -> dict[str, object]:
        """Append source-cited binder details to a package using compare-and-set.

        The package must belong to the supplied synthetic profile and this
        tenant. A stale revision is reported without changing the package.
        """
        reader = getattr(
            catalog_repository, "read_scoped_requirements_package_state", None
        )
        enricher = getattr(catalog_repository, "enrich_requirements_package", None)
        if not callable(reader) or not callable(enricher):
            return {
                "status": "unavailable",
                "reason_code": "requirements_package_enrichment_not_configured",
            }
        try:
            request = RequirementsPackageEnrichmentRequest(
                package_id=package_id,
                expected_revision=expected_revision,
                details=details,
                profile_id=profile_id,
                shared_profile=shared_profile,
            )
            scoped_profile_id = request.profile_id or _shared_profile_id(
                request.shared_profile
            )
            if scoped_profile_id is None:
                return {
                    "status": "invalid_request",
                    "reason_code": "invalid_package_query",
                }
            current, _current_revision = await asyncio.to_thread(
                reader, request.package_id, tenant_id, scoped_profile_id
            )
            profile = (
                current.get("profile_snapshot") if isinstance(current, dict) else None
            )
            if (
                not isinstance(current, dict)
                or current.get("tenant_id") != tenant_id
                or not isinstance(profile, dict)
                or profile.get("profile_id") != scoped_profile_id
            ):
                return {"status": "package_not_found"}
            receipt = await asyncio.to_thread(
                enricher,
                request.package_id,
                tenant_id=tenant_id,
                profile_id=scoped_profile_id,
                expected_revision=request.expected_revision,
                details=[item.model_dump(mode="json") for item in request.details],
            )
            if (
                not isinstance(receipt, dict)
                or receipt.get("status") == "revision_conflict"
            ):
                return (
                    receipt
                    if isinstance(receipt, dict)
                    else {
                        "status": "unavailable",
                        "reason_code": "invalid_package_enrichment_receipt",
                    }
                )
            readback, readback_revision = await asyncio.to_thread(
                reader, request.package_id, tenant_id, scoped_profile_id
            )
            if readback_revision != receipt.get("revision") or readback != receipt.get(
                "package"
            ):
                return {
                    "status": "readback_failed",
                    "package_id": request.package_id,
                    "revision": receipt.get("revision"),
                    "readback_verified": False,
                    "next_action": "reread_package_and_reconcile_revision",
                }
            return {
                **receipt,
                "readback_verified": True,
                "report_markdown": render_requirements_report(readback),
            }
        except (TypeError, ValueError):
            return {
                "status": "invalid_request",
                "reason_code": "invalid_package_enrichment",
            }
        except RuntimeError:
            return {"status": "unavailable", "reason_code": "package_store_unavailable"}

    @app.tool(
        name="write_program_candidate_requirements",
        annotations={
            "title": "Save selected program requirements",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    def write_program_candidate_requirements_tool(
        criteria_package: RequestScopedCandidateCriteriaPackage,
        details: list[RequestScopedProgramDetail],
        profile_submission: ProfileSubmission,
        profile_id: str | None = None,
    ) -> dict[str, object]:
        """Persist and read back a scored, source-backed packet for a selected lead.

        The selected listing need not be published in the shared catalog. The
        request-scoped write stores a synthetic profile snapshot and deterministic
        evaluation with the cited requirements, using the existing package store.
        """
        writer = getattr(catalog_repository, "write_requirements_package", None)
        reader = getattr(catalog_repository, "read_requirements_package", None)
        if not callable(writer) or not callable(reader):
            return {
                "status": "unavailable",
                "reason_code": "requirements_package_store_not_configured",
                "written": False,
                "next_action": "configure_the_existing_package_store_for_this_runtime",
            }
        try:
            request = ProgramCandidateRequirementsWriteRequest(
                criteria_package=criteria_package,
                details=details,
                profile_submission=profile_submission,
                profile_id=profile_id,
            )
            preview = preview_request_scoped_requirements(request)
            body = preview["package"]
            evaluation = evaluate_request_scoped_candidate(
                ProgramCandidateEvaluationRequest(
                    criteria_package=criteria_package,
                    profile_submission=profile_submission,
                )
            )
            sources = criteria_package.sources
            source_refs = [
                {
                    "source_id": source.source_id,
                    "source_type": source.source_type,
                    "url": str(source.url),
                    "title": source.title,
                    "agency": source.agency,
                    "approval_status": source.approval_status,
                    "jurisdiction": source.jurisdiction.model_dump(mode="json"),
                    "publisher_version": source.publisher_version or "observed",
                    "retrieved_at": source.retrieved_at.isoformat(),
                }
                for source in sources
            ]
            grouped = body["requirements"]
            payload = {
                "contract_version": "eligibility-requirements-package-v2",
                "tenant_id": tenant_id,
                "cluster_id": cluster_id,
                "pattern_version": pattern_version,
                "program_id": criteria_package.program_id,
                "program_title": criteria_package.title,
                "catalog_version": "request-scoped",
                "required_documents": [
                    {
                        "kind": "required_document",
                        "text": item["text"],
                        "source_id": item["citation"]["source_id"],
                    }
                    for item in grouped["required_documents"]
                ],
                "forms": [
                    {
                        "kind": "form",
                        "text": item["text"],
                        "source_id": item["citation"]["source_id"],
                    }
                    for item in grouped["forms"]
                ],
                "deadlines": [
                    {
                        "kind": "deadline",
                        "text": item["text"],
                        "source_id": item["citation"]["source_id"],
                    }
                    for item in grouped["deadlines"]
                ],
                "procedural_steps": [
                    {
                        "kind": "application_step",
                        "text": item["text"],
                        "source_id": item["citation"]["source_id"],
                    }
                    for item in grouped["procedural_steps"]
                ],
                "unavailable_detail_kinds": body["unavailable_detail_kinds"],
                "source_references": source_refs,
                "source_health": [
                    {"source_id": source.source_id, "status": "current"}
                    for source in sources
                ],
                "source_limitations": list(criteria_package.source_limitations),
                "profile_snapshot": profile_submission.profile.model_dump(mode="json"),
                "evaluation": {
                    **evaluation,
                    "requirements_detail_citations": [
                        item.model_dump(mode="json") for item in details
                    ],
                },
                "created_at": datetime.now(UTC).isoformat(),
                "official_eligibility_determination": False,
                "official_application_submitted": False,
            }
            package_identity = {
                key: value for key, value in payload.items() if key != "created_at"
            }
            package_digest = hashlib.sha256(
                json.dumps(
                    package_identity,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            package_id = str(uuid.uuid5(PACKAGE_NAMESPACE, package_digest))
            payload["package_id"] = package_id
            package_payload = RequirementsPackage.model_validate(payload).model_dump(
                mode="json"
            )
            receipt = writer(package_payload)
            readback = reader(package_id)
            if not isinstance(readback, dict) or readback != package_payload:
                return {
                    "status": "readback_failed",
                    "written": True,
                    "package_id": package_id,
                    "receipt": receipt,
                    "readback_verified": False,
                    "next_action": "retry_package_readback",
                }
            return {
                "status": "written",
                "written": True,
                "request_scoped": True,
                "package_id": package_id,
                "profile_id": profile_submission.profile.profile_id,
                "receipt": receipt,
                "readback_verified": True,
                "package": readback,
                "report_markdown": render_requirements_report(readback),
                "evaluation": evaluation,
                "evaluation_performed": evaluation.get("evaluation_performed") is True,
                "official_eligibility_determination": False,
                "official_application_submitted": False,
                "persistence_performed": True,
            }
        except (TypeError, ValueError, RuntimeError) as exc:
            return {
                "status": "write_failed",
                "written": False,
                "reason": str(exc),
                "next_action": "fix_package_or_store_failure_and_retry",
            }


def _no_mapped_criteria_result(
    prepared: dict[str, object],
    sources: list[SourceReference],
    program_id: str,
) -> dict[str, object]:
    """Return a truthful non-evaluation when the candidate has no compiled rule."""
    source_references = [source.model_dump(mode="json") for source in sources]
    source_ids = sorted(
        {
            source["source_id"]
            for source in source_references
            if isinstance(source, dict) and isinstance(source.get("source_id"), str)
        }
    )
    return {
        "status": "mapping_required",
        "reason_code": "criteria_mapping_required",
        "evaluation_scope": "request_scoped_candidate",
        "criteria_package_id": prepared.get("criteria_package_id"),
        "program_id": prepared.get("program_id"),
        "source_references": source_references,
        "criteria_status": "unmapped",
        "next_action": "prepare_criteria_mapping_from_source",
        "next_step": CriteriaMappingWorkflowAction(
            program_id=program_id, source_ids=source_ids
        ).model_dump(mode="json"),
        "evaluation_performed": False,
        "unresolved_requirements": prepared.get("unresolved_requirements", []),
        "coverage": prepared.get("coverage", {"source_completeness": "not_proven"}),
        "source_validation": {
            "status": "caller_supplied_lineage_consistency_checked",
            "independently_revalidated": False,
        },
        "official_eligibility_determination": False,
        **_no_write_effects(),
    }


def _unresolved_evidence(
    requirement: RequirementCandidate,
    diagnostic: dict[str, object] | None,
) -> dict[str, object]:
    """Keep an unmapped source clause visible as unknown with its cited reason."""
    return {
        "requirement_id": requirement.requirement_id,
        "statement": requirement.statement,
        "requirement_type": requirement.requirement_type,
        "mapping_status": requirement.mapping_status,
        "evaluation_outcome": (
            "unknown" if requirement.requirement_type == "eligibility" else None
        ),
        "reason_code": (
            diagnostic.get("reason_code")
            if diagnostic
            else requirement.unresolved_reason or "criteria_mapping_unresolved"
        ),
        "explanation": (
            diagnostic.get("explanation")
            if diagnostic
            else "The source clause has not been fully mapped to a supported rule."
        ),
        "recommended_action": (
            diagnostic.get("recommended_action")
            if diagnostic
            else "review_requirement_mapping"
        ),
        "ask_user": bool(diagnostic and diagnostic.get("ask_user")),
        "profile_attribute": requirement.profile_attribute,
        "citation": requirement.citation.model_dump(mode="json", exclude_none=True),
    }


def _compiler_diagnostics_by_requirement(
    prepared: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Index compiler rejection diagnostics by requirement ID."""
    unresolved = prepared.get("unresolved_requirements")
    if not isinstance(unresolved, list):
        return {}
    return {
        requirement_id: item
        for item in unresolved
        if isinstance(item, dict)
        and isinstance((requirement_id := item.get("requirement_id")), str)
    }


def _mapped_rule_fraction(
    program: ProgramRecord,
    evidence: list[ProgramCriterionEvidence],
    logic_groups: list[ProgramLogicGroupEvidence],
) -> tuple[float | None, dict[str, int]]:
    """Summarize only mapped non-grouped clauses and explicitly declared groups."""
    grouped_ids = {
        criterion.criterion_id
        for criterion in program.criteria
        if criterion.logic_group_id is not None
    }
    outcomes = [
        item.outcome for item in evidence if item.criterion_id not in grouped_ids
    ]
    outcomes.extend(item.outcome for item in logic_groups)
    met = outcomes.count("met")
    unmet = outcomes.count("unmet")
    unknown = outcomes.count("unknown")
    not_applicable = outcomes.count("not_applicable")
    evaluable = met + unmet
    return (
        (met / evaluable if evaluable else None),
        {
            "met": met,
            "unmet": unmet,
            "unknown": unknown,
            "not_applicable": not_applicable,
            "evaluable": evaluable,
        },
    )


def _no_write_effects(*, rules_activated: bool = False) -> dict[str, bool]:
    """State request-scoped effects, including an automatic criteria write."""
    return {
        "request_scoped": True,
        "persistence_performed": rules_activated,
        "shared_catalog_changed": False,
        "rules_activated": rules_activated,
    }
