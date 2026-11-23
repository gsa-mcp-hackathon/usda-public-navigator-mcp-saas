"""Synthetic Eligibility cluster tools at pattern version 0.1.0."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from fastmcp import FastMCP

from clusters.eligibility_candidate import (
    CandidateCitation,
    CandidateListing,
    ProgramCandidateRequest,
    ProgramCriteriaPackageRequest,
    RequirementCandidate,
    _stable_program_id,
)
from clusters.eligibility_contracts import (
    CriteriaMappingWorkflowAction,
    ProgramComparisonSummary,
    ProgramRecord,
    ProgramResultEntry,
    SourceBackedProgramDetail,
    SourceHealth,
    SourceReference,
)
from clusters.eligibility_criteria_persistence import (
    evaluate_active_criteria_ruleset,
    read_active_criteria_readiness,
)
from clusters.eligibility_discovery import (
    CatalogReadFailure,
    DatasetCatalog,
    DiscoveryResult,
    DiscoveryScope,
    ProfileSubmission,
    ProgramCriteriaReference,
    approved_source_specs,
    dataset_catalog_from_payload,
    discover_program_candidates,
    merge_same_program_records,
    normalize_profile,
)
from clusters.eligibility_evaluation import (
    UnsupportedCriterionError,
    evaluate_program_criteria,
)
from clusters.eligibility_inventory import register_search_program_inventory
from clusters.eligibility_persistence import HttpCriteriaRulesetRepository
from clusters.eligibility_profile_projection import (
    ProfileFixtureReader,
    ProfileFixtureReference,
    ProfileProjectionMapping,
    ProjectionResult,
    project_profile_fixture,
    project_shared_profile,
)
from clusters.eligibility_profile_readiness import build_selected_ruleset_facts
from clusters.eligibility_public_search import PublicSearchProvider
from clusters.eligibility_requirements import (
    RequirementsPackage,
    build_requirements_package,
    render_requirements_report,
)
from clusters.eligibility_results import assemble_program_results
from clusters.eligibility_versions import (
    ACTIVE_RULESET_READINESS_VERSION,
    supports_eligibility_feature,
)
from example_server.models import (
    TenantCatalogResult,
    TenantProgram,
    TenantPublicListing,
)
from example_server.storage import CatalogRepository, StorageUnavailable

MAX_SHARED_INVENTORY_PAGES = 100
SHARED_INVENTORY_PAGE_SIZE = 100
SEARCHABLE_DATA_STATUSES = {"loaded", "partial", "stale"}


def _rule_sources_match_criteria_references(
    rule_sources: object,
    references: list[ProgramCriteriaReference],
) -> bool:
    """Match persisted citations to primary or explicitly reviewed sources.

    Secondary source verification state remains attached to its source record;
    this link check only confirms its exact identity and reviewed relationship.
    A shared program identity alone never relaxes exact URL matching.
    """
    if not isinstance(rule_sources, list) or not rule_sources:
        return False
    linked_sources = {
        (reference.source_id, str(reference.source_url)) for reference in references
    }
    linked_sources.update(
        (link.source.source_id, str(link.source.url))
        for reference in references
        for link in reference.criteria_source_links
        if link.source.url is not None
    )
    for source in rule_sources:
        if not isinstance(source, dict):
            return False
        source_id = source.get("source_id")
        url = source.get("url")
        if not isinstance(source_id, str) or not isinstance(url, str):
            return False
        if (source_id, url) not in linked_sources:
            return False
    return True


def _rule_sources_match_program_sources(
    rule_sources: object, program_sources: list[SourceReference]
) -> bool:
    """Require an exact source identity link before preferring persisted rules."""
    if not isinstance(rule_sources, list) or not rule_sources or not program_sources:
        return False
    program_identities = {
        (source.source_id, str(source.url))
        for source in program_sources
        if source.url is not None
    }
    return any(
        isinstance(source, dict)
        and isinstance(source.get("source_id"), str)
        and isinstance(source.get("url"), str)
        and (source["source_id"], source["url"]) in program_identities
        for source in rule_sources
    )


def _active_ruleset_identity(states: list[dict[str, object]]) -> dict[str, object]:
    """Expose one unambiguous active package identity on search/discovery rows."""
    identities = {
        (
            state.get("active_ruleset_package_id"),
            state.get("active_ruleset_content_sha256"),
        )
        for state in states
        if state.get("active_ruleset_status") == "active"
        and isinstance(state.get("active_ruleset_package_id"), str)
        and isinstance(state.get("active_ruleset_content_sha256"), str)
    }
    if len(identities) != 1:
        return {}
    package_id, content_sha256 = identities.pop()
    return {
        "active_ruleset_package_id": package_id,
        "active_ruleset_content_sha256": content_sha256,
    }


def _criteria_package_for_reference(
    reference: ProgramCriteriaReference,
    *,
    navigator: str,
    jurisdiction: Any,
    agency: str,
    description: str,
) -> ProgramCriteriaPackageRequest:
    """Build mapper input from selected public source identity, not catalog status."""
    retrieved_at = datetime.fromisoformat(reference.retrieved_at.replace("Z", "+00:00"))
    source = {
        "source_id": reference.source_id,
        "source_type": "public_source",
        "approval_status": "unverified",
        "agency": agency or "Public program publisher",
        "title": reference.title,
        "url": str(reference.source_url),
        "jurisdiction": jurisdiction.model_dump(mode="json"),
        "retrieved_at": retrieved_at,
        "publisher_version": reference.source_version,
    }
    citation = CandidateCitation(
        source_url=reference.source_url,
        source_record_id=reference.source_record_id,
        retrieved_at=retrieved_at,
        source_id=reference.source_id,
        source_version=reference.source_version,
        locator="selected_program_source",
        excerpt=(
            "Selected program source; runtime mapper will attach current citations."
        ),
    )
    placeholder = RequirementCandidate(
        requirement_id="runtime-mapping-request",
        statement="Extract the selected program's published eligibility criteria.",
        requirement_type="context",
        mapping_status="context_only",
        citation=citation,
    )
    return ProgramCriteriaPackageRequest(
        program_id=reference.program_id,
        catalog_version="request-scoped",
        navigator=navigator,
        title=reference.title,
        description=description or reference.title,
        jurisdiction=jurisdiction,
        sources=[source],
        requirements=[placeholder],
    )


def _criteria_package_for_listing(
    catalog: DatasetCatalog, listing: TenantPublicListing, navigator: str
) -> ProgramCriteriaPackageRequest:
    """Make a stable mapping request directly from a selected public listing."""
    source = listing.source
    selected_url = listing.program_url or listing.website_url or listing.opportunity_url
    if selected_url is None:
        selected_url = source.url
    if selected_url is None or source.retrieved_at is None:
        raise ValueError("Selected listing has no current public source URL.")
    candidate = ProgramCandidateRequest(
        source_id=source.source_id,
        source_record_id=listing.listing_id,
        source_version=source.publisher_version or "observed",
        source_url=selected_url,
        retrieved_at=source.retrieved_at,
        listing=CandidateListing(
            listing_kind=(
                "funding_opportunity"
                if listing.record_kind == "funding_opportunity"
                else "assistance_program"
            ),
            program_number=listing.program_number,
            title=listing.title,
            summary=listing.summary,
            agency=listing.agency,
            jurisdiction_code=listing.jurisdiction.state_code,
            canonical_url=selected_url,
        ),
    )
    program_id, _identity_basis = _stable_program_id(candidate)
    reference = ProgramCriteriaReference(
        program_id=program_id,
        dataset_id=catalog.dataset_id,
        source_record_id=listing.listing_id,
        title=listing.title,
        source_id=source.source_id,
        source_url=selected_url,
        source_version=source.publisher_version or "observed",
        retrieved_at=source.retrieved_at.isoformat(),
        coverage="runtime_selected_listing",
    )
    return _criteria_package_for_reference(
        reference,
        navigator=navigator,
        jurisdiction=listing.jurisdiction,
        agency=listing.agency,
        description=listing.summary or listing.title,
    )


async def _apply_selected_ruleset_readiness(
    result: DiscoveryResult,
    repository: HttpCriteriaRulesetRepository | None,
) -> DiscoveryResult:
    """Read active-rule presence for selected discovery without profile scoring."""
    semaphore = asyncio.Semaphore(4)
    readiness_by_program: dict[str, dict[str, object]] = {}

    async def read_one(
        program_id: str, sources: list[dict[str, object]]
    ) -> dict[str, object]:
        if repository is None:
            return {
                "active_ruleset_status": "unavailable",
                "evaluation_available": False,
            }
        async with semaphore:
            return await asyncio.to_thread(
                read_active_criteria_readiness, program_id, sources, repository
            )

    async def enrich(record: object, *, is_listing: bool) -> object:
        references = getattr(record, "criteria_references", [])
        program_ids = (
            {
                *getattr(record, "related_program_ids", []),
                *(reference.program_id for reference in references),
            }
            if is_listing
            else {getattr(record, "program_id")}
        )
        if not program_ids:
            return record.model_copy(update={"active_ruleset_status": "not_linked"})
        sources = getattr(record, "sources", [])
        source_values = [source.model_dump(mode="json") for source in sources]
        if is_listing:
            source_values.append(record.source.model_dump(mode="json"))
        source_values.extend(
            {"source_id": item.source_id, "url": str(item.source_url)}
            for item in references
        )
        source_values.extend(
            link.source.model_dump(mode="json")
            for item in references
            for link in item.criteria_source_links
        )
        normalized_sources = [
            {
                **source,
                "url": source.get("url", source.get("source_url")),
            }
            for source in source_values
            if isinstance(source.get("source_id"), str)
            and isinstance(source.get("url", source.get("source_url")), str)
        ]
        ordered_program_ids = sorted(program_ids)
        states = await asyncio.gather(
            *(
                read_one(program_id, normalized_sources)
                for program_id in ordered_program_ids
            )
        )
        readiness_by_program.update(zip(ordered_program_ids, states))
        statuses = {str(state.get("active_ruleset_status")) for state in states}
        status = (
            "active"
            if "active" in statuses
            else "unavailable"
            if "unavailable" in statuses
            else "source_mismatch"
            if "source_mismatch" in statuses
            else "invalid"
            if "invalid" in statuses
            else "not_found"
        )
        return record.model_copy(
            update={
                "active_ruleset_status": status,
                **_active_ruleset_identity(states),
                "evaluation_available": bool(
                    getattr(record, "evaluation_available", False)
                )
                or any(
                    state.get("active_ruleset_status") == "active"
                    and state.get("evaluation_available") is True
                    for state in states
                ),
            }
        )

    candidates = [await enrich(item, is_listing=False) for item in result.candidates]
    listings = [
        await enrich(item, is_listing=True) for item in result.unassessed_listings
    ]
    profile_readiness = result.profile_readiness
    if profile_readiness is not None and profile_readiness.selected_program_id:
        selected_candidate = next(
            (
                item
                for item in candidates
                if item.program_id == profile_readiness.selected_program_id
            ),
            None,
        )
        selected_listing = next(
            (
                item
                for item in listings
                if item.listing_id == profile_readiness.selected_program_id
                or item.program_number == profile_readiness.selected_program_id
                or profile_readiness.selected_program_id in item.related_program_ids
            ),
            None,
        )
        if (
            selected_candidate is None
            and selected_listing is not None
            and selected_listing.active_ruleset_status == "active"
            and selected_listing.evaluation_available
        ):
            related_program_ids = {
                *selected_listing.related_program_ids,
                *(
                    reference.program_id
                    for reference in selected_listing.criteria_references
                ),
            }
            states = [
                readiness_by_program[program_id]
                for program_id in sorted(related_program_ids)
                if program_id in readiness_by_program
            ]
            ruleset_facts = [
                fact
                for state in states
                for fact in state.get("profile_facts", [])
                if isinstance(fact, dict)
            ]
            selected_facts = (
                build_selected_ruleset_facts(result.profile, ruleset_facts)
                if ruleset_facts
                else profile_readiness.selected_program_facts
            )
            ruleset_sources = {
                source.source_id: source
                for fact in selected_facts
                for program in fact.programs
                for source in program.source_references
            }
            ruleset_sources.update(
                {
                    source.source_id: source
                    for source in profile_readiness.selected_program_source_references
                }
            )
            profile_readiness = profile_readiness.model_copy(
                update={
                    "selected_program_status": "mapped",
                    "selected_program_reason_code": "mapped_criteria_available",
                    "selected_program_explanation": (
                        "An active persisted criteria ruleset is linked to this "
                        "listing. Run evaluation to calculate this profile's "
                        "criterion facts and results. No result is inferred here."
                    ),
                    "selected_program_next_action": "compare_selected_program",
                    "selected_program_facts": selected_facts,
                    "selected_program_source_references": sorted(
                        ruleset_sources.values(), key=lambda source: source.source_id
                    ),
                }
            )
    return result.model_copy(
        update={
            "contract_version": "eligibility-discovery-v3",
            "candidates": candidates,
            "unassessed_listings": listings,
            "profile_readiness": profile_readiness,
        }
    )


def jurisdiction_mismatch_evidence(
    submission: ProfileSubmission, program: ProgramRecord
) -> list[dict[str, object]]:
    """Return not_applicable evidence only when every criterion is a jurisdiction miss.

    Used when a criteria source is not current: the criteria cannot be evaluated,
    but a known profile location outside the program's jurisdiction still makes
    the program not applicable, and that fact must not be dropped.
    """
    try:
        result = evaluate_program_criteria(submission, program)
    except (UnsupportedCriterionError, ValueError):
        return []
    evidence = result.evidence
    if not evidence or any(
        item.reason_code != "jurisdiction_mismatch" for item in evidence
    ):
        return []
    return [item.model_dump(mode="json") for item in evidence]


def register_tools(
    app: FastMCP,
    *,
    tenant_id: str,
    cluster_id: str,
    pattern_version: str,
    programs: list[TenantProgram],
    catalog_repository: CatalogRepository | None,
    cluster_config: Mapping[str, object],
    profile_fixture_reader: ProfileFixtureReader | None = None,
    public_search_provider: PublicSearchProvider | None = None,
    cluster_mcp_endpoints: Mapping[str, str] | None = None,
    criteria_ruleset_repository: HttpCriteriaRulesetRepository | None = None,
) -> None:
    """Register catalog discovery operations for one configured tenant."""

    mapping = cluster_config.get("profile_projection_mapping")
    if isinstance(mapping, ProfileProjectionMapping):
        profile_mapping = mapping
    elif isinstance(mapping, dict):
        try:
            profile_mapping = ProfileProjectionMapping.model_validate(mapping)
        except ValueError:
            profile_mapping = None
    else:
        profile_mapping = None

    async def resolve_profile_input(
        profile_payload: dict[str, Any] | None,
        profile_reference: dict[str, str] | None,
        shared_profile: dict[str, Any] | None = None,
        navigator: str | None = None,
    ) -> tuple[dict[str, Any] | None, ProjectionResult | None, str | None]:
        """Require one input path and project references inside the tenant."""
        supplied = [
            item
            for item in (profile_payload, profile_reference, shared_profile)
            if item is not None
        ]
        if len(supplied) != 1:
            return None, None, "invalid_request"
        if profile_payload is not None:
            return profile_payload, None, None
        if shared_profile is not None:
            if profile_mapping is None:
                return None, None, "profile_reference_unavailable"
            shared = project_shared_profile(
                shared_profile,
                profile_mapping,
                navigator=navigator or "general",
                tenant_id=tenant_id,
                template_id=cluster_id,
                template_version=pattern_version,
            )
            if shared.status != "available" or shared.profile_payload is None:
                return None, shared, shared.status
            return shared.profile_payload, shared, None
        try:
            reference = ProfileFixtureReference.model_validate(profile_reference)
        except (ValueError, TypeError):
            return None, None, "invalid_request"
        if profile_fixture_reader is None or profile_mapping is None:
            return None, None, "profile_reference_unavailable"
        if not profile_mapping.allows_fixture_set(reference):
            return None, None, "profile_scope_rejected"
        try:
            fixture = await asyncio.to_thread(
                profile_fixture_reader.read_fixture, reference
            )
        except (OSError, RuntimeError, ValueError):
            return None, None, "profile_reference_unavailable"
        projection = project_profile_fixture(
            fixture,
            reference,
            profile_mapping,
            tenant_id=tenant_id,
            template_id=cluster_id,
            template_version=pattern_version,
        )
        if projection.status != "available" or projection.profile_payload is None:
            return None, projection, projection.status
        return projection.profile_payload, projection, None

    def attach_projection(
        result: dict[str, object], projection: ProjectionResult | None
    ) -> dict[str, object]:
        """Add safe reference/mapping lineage, never projected profile values."""
        if projection is None:
            return result
        result["profile_reference"] = projection.profile_reference
        result["projection_lineage"] = projection.projection_lineage
        result["profile_field_provenance"] = projection.field_provenance
        result["unresolved_profile_fields"] = projection.unresolved_fields
        return result

    async def load_shared_program_catalogs() -> tuple[
        list[DatasetCatalog], list[str], list[CatalogReadFailure]
    ]:
        """Read searchable approved program datasets from the shared registry.

        Tenant dataset and jurisdiction configuration is intentionally not used
        here: it adds tenant semantics and cannot define shared discovery scope.
        The Persistence API inventory remains the source-approval boundary.
        """
        list_inventory = getattr(
            catalog_repository, "list_public_catalog_inventory", None
        )
        read_dataset = getattr(catalog_repository, "read_cluster_public_dataset", None)
        read_dataset_from_snapshot = getattr(
            catalog_repository,
            "read_cluster_public_dataset_from_catalog_snapshot",
            None,
        )
        if not callable(list_inventory) or not callable(read_dataset):
            raise StorageUnavailable("Shared public catalog reader is unavailable.")

        items: list[dict[str, object]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MAX_SHARED_INVENTORY_PAGES):
            page = await asyncio.to_thread(
                list_inventory, limit=SHARED_INVENTORY_PAGE_SIZE, cursor=cursor
            )
            page_items = page.get("items") if isinstance(page, dict) else None
            if not isinstance(page_items, list) or any(
                not isinstance(item, dict) for item in page_items
            ):
                raise StorageUnavailable("Shared public catalog page is invalid.")
            items.extend(page_items)
            if not page.get("truncated"):
                break
            next_cursor = page.get("next_cursor")
            if (
                not isinstance(next_cursor, str)
                or not next_cursor
                or next_cursor in seen_cursors
            ):
                raise StorageUnavailable("Shared public catalog cursor is invalid.")
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            raise StorageUnavailable("Shared public catalog exceeds the page bound.")

        specs = approved_source_specs()
        dataset_targets: dict[str, str] = {}
        for item in items:
            dataset = item.get("dataset")
            source = item.get("source")
            if not isinstance(dataset, dict) or not isinstance(source, dict):
                continue
            metadata = dataset.get("metadata")
            dataset_id = dataset.get("dataset_id")
            source_id = source.get("source_id")
            if (
                item.get("registration_status") == "registered"
                and item.get("searchable") is True
                and item.get("data_status") in SEARCHABLE_DATA_STATUSES
                and isinstance(metadata, dict)
                and metadata.get("catalog_namespace") == "programs"
                and isinstance(dataset_id, str)
                and isinstance(source_id, str)
            ):
                dataset_targets[dataset_id] = source_id

        catalogs: list[DatasetCatalog] = []
        unavailable_sources: list[str] = []
        catalog_read_failures: list[CatalogReadFailure] = []
        for dataset_id, source_id in sorted(dataset_targets.items()):
            try:
                if callable(read_dataset_from_snapshot):
                    payload = await asyncio.to_thread(
                        read_dataset_from_snapshot,
                        dataset_id,
                        catalog_snapshot={"inventory_items": items},
                    )
                else:
                    payload = await asyncio.to_thread(read_dataset, dataset_id)
                catalogs.append(
                    dataset_catalog_from_payload(
                        payload,
                        spec=specs.get(dataset_id),
                        expected_dataset_id=dataset_id,
                        expected_source_id=source_id,
                    )
                )
            except StorageUnavailable:
                catalog_read_failures.append(
                    CatalogReadFailure(
                        dataset_id=dataset_id,
                        source_id=source_id,
                        reason_code="data_api_read_failed",
                    )
                )
            except (ValueError, TypeError):
                catalog_read_failures.append(
                    CatalogReadFailure(
                        dataset_id=dataset_id,
                        source_id=source_id,
                        reason_code="invalid_catalog_response",
                    )
                )
        return catalogs, sorted(set(unavailable_sources)), catalog_read_failures

    @app.tool(name="list_tenant_programs")
    async def list_tenant_programs() -> dict[str, object]:
        """List shared active programs available through this tenant context.

        Tenant mappings add semantic context; they do not restrict which approved
        shared-cluster program datasets are listed or searched.
        """
        try:
            (
                catalogs,
                unavailable_sources,
                catalog_read_failures,
            ) = await load_shared_program_catalogs()
        except StorageUnavailable:
            return TenantCatalogResult(
                tenant_id=tenant_id,
                cluster_id=cluster_id,
                programs=[],
                status="unavailable",
                error="Catalog store unavailable.",
            ).model_dump(mode="json", exclude_none=True)
        shared_programs = [
            TenantProgram.model_validate(
                {
                    "program_id": program.program_id,
                    "title": program.title,
                    "source_id": program.sources[0].source_id,
                    "jurisdiction": program.jurisdiction.level,
                    "catalog_version": program.catalog_version,
                    "schema_version": program.contract_version,
                    "source_url": str(program.sources[0].url),
                    "attributes": {
                        "eligibility.program.navigator": program.navigator,
                        "eligibility.program.description": program.description,
                        "eligibility.program.criteria": [
                            item.model_dump(mode="json") for item in program.criteria
                        ],
                    },
                    "provenance": {
                        "sources": [
                            item.model_dump(mode="json") for item in program.sources
                        ],
                        "source_limitations": program.source_limitations,
                    },
                    "details": [
                        item.model_dump(mode="json") for item in program.details
                    ],
                }
            )
            for catalog in catalogs
            for program in catalog.programs
        ]
        shared_public_listings = [
            TenantPublicListing(
                listing_id=listing.listing_id,
                record_kind=listing.record_kind,
                title=listing.title,
                agency=listing.agency,
                jurisdiction=(
                    f"US-{listing.jurisdiction.state_code}"
                    if listing.jurisdiction.state_code
                    else listing.jurisdiction.country_code
                ),
                source_id=listing.source.source_id,
                source_url=listing.source.url,
                criteria_status=listing.criteria_status,
            )
            for catalog in catalogs
            for listing in catalog.listings
        ]
        return TenantCatalogResult(
            tenant_id=tenant_id,
            cluster_id=cluster_id,
            programs=shared_programs,
            public_listings=shared_public_listings,
            status=("partial" if unavailable_sources or catalog_read_failures else "ok")
            if catalogs
            else "unavailable",
            error=(
                "Some source or catalog reads were incomplete."
                if (unavailable_sources or catalog_read_failures) and catalogs
                else "Shared program catalog reads failed."
                if catalog_read_failures and not catalogs
                else "Shared program inventory is unavailable."
                if not catalogs
                else None
            ),
            unavailable_sources=unavailable_sources,
            catalog_read_failures=(
                [item.model_dump(mode="json") for item in catalog_read_failures]
                if catalog_read_failures
                else None
            ),
        ).model_dump(mode="json", exclude_defaults=True)

    @app.tool(name="discover_program_candidates")
    async def discover_program_candidates_tool(
        profile_payload: dict[str, Any] | None = None,
        ambiguous_attributes: list[str] | None = None,
        profile_reference: dict[str, str] | None = None,
        shared_profile: dict[str, Any] | None = None,
        navigator: str | None = None,
        selected_program_id: str | None = None,
        need_query: str | None = None,
    ) -> dict[str, object]:
        """List shared programs or readiness facts for one selected program.

        Without a selection, returns shared discovery, optionally narrowed to a
        plain-language need. With a selected ID, returns only that record and its
        profile-readiness gaps, regardless of query relevance. This read-only
        operation never evaluates eligibility.
        """
        profile_payload, projection, input_error = await resolve_profile_input(
            profile_payload, profile_reference, shared_profile, navigator
        )
        if input_error:
            return {
                "status": input_error,
                "tenant_id": tenant_id,
                "evaluation_performed": False,
            }
        try:
            submission = ProfileSubmission.model_validate(
                {
                    "profile": profile_payload,
                    "ambiguous_attributes": ambiguous_attributes or [],
                }
            )
            scope = DiscoveryScope(tenant_id=tenant_id)
        except (ValueError, TypeError):
            return {
                "status": "invalid_request",
                "tenant_id": tenant_id,
                "error": "Profile is invalid.",
                "evaluation_performed": False,
            }

        try:
            (
                catalogs,
                unavailable_sources,
                catalog_read_failures,
            ) = await load_shared_program_catalogs()
        except StorageUnavailable:
            return attach_projection(
                DiscoveryResult(
                    status="unavailable",
                    tenant_id=tenant_id,
                    navigator=submission.profile.navigator,
                    profile=normalize_profile(submission),
                    catalog_versions=[],
                    candidates=[],
                    source_health=[],
                    unavailable_sources=[],
                    stale_sources=[],
                    coverage="unavailable",
                ).model_dump(mode="json"),
                projection,
            )

        result = discover_program_candidates(
            submission,
            scope,
            catalogs,
            unavailable_source_ids=unavailable_sources,
            catalog_read_failures=catalog_read_failures,
            selected_program_id=selected_program_id,
            need_query=need_query,
        )
        response = result.model_dump(mode="json")
        if supports_eligibility_feature(
            pattern_version, ACTIVE_RULESET_READINESS_VERSION
        ):
            result = result.model_copy(
                update={"contract_version": "eligibility-discovery-v3"}
            )
            if selected_program_id is not None:
                result = await _apply_selected_ruleset_readiness(
                    result, criteria_ruleset_repository
                )
            response = result.model_dump(mode="json")
        else:
            for item in [
                *response.get("candidates", []),
                *response.get("unassessed_listings", []),
            ]:
                if isinstance(item, dict):
                    item.pop("active_ruleset_status", None)
                    item.pop("evaluation_available", None)
        return attach_projection(response, projection)

    if supports_eligibility_feature(pattern_version, "0.1.5"):
        register_search_program_inventory(
            app,
            cluster_id=cluster_id,
            pattern_version=pattern_version,
            catalog_repository=catalog_repository,
            public_search_provider=public_search_provider,
            criteria_ruleset_repository=criteria_ruleset_repository,
        )

    @app.tool(name="evaluate_program_criteria")
    async def evaluate_program_criteria_tool(
        program_id: str,
        profile_payload: dict[str, Any] | None = None,
        ambiguous_attributes: list[str] | None = None,
        profile_reference: dict[str, str] | None = None,
        shared_profile: dict[str, Any] | None = None,
        navigator: str | None = None,
    ) -> dict[str, object]:
        """Compare a sparse profile with a selected shared program or listing.

        Mapped programs return criterion outcomes with source references. When
        a selected source has no usable mapped rules, return a typed client
        mapping workflow and the source identity; scoring never waits on an
        inline mapper. Results are never an official eligibility determination.
        """
        profile_payload, projection, input_error = await resolve_profile_input(
            profile_payload, profile_reference, shared_profile, navigator
        )
        if input_error:
            return {
                "status": input_error,
                "tenant_id": tenant_id,
                "program_id": program_id,
                "evaluation_performed": False,
            }
        try:
            submission = ProfileSubmission.model_validate(
                {
                    "profile": profile_payload,
                    "ambiguous_attributes": ambiguous_attributes or [],
                }
            )
        except (ValueError, TypeError):
            return {
                "status": "invalid_request",
                "tenant_id": tenant_id,
                "error": "Profile is invalid.",
                "evaluation_performed": False,
            }

        try:
            (
                catalogs,
                unavailable_sources,
                catalog_read_failures,
            ) = await load_shared_program_catalogs()
        except StorageUnavailable:
            return {
                "status": "unavailable",
                "tenant_id": tenant_id,
                "program_id": program_id,
                "error": "Shared catalog reads failed.",
                "evaluation_performed": False,
            }
        matching_programs = merge_same_program_records(
            [
                program
                for catalog in catalogs
                for program in catalog.programs
                if program.program_id == program_id
            ]
        )
        matching_listings = [
            (catalog, listing)
            for catalog in catalogs
            for listing in catalog.listings
            if program_id
            in {
                listing.listing_id,
                listing.program_number,
                *listing.related_program_ids,
            }
        ]

        if not matching_programs and len(matching_listings) > 1:
            return {
                "status": "invalid_catalog",
                "tenant_id": tenant_id,
                "program_id": program_id,
                "listing_ids": sorted(
                    {listing.listing_id for _, listing in matching_listings}
                ),
                "error": "Selected listing identity is ambiguous in shared inventory.",
                "evaluation_performed": False,
            }

        if (
            not matching_programs
            and len(matching_listings) == 1
            and (
                matching_listings[0][1].record_role == "referral"
                or matching_listings[0][1].record_kind == "referral_resource"
            )
        ):
            catalog, listing = matching_listings[0]
            source_health = catalog.source_health.model_dump(mode="json")
            source_reference = listing.source.model_dump(mode="json")
            source_is_current = catalog.source_health.status == "current"
            return attach_projection(
                {
                    "status": "not_assessed",
                    "reason_code": "referral_only_resource",
                    "tenant_id": tenant_id,
                    "program_id": program_id,
                    "listing_id": listing.listing_id,
                    "title": listing.title,
                    "agency": listing.agency,
                    "record_role": "referral",
                    "record_kind": "referral_resource",
                    "jurisdiction": listing.jurisdiction.model_dump(mode="json"),
                    "source_reference": source_reference,
                    "source_health": source_health,
                    "criteria_status": "not_assessed",
                    "criteria_reference_status": "not_applicable_referral",
                    "evaluation_performed": False,
                    "official_eligibility_determination": False,
                    "next_action": (
                        "review_referral_source"
                        if source_is_current
                        else "refresh_referral_source"
                    ),
                    "next_step": {
                        "source_reference": source_reference,
                        "message": (
                            "This record is a referral resource, not an "
                            "eligibility program. Use its official source to "
                            "learn about services and contact options."
                        ),
                    },
                },
                projection,
            )

        if not matching_programs and criteria_ruleset_repository is not None:
            criteria_references = [
                reference
                for catalog in catalogs
                for reference in catalog.criteria_references
                if reference.program_id == program_id
            ]
            if criteria_references:
                persisted_evaluation = await asyncio.to_thread(
                    evaluate_active_criteria_ruleset,
                    program_id,
                    submission,
                    criteria_ruleset_repository,
                )
                if persisted_evaluation.get("evaluation_performed") is True:
                    active_source_records = persisted_evaluation.get(
                        "source_references"
                    )
                    if not _rule_sources_match_criteria_references(
                        active_source_records, criteria_references
                    ):
                        return {
                            "status": "criteria_link_conflict",
                            "reason_code": (
                                "persisted_ruleset_source_does_not_match_catalog_reference"
                            ),
                            "program_id": program_id,
                            "evaluation_performed": False,
                            "next_action": "review_criteria_program_source_link",
                        }
                    matching_reference = next(
                        reference
                        for reference in criteria_references
                        if any(
                            source.get("source_id") == reference.source_id
                            and source.get("url") == str(reference.source_url)
                            for source in active_source_records
                            if isinstance(source, dict)
                        )
                        or any(
                            isinstance(source, dict)
                            and any(
                                source.get("source_id") == link.source.source_id
                                and source.get("url") == str(link.source.url)
                                for link in reference.criteria_source_links
                                if link.source.url is not None
                            )
                            for source in active_source_records
                        )
                    )
                    return attach_projection(
                        {
                            **persisted_evaluation,
                            "status": "ok",
                            "tenant_id": tenant_id,
                            "criteria_reference": matching_reference.model_dump(
                                mode="json"
                            ),
                            "criteria_references": [
                                reference.model_dump(mode="json")
                                for reference in criteria_references
                            ],
                            "evaluation_performed": True,
                            "official_eligibility_determination": False,
                        },
                        projection,
                    )

        if not matching_programs and matching_listings:
            catalog, listing = matching_listings[0]
            linked_program_ids = {
                program_id,
                listing.listing_id,
                *listing.related_program_ids,
            }
            criteria_references = [
                reference
                for item in catalogs
                for reference in item.criteria_references
                if reference.program_id in linked_program_ids
                or (
                    listing.program_number is not None
                    and listing.program_number in reference.assistance_listing_ids
                )
            ]
            criteria_references = list(
                {
                    (reference.dataset_id, reference.source_record_id): reference
                    for reference in criteria_references
                }.values()
            )
            source_unavailable = catalog.source_health.status != "current"
            linked_evaluations = (
                await evaluate_linked_criteria_references(
                    submission,
                    listing.listing_id,
                    criteria_references,
                    listing.related_program_ids,
                    catalogs,
                )
                if not source_unavailable
                else [
                    {
                        "listing_id": listing.listing_id,
                        "criteria_program_id": reference.program_id,
                        "source_record_id": reference.source_record_id,
                        "status": "source_unavailable",
                        "reason_code": "linked_listing_source_not_current",
                        "evaluation_performed": False,
                        "next_action": "refresh_source",
                    }
                    for reference in criteria_references
                ]
            )
            evaluated_references = [
                item
                for item in linked_evaluations
                if item.get("evaluation_performed") is True
            ]
            if evaluated_references:
                return attach_projection(
                    {
                        "status": (
                            "ok"
                            if all(
                                item.get("evaluation_performed") is True
                                for item in linked_evaluations
                            )
                            else "partial"
                        ),
                        "tenant_id": tenant_id,
                        "program_id": program_id,
                        "listing_id": listing.listing_id,
                        "title": listing.title,
                        "agency": listing.agency,
                        "record_kind": listing.record_kind,
                        "jurisdiction": listing.jurisdiction.model_dump(mode="json"),
                        "source_reference": listing.source.model_dump(mode="json"),
                        "source_health": catalog.source_health.model_dump(mode="json"),
                        "criteria_status": "active_rules_evaluated",
                        "criteria_reference_status": (
                            "linked_active_ruleset"
                            if all(
                                item.get("evaluation_scope")
                                == "persisted_program_ruleset"
                                for item in linked_evaluations
                            )
                            else "linked_active_criteria"
                            if all(
                                item.get("evaluation_performed") is True
                                for item in linked_evaluations
                            )
                            else "partially_evaluated"
                        ),
                        "criteria_references": [
                            reference.model_dump(mode="json")
                            for reference in criteria_references
                        ],
                        "criteria_evaluations": linked_evaluations,
                        "evaluation_performed": True,
                        "official_eligibility_determination": False,
                    },
                    projection,
                )
            if not source_unavailable:
                source_ids = sorted(
                    {
                        listing.source.source_id,
                        *(reference.source_id for reference in criteria_references),
                    }
                )
                return attach_projection(
                    {
                        "status": "mapping_required",
                        "reason_code": "criteria_mapping_required",
                        "tenant_id": tenant_id,
                        "program_id": program_id,
                        "listing_id": listing.listing_id,
                        "title": listing.title,
                        "source_reference": listing.source.model_dump(mode="json"),
                        "source_health": catalog.source_health.model_dump(mode="json"),
                        "criteria_status": "not_assessed",
                        "criteria_reference_status": (
                            "linked_conflict"
                            if any(
                                item.get("status") == "criteria_link_conflict"
                                for item in linked_evaluations
                            )
                            else "linked_review_only"
                            if criteria_references
                            else "no_linked_criteria_reference"
                        ),
                        "criteria_references": [
                            reference.model_dump(mode="json")
                            for reference in criteria_references
                        ],
                        "criteria_evaluations": linked_evaluations,
                        "unavailable_sources": unavailable_sources,
                        "catalog_read_failures": catalog_read_failures,
                        "evaluation_performed": False,
                        "official_eligibility_determination": False,
                        "next_action": "prepare_criteria_mapping_from_source",
                        "next_step": CriteriaMappingWorkflowAction(
                            program_id=program_id, source_ids=source_ids
                        ).model_dump(mode="json"),
                    },
                    projection,
                )
            criteria_source_unavailable = any(
                item.get("status") == "source_unavailable"
                for item in linked_evaluations
            )
            return attach_projection(
                {
                    "status": (
                        "source_unavailable"
                        if source_unavailable or criteria_source_unavailable
                        else "mapping_required"
                    ),
                    "reason_code": (
                        "source_fetch_unavailable"
                        if source_unavailable or criteria_source_unavailable
                        else "criteria_mapping_required"
                    ),
                    "tenant_id": tenant_id,
                    "program_id": program_id,
                    "listing_id": listing.listing_id,
                    "title": listing.title,
                    "agency": listing.agency,
                    "record_kind": listing.record_kind,
                    "jurisdiction": listing.jurisdiction.model_dump(mode="json"),
                    "source_reference": listing.source.model_dump(mode="json"),
                    "source_health": catalog.source_health.model_dump(mode="json"),
                    "criteria_status": "not_assessed",
                    "criteria_reference_status": (
                        "linked_review_only" if criteria_references else "not_linked"
                    ),
                    "criteria_references": [
                        reference.model_dump(mode="json")
                        for reference in sorted(
                            criteria_references,
                            key=lambda item: (item.dataset_id, item.source_record_id),
                        )
                    ],
                    "criteria_evaluations": linked_evaluations,
                    "unavailable_sources": sorted(unavailable_sources),
                    "catalog_read_failures": [
                        item.model_dump(mode="json") for item in catalog_read_failures
                    ],
                    "next_action": (
                        "retry_source_fetch"
                        if source_unavailable
                        else "refresh_criteria_source"
                        if criteria_source_unavailable
                        else "apply_existing_criteria_mapping"
                        if criteria_references
                        else "prepare_criteria_mapping_from_source"
                    ),
                    "evaluation_performed": False,
                    "official_eligibility_determination": False,
                },
                projection,
            )

        if not matching_programs and catalog_read_failures:
            return {
                "status": "catalog_read_incomplete",
                "reason_code": "catalog_read_failed",
                "tenant_id": tenant_id,
                "program_id": program_id,
                "catalog_read_failures": [
                    item.model_dump(mode="json") for item in catalog_read_failures
                ],
                "next_action": "retry_catalog_read",
                "evaluation_performed": False,
            }
        if not matching_programs:
            source_unavailable = sorted(
                {
                    catalog.source_id
                    for catalog in catalogs
                    if catalog.source_health.status == "unavailable"
                }
            )
            if source_unavailable:
                return {
                    "status": "source_unavailable",
                    "reason_code": "source_fetch_unavailable",
                    "tenant_id": tenant_id,
                    "program_id": program_id,
                    "unavailable_sources": source_unavailable,
                    "next_action": "retry_source_fetch_or_search_inventory",
                    "evaluation_performed": False,
                }
            return {
                "status": "record_absent",
                "reason_code": "record_absent_from_readable_catalog",
                "tenant_id": tenant_id,
                "program_id": program_id,
                "next_action": "search_program_inventory",
                "evaluation_performed": False,
            }
        if len(matching_programs) > 1:
            return {
                "status": "invalid_catalog",
                "tenant_id": tenant_id,
                "program_id": program_id,
                "error": "Program ID is ambiguous across configured catalogs.",
                "evaluation_performed": False,
            }

        program = matching_programs[0]
        if criteria_ruleset_repository is not None:
            persisted_evaluation = await asyncio.to_thread(
                evaluate_active_criteria_ruleset,
                program_id,
                submission,
                criteria_ruleset_repository,
            )
            persisted_sources_match_program = _rule_sources_match_program_sources(
                persisted_evaluation.get("source_references"), program.sources
            )
            if (
                persisted_evaluation.get("evaluation_performed") is True
                and not persisted_sources_match_program
            ):
                return attach_projection(
                    {
                        "status": "criteria_link_conflict",
                        "reason_code": (
                            "persisted_ruleset_source_does_not_match_catalog_program"
                        ),
                        "tenant_id": tenant_id,
                        "program_id": program_id,
                        "evaluation_performed": False,
                        "official_eligibility_determination": False,
                        "next_action": "review_criteria_program_source_link",
                    },
                    projection,
                )
            if persisted_sources_match_program:
                criteria_references = [
                    reference
                    for catalog in catalogs
                    for reference in catalog.criteria_references
                    if reference.program_id == program_id
                ]
                active_sources = persisted_evaluation.get("source_references")
                matching_reference = next(
                    (
                        reference
                        for reference in criteria_references
                        if isinstance(active_sources, list)
                        and any(
                            isinstance(source, dict)
                            and source.get("source_id") == reference.source_id
                            and source.get("url") == str(reference.source_url)
                            for source in active_sources
                        )
                    ),
                    None,
                )
                persisted_result = {
                    **persisted_evaluation,
                    "tenant_id": tenant_id,
                    "program_id": program_id,
                    "criteria_reference_status": "linked_active_ruleset",
                    "official_eligibility_determination": False,
                }
                if persisted_evaluation.get("evaluation_performed") is True:
                    persisted_result["status"] = "ok"
                    persisted_result["criteria_references"] = [
                        reference.model_dump(mode="json")
                        for reference in criteria_references
                    ]
                    persisted_result["evaluation_performed"] = True
                if matching_reference is not None:
                    persisted_result["criteria_reference"] = (
                        matching_reference.model_dump(mode="json")
                    )
                return attach_projection(
                    persisted_result,
                    projection,
                )
        try:
            result = evaluate_program_criteria(submission, program)
        except UnsupportedCriterionError:
            return {
                "status": "unsupported_criterion",
                "reason_code": "rule_operator_or_attribute_unsupported",
                "tenant_id": tenant_id,
                "program_id": program_id,
                "catalog_version": program.catalog_version,
                "source_references": [
                    source.model_dump(mode="json") for source in program.sources
                ],
                "next_action": "review_rule_mapping",
                "evaluation_performed": False,
            }
        except ValueError:
            return {
                "status": "invalid_rule",
                "reason_code": "rule_validation_failed",
                "tenant_id": tenant_id,
                "program_id": program_id,
                "catalog_version": program.catalog_version,
                "source_references": [
                    source.model_dump(mode="json") for source in program.sources
                ],
                "next_action": "correct_rule_record",
                "evaluation_performed": False,
            }
        return attach_projection(
            {
                "status": "ok",
                "tenant_id": tenant_id,
                "evaluation_performed": True,
                "evaluation_scope": "mapped_criteria_only",
                **result.model_dump(mode="json"),
            },
            projection,
        )

    @app.tool(name="get_program_results")
    async def get_program_results_tool(
        profile_payload: dict[str, Any] | None = None,
        ambiguous_attributes: list[str] | None = None,
        profile_reference: dict[str, str] | None = None,
        shared_profile: dict[str, Any] | None = None,
        navigator: str | None = None,
    ) -> dict[str, object]:
        """Return ordered source-backed program details and criterion evidence.

        Uses only the configured approved catalogs. Missing application details,
        source gaps, and non-current freshness remain explicit; ordering is a
        stable program-ID fallback, not a score or eligibility probability.
        This read-only operation does not determine official eligibility.
        """
        profile_payload, projection, input_error = await resolve_profile_input(
            profile_payload, profile_reference, shared_profile, navigator
        )
        if input_error:
            return {
                "status": input_error,
                "tenant_id": tenant_id,
                "evaluation_performed": False,
            }
        try:
            submission = ProfileSubmission.model_validate(
                {
                    "profile": profile_payload,
                    "ambiguous_attributes": ambiguous_attributes or [],
                }
            )
            scope = DiscoveryScope(tenant_id=tenant_id)
        except (ValueError, TypeError):
            return {
                "status": "invalid_request",
                "tenant_id": tenant_id,
                "error": "Profile is invalid.",
                "evaluation_performed": False,
            }

        try:
            (
                catalogs,
                unavailable_sources,
                catalog_read_failures,
            ) = await load_shared_program_catalogs()
        except StorageUnavailable:
            return {
                "status": "unavailable",
                "tenant_id": tenant_id,
                "error": "Shared program inventory is unavailable.",
                "evaluation_performed": False,
            }

        result = assemble_program_results(
            submission,
            scope,
            catalogs,
            unavailable_source_ids=unavailable_sources,
        )
        response = result.model_dump(mode="json")
        response["catalog_read_failures"] = [
            item.model_dump(mode="json") for item in catalog_read_failures
        ]
        return attach_projection(response, projection)

    async def current_program_result(
        profile_payload: dict[str, Any],
        ambiguous_attributes: list[str] | None,
        program_id: str,
    ) -> tuple[dict[str, object] | None, str | None, list[DatasetCatalog] | None]:
        """Select a current result or public listing from one catalog read."""
        try:
            submission = ProfileSubmission.model_validate(
                {
                    "profile": profile_payload,
                    "ambiguous_attributes": ambiguous_attributes or [],
                }
            )
            scope = DiscoveryScope(tenant_id=tenant_id)
            (
                catalogs,
                unavailable_sources,
                catalog_read_failures,
            ) = await load_shared_program_catalogs()
        except (ValueError, TypeError):
            return None, "Profile is invalid.", None
        except StorageUnavailable:
            return None, "Approved program sources are unavailable.", None

        result = assemble_program_results(
            submission,
            scope,
            catalogs,
            unavailable_source_ids=unavailable_sources,
        ).model_dump(mode="json")
        result["catalog_read_failures"] = [
            item.model_dump(mode="json") for item in catalog_read_failures
        ]
        if result.get("status") not in {"ok", "partial"}:
            return None, "Approved program sources are unavailable.", None
        try:
            programs = result["programs"]
            if not isinstance(programs, list):
                raise ValueError
            matches = [
                item for item in programs if item.get("program_id") == program_id
            ]
        except (KeyError, TypeError, ValueError, AttributeError):
            return None, "Program result is malformed.", None
        if len(matches) > 1:
            return None, "Program is ambiguous in the current catalog.", None

        def linked_catalog_material(
            listing: Any,
            listing_catalog: DatasetCatalog,
        ) -> tuple[
            list[SourceBackedProgramDetail],
            list[SourceReference],
            list[SourceHealth],
            list[ProgramCriteriaReference],
        ]:
            """Resolve details through explicit listing-to-criteria identities."""
            linked_ids = set(listing.related_program_ids)
            linked_ids.add(listing.listing_id)
            details: list[SourceBackedProgramDetail] = []
            sources: list[SourceReference] = []
            health = [listing_catalog.source_health]
            references: list[ProgramCriteriaReference] = []
            seen_details: set[tuple[str, str, str]] = set()

            def add_details(values: list[SourceBackedProgramDetail]) -> None:
                for detail in values:
                    identity = (detail.kind, detail.text, detail.source_id)
                    if identity not in seen_details:
                        seen_details.add(identity)
                        details.append(detail)

            for candidate_catalog in catalogs:
                if candidate_catalog.source_health.status != "current":
                    continue
                for program in candidate_catalog.programs:
                    if program.program_id not in linked_ids:
                        continue
                    listing_source_ids = {listing.source.source_id}
                    linked_sources = [
                        source
                        for source in program.sources
                        if source.source_id in listing_source_ids
                    ]
                    if not linked_sources:
                        continue
                    linked_source_ids = {source.source_id for source in linked_sources}
                    add_details(
                        [
                            detail
                            for detail in program.details
                            if detail.source_id in linked_source_ids
                        ]
                    )
                    sources.extend(linked_sources)
                    if candidate_catalog.source_health not in health:
                        health.append(candidate_catalog.source_health)
                for reference in candidate_catalog.criteria_references:
                    assistance_listing_match = (
                        listing.program_number is not None
                        and listing.program_number in reference.assistance_listing_ids
                        and reference.identity_link_basis == "curated_source_crosswalk"
                    )
                    if (
                        reference.program_id not in linked_ids
                        and not assistance_listing_match
                    ):
                        continue
                    references.append(reference)
                    add_details(reference.details)
                    sources.append(
                        SourceReference(
                            source_id=reference.source_id,
                            source_type="public_source",
                            approval_status=listing.source.approval_status,
                            agency=listing.source.agency,
                            title=reference.title,
                            url=reference.source_url,
                            jurisdiction=listing.jurisdiction,
                            retrieved_at=reference.retrieved_at,
                            publisher_version=reference.source_version,
                        )
                    )
                    if candidate_catalog.source_health not in health:
                        health.append(candidate_catalog.source_health)

            unique_sources = list(
                {
                    (str(source.source_id), str(source.url)): source
                    for source in [listing.source, *sources]
                }.values()
            )
            return details, unique_sources, health, references

        def with_linked_catalog_material(
            entry: dict[str, object],
        ) -> dict[str, object]:
            """Attach only current details tied to one exact linked listing."""
            identities = [
                (catalog, listing)
                for catalog in catalogs
                for listing in catalog.listings
                if program_id
                in {
                    listing.listing_id,
                    listing.program_number,
                    *listing.related_program_ids,
                }
            ]
            if len(identities) != 1:
                return entry
            listing_catalog, listing = identities[0]
            if listing_catalog.source_health.status != "current":
                return entry
            details, sources, health, references = linked_catalog_material(
                listing, listing_catalog
            )
            if not details:
                return entry
            existing_details = entry.get("details", [])
            all_details = [
                SourceBackedProgramDetail.model_validate(item)
                for item in existing_details
                if isinstance(item, (dict, SourceBackedProgramDetail))
            ]
            seen = {(item.kind, item.text, item.source_id) for item in all_details}
            all_details.extend(
                item
                for item in details
                if (item.kind, item.text, item.source_id) not in seen
            )
            available = {item.kind for item in all_details}
            entry["details"] = all_details
            entry["unavailable_detail_kinds"] = [
                kind
                for kind in (
                    "application_step",
                    "form",
                    "required_document",
                    "deadline",
                )
                if kind not in available
            ]
            entry["source_references"] = sources
            entry["source_health"] = health
            entry["listing_id"] = listing.listing_id
            entry["criteria_program_ids"] = listing.related_program_ids
            entry["criteria_references"] = [
                reference.model_dump(mode="json") for reference in references
            ]
            return entry

        if not matches:
            listings = [
                (catalog, listing)
                for catalog in catalogs
                for listing in catalog.listings
                if program_id
                in {
                    listing.listing_id,
                    listing.program_number,
                    *listing.related_program_ids,
                }
            ]
            if len(listings) != 1:
                return (
                    None,
                    "Program is absent or ambiguous in the current catalog.",
                    None,
                )

            catalog, listing = listings[0]
            if catalog.source_health.status != "current":
                return (
                    None,
                    "Program sources are stale, unavailable, or conflicting.",
                    None,
                )
            linked_details, source_references, source_health, linked_criteria = (
                linked_catalog_material(listing, catalog)
            )
            available_detail_kinds = {detail.kind for detail in linked_details}
            if linked_criteria:
                criteria_limitations = [
                    "Linked catalog criteria references are source pointers; any "
                    "active persisted ruleset is evaluated separately and is not "
                    "embedded in this requirements package."
                ]
            else:
                criteria_limitations = [
                    "No approved machine-evaluable criteria are linked to this public "
                    "listing; no eligibility scoring was performed."
                ]
            fallback = ProgramResultEntry(
                status="current",
                program_id=listing.listing_id,
                navigator=submission.profile.navigator,
                title=listing.title,
                description=listing.summary or listing.source_notice or listing.title,
                catalog_version=(
                    listing.source.publisher_version
                    or f"{catalog.dataset_id}:{listing.source.retrieved_at.isoformat()}"
                ),
                jurisdiction=listing.jurisdiction,
                criteria_evidence=[],
                comparison=ProgramComparisonSummary(
                    status="not_evaluated",
                    matched_units=0,
                    evaluable_units=0,
                    unknown_units=0,
                    not_applicable_units=0,
                    matched_fraction=None,
                    explanation=(
                        "This public listing has no active mapped criteria. "
                        "No eligibility comparison was performed."
                    ),
                ),
                details=linked_details,
                unavailable_detail_kinds=[
                    kind
                    for kind in (
                        "application_step",
                        "form",
                        "required_document",
                        "deadline",
                    )
                    if kind not in available_detail_kinds
                ],
                source_references=source_references,
                source_health=source_health,
                source_limitations=[
                    *criteria_limitations,
                    (
                        "The linked catalog criteria record includes partial "
                        "source-backed application details; confirm current "
                        "program-specific requirements with the agency."
                        if linked_details
                        else "The shared catalog does not publish source-backed "
                        "documents, forms, deadlines, or procedural steps for "
                        "this listing."
                    ),
                ],
                evidence_completeness="incomplete_for_cataloged_criteria",
                evaluation_performed=False,
            )
            fallback_result = fallback.model_dump(mode="json")
            fallback_result["listing_id"] = listing.listing_id
            fallback_result["criteria_program_ids"] = listing.related_program_ids
            fallback_result["criteria_references"] = [
                reference.model_dump(mode="json") for reference in linked_criteria
            ]
            return with_linked_catalog_material(fallback_result), None, catalogs
        if matches[0].get("status") != "current":
            return None, "Program sources are stale, unavailable, or conflicting.", None
        return with_linked_catalog_material(matches[0]), None, catalogs

    async def evaluate_linked_criteria_references(
        submission: ProfileSubmission,
        listing_id: str,
        references: list[ProgramCriteriaReference],
        linked_program_ids: list[str],
        catalogs: list[DatasetCatalog],
    ) -> list[dict[str, object]]:
        """Evaluate linked catalog criteria or persisted rulesets, preserving IDs."""
        criteria_program_ids = set(linked_program_ids)
        criteria_program_ids.update(reference.program_id for reference in references)
        if not criteria_program_ids:
            return []
        evaluations: list[dict[str, object]] = []
        references_by_program: dict[str, list[ProgramCriteriaReference]] = {}
        for reference in references:
            references_by_program.setdefault(reference.program_id, []).append(reference)
        for criteria_program_id in sorted(criteria_program_ids):
            linked_references = references_by_program.get(criteria_program_id, [])
            matching_program_catalogs = [
                catalog
                for catalog in catalogs
                if any(
                    program.program_id == criteria_program_id
                    for program in catalog.programs
                )
            ]
            matching_programs = merge_same_program_records(
                [
                    program
                    for catalog in matching_program_catalogs
                    for program in catalog.programs
                    if program.program_id == criteria_program_id
                ]
            )
            if len(matching_programs) == 1:
                criteria_catalog = matching_program_catalogs[0]
                if criteria_catalog.source_health.status != "current":
                    evaluation = {
                        "status": "source_unavailable",
                        "reason_code": "linked_criteria_source_not_current",
                        "program_id": criteria_program_id,
                        "evaluation_performed": False,
                        "next_action": "refresh_criteria_source",
                    }
                    # A known profile location outside the program's jurisdiction
                    # is not applicable whatever the criteria source health, so
                    # keep that evidence in the saved package. The criteria
                    # themselves are still not evaluated.
                    mismatch_evidence = jurisdiction_mismatch_evidence(
                        submission, matching_programs[0]
                    )
                    if mismatch_evidence:
                        evaluation["criteria_evidence"] = mismatch_evidence
                else:
                    try:
                        result = evaluate_program_criteria(
                            submission, matching_programs[0]
                        )
                        evaluation = {
                            "status": "ok",
                            **result.model_dump(mode="json"),
                            "evaluation_scope": "mapped_criteria_only",
                            "evaluation_performed": True,
                            "official_eligibility_determination": False,
                        }
                        if linked_references:
                            if not _rule_sources_match_criteria_references(
                                evaluation.get("source_references"),
                                linked_references,
                            ):
                                evaluation = {
                                    "status": "criteria_link_conflict",
                                    "reason_code": (
                                        "persisted_ruleset_source_does_not_match_"
                                        "linked_reference"
                                    ),
                                    "program_id": criteria_program_id,
                                    "evaluation_performed": False,
                                    "next_action": (
                                        "review_criteria_program_source_link"
                                    ),
                                }
                    except UnsupportedCriterionError:
                        evaluation = {
                            "status": "unsupported_criterion",
                            "reason_code": "rule_operator_or_attribute_unsupported",
                            "program_id": criteria_program_id,
                            "evaluation_performed": False,
                            "next_action": "review_rule_mapping",
                        }
                    except ValueError:
                        evaluation = {
                            "status": "invalid_rule",
                            "reason_code": "rule_validation_failed",
                            "program_id": criteria_program_id,
                            "evaluation_performed": False,
                            "next_action": "correct_rule_record",
                        }
            elif len(matching_programs) > 1:
                evaluation = {
                    "status": "invalid_catalog",
                    "reason_code": "criteria_program_identity_ambiguous",
                    "program_id": criteria_program_id,
                    "evaluation_performed": False,
                    "next_action": "resolve_duplicate_criteria_program_identity",
                }
            else:
                evaluation = (
                    await asyncio.to_thread(
                        evaluate_active_criteria_ruleset,
                        criteria_program_id,
                        submission,
                        criteria_ruleset_repository,
                    )
                    if criteria_ruleset_repository is not None
                    else {
                        "status": "mapping_required",
                        "reason_code": "criteria_mapping_required",
                        "program_id": criteria_program_id,
                        "evaluation_performed": False,
                        "next_action": "prepare_criteria_mapping_from_source",
                    }
                )
            if (
                not matching_programs
                and linked_references
                and evaluation.get("evaluation_performed") is True
            ):
                if not _rule_sources_match_criteria_references(
                    evaluation.get("source_references"), linked_references
                ):
                    evaluations.append(
                        {
                            "listing_id": listing_id,
                            "criteria_program_id": criteria_program_id,
                            "source_record_ids": sorted(
                                reference.source_record_id
                                for reference in linked_references
                            ),
                            "status": "criteria_link_conflict",
                            "reason_code": (
                                "persisted_ruleset_source_does_not_match_linked_reference"
                            ),
                            "evaluation_performed": False,
                            "next_action": "review_criteria_program_source_link",
                        }
                    )
                    continue
            evaluations.append(
                {
                    "listing_id": listing_id,
                    "criteria_program_id": criteria_program_id,
                    "source_record_ids": sorted(
                        reference.source_record_id for reference in linked_references
                    ),
                    **evaluation,
                }
            )
        return evaluations

    async def requirements_package_evaluation(
        raw_entry: dict[str, object],
        catalogs: list[DatasetCatalog] | None,
        profile_payload: dict[str, Any],
        ambiguous_attributes: list[str] | None,
        requested_program_id: str | None = None,
    ) -> tuple[ProgramResultEntry, dict[str, object], list[dict[str, object]]]:
        """Build the same persisted-criteria evaluation for preview and write."""
        entry = ProgramResultEntry.model_validate(
            {
                key: value
                for key, value in raw_entry.items()
                if key in ProgramResultEntry.model_fields
            }
        )
        submission = ProfileSubmission.model_validate(
            {
                "profile": profile_payload,
                "ambiguous_attributes": ambiguous_attributes or [],
            }
        )
        criteria_evaluations: list[dict[str, object]] = []
        persisted_evaluation: dict[str, object] | None = None
        references = raw_entry.get("criteria_references", [])
        if entry.evaluation_performed:
            matching_programs = merge_same_program_records(
                [
                    program
                    for catalog in catalogs or []
                    for program in catalog.programs
                    if program.program_id == entry.program_id
                ]
            )
            if len(matching_programs) == 1:
                if criteria_ruleset_repository is not None:
                    active_evaluation = await asyncio.to_thread(
                        evaluate_active_criteria_ruleset,
                        entry.program_id,
                        submission,
                        criteria_ruleset_repository,
                    )
                    if _rule_sources_match_program_sources(
                        active_evaluation.get("source_references"),
                        matching_programs[0].sources,
                    ):
                        persisted_evaluation = active_evaluation
                evaluation = (
                    persisted_evaluation
                    if persisted_evaluation is not None
                    else evaluate_program_criteria(submission, matching_programs[0])
                )
                evaluation_payload = (
                    evaluation
                    if isinstance(evaluation, dict)
                    else evaluation.model_dump(mode="json")
                )
                criteria_evaluations = [
                    {
                        "listing_id": str(
                            raw_entry.get("listing_id", entry.program_id)
                        ),
                        "criteria_program_id": entry.program_id,
                        "status": evaluation_payload.get("status", "ok"),
                        **evaluation_payload,
                        "evaluation_performed": (
                            evaluation_payload.get("evaluation_performed") is True
                            if persisted_evaluation is not None
                            else True
                        ),
                        "official_eligibility_determination": False,
                    }
                ]
        elif isinstance(references, list):
            typed_references = [
                ProgramCriteriaReference.model_validate(reference)
                for reference in references
                if isinstance(reference, dict)
            ]
            criteria_evaluations = await evaluate_linked_criteria_references(
                submission,
                str(raw_entry.get("listing_id", entry.program_id)),
                typed_references,
                [
                    item
                    for item in raw_entry.get("criteria_program_ids", [])
                    if isinstance(item, str)
                ],
                catalogs or [],
            )
            if not criteria_evaluations and requested_program_id:
                # Result assembly drops programs outside the profile's local
                # jurisdiction, so the package fell back to a bare listing.
                # Keep the not_applicable evidence the evaluator reports.
                excluded = [
                    program
                    for catalog in catalogs or []
                    for program in catalog.programs
                    if program.program_id == requested_program_id
                    and program.data_origin == "public_source"
                ]
                mismatch = (
                    jurisdiction_mismatch_evidence(submission, excluded[0])
                    if len(excluded) == 1
                    else []
                )
                if mismatch:
                    criteria_evaluations = [
                        {
                            "listing_id": str(
                                raw_entry.get("listing_id", entry.program_id)
                            ),
                            "criteria_program_id": requested_program_id,
                            "program_id": requested_program_id,
                            "status": "not_applicable",
                            "reason_code": "jurisdiction_mismatch",
                            "criteria_evidence": mismatch,
                            "evaluation_performed": False,
                            "official_eligibility_determination": False,
                        }
                    ]

        package_evaluation: dict[str, object] = {
            key: raw_entry[key]
            for key in (
                "evaluation_performed",
                "evaluation_scope",
                "criteria_evidence",
                "criteria_evaluations",
                "comparison",
                "coverage",
                "evidence_completeness",
            )
            if key in raw_entry
        }
        if persisted_evaluation is not None:
            for key in (
                "evaluation_performed",
                "evaluation_scope",
                "criteria_evidence",
                "logic_groups",
                "criteria_package_id",
                "criteria_content_sha256",
                "source_references",
                "coverage",
                "criteria_status",
                "reason_code",
                "next_action",
            ):
                if key == "criteria_evidence":
                    package_evaluation[key] = persisted_evaluation.get(key, [])
                elif key in persisted_evaluation:
                    package_evaluation[key] = persisted_evaluation[key]
        if criteria_evaluations:
            linked_evaluation_performed = any(
                item.get("evaluation_performed") is True
                for item in criteria_evaluations
            )
            package_evaluation["criteria_evaluations"] = criteria_evaluations
            package_evaluation["evaluation_performed"] = all(
                item.get("evaluation_performed") is True
                for item in criteria_evaluations
            )
            package_evaluation["official_eligibility_determination"] = False
            package_evaluation.pop("comparison", None)
            if linked_evaluation_performed:
                stale_criteria_limitations = (
                    "no approved machine-evaluable criteria are linked to this "
                    "public listing",
                    "active persisted ruleset is evaluated separately and is "
                    "not embedded",
                )
                entry = entry.model_copy(
                    update={
                        "source_limitations": [
                            limitation
                            for limitation in entry.source_limitations
                            if not any(
                                marker in limitation.lower()
                                for marker in stale_criteria_limitations
                            )
                        ]
                    }
                )
        return entry, package_evaluation, criteria_evaluations

    @app.tool(
        name="preview_requirements_package",
        annotations={
            "title": "Preview Eligibility Requirements Package",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def preview_requirements_package_tool(
        program_id: str,
        profile_payload: dict[str, Any] | None = None,
        ambiguous_attributes: list[str] | None = None,
        profile_reference: dict[str, str] | None = None,
        shared_profile: dict[str, Any] | None = None,
        navigator: str | None = None,
    ) -> dict[str, object]:
        """Preview a source-grounded requirements packet without writing it."""
        profile_payload, projection, input_error = await resolve_profile_input(
            profile_payload, profile_reference, shared_profile, navigator
        )
        if input_error:
            return {"status": input_error, "written": False}
        raw_entry, error, catalogs = await current_program_result(
            profile_payload, ambiguous_attributes, program_id
        )
        if error:
            return {"status": "unavailable", "error": error, "written": False}
        try:
            (
                entry,
                package_evaluation,
                criteria_evaluations,
            ) = await requirements_package_evaluation(
                raw_entry,
                catalogs,
                profile_payload,
                ambiguous_attributes,
                program_id,
            )
            package = build_requirements_package(
                entry,
                tenant_id=tenant_id,
                cluster_id=cluster_id,
                pattern_version=pattern_version,
                profile_snapshot=ProfileSubmission.model_validate(
                    {
                        "profile": profile_payload,
                        "ambiguous_attributes": ambiguous_attributes or [],
                    }
                ).profile.model_dump(mode="json"),
                evaluation=package_evaluation,
            )
        except (ValueError, TypeError):
            return {
                "status": "invalid_program_result",
                "error": "Current program result does not meet the package contract.",
                "written": False,
            }
        return attach_projection(
            {
                "status": "preview",
                "written": False,
                "package": package.model_dump(mode="json"),
                "report_markdown": render_requirements_report(
                    package.model_dump(mode="json")
                ),
                "criteria_evaluations": criteria_evaluations,
            },
            projection,
        )

    @app.tool(
        name="write_requirements_package",
        annotations={
            "title": "Save Eligibility Requirements Package",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def write_requirements_package_tool(
        program_id: str,
        profile_payload: dict[str, Any] | None = None,
        ambiguous_attributes: list[str] | None = None,
        profile_reference: dict[str, str] | None = None,
        shared_profile: dict[str, Any] | None = None,
        navigator: str | None = None,
    ) -> dict[str, object]:
        """Write the requirements packet authorized by the screening request."""
        profile_payload, projection, input_error = await resolve_profile_input(
            profile_payload, profile_reference, shared_profile, navigator
        )
        if input_error:
            return {"status": input_error, "written": False}
        writer = getattr(catalog_repository, "write_requirements_package", None)
        reader = getattr(catalog_repository, "read_requirements_package", None)
        if not callable(writer) or not callable(reader):
            return {
                "status": "unavailable",
                "error": "Requirements package save and readback are not configured.",
                "written": False,
            }
        raw_entry, error, catalogs = await current_program_result(
            profile_payload, ambiguous_attributes, program_id
        )
        if error:
            return {"status": "unavailable", "error": error, "written": False}
        try:
            (
                entry,
                package_evaluation,
                _criteria_evaluations,
            ) = await requirements_package_evaluation(
                raw_entry,
                catalogs,
                profile_payload,
                ambiguous_attributes,
                program_id,
            )
            package = build_requirements_package(
                entry,
                tenant_id=tenant_id,
                cluster_id=cluster_id,
                pattern_version=pattern_version,
                profile_snapshot=ProfileSubmission.model_validate(
                    {
                        "profile": profile_payload,
                        "ambiguous_attributes": ambiguous_attributes or [],
                    }
                ).profile.model_dump(mode="json"),
                evaluation={
                    **package_evaluation,
                },
            )
            package_payload = package.model_dump(mode="json")
        except (ValueError, TypeError):
            return {
                "status": "write_failed",
                "reason_code": "requirements_package_invalid",
                "error": "Requirements package write was rejected or unavailable.",
                "written": False,
            }
        try:
            receipt = await asyncio.to_thread(writer, package_payload)
        except StorageUnavailable:
            return {
                "status": "write_failed",
                "reason_code": "requirements_package_store_unavailable",
                "error": "Requirements package write was rejected or unavailable.",
                "written": False,
            }
        except (ValueError, TypeError):
            return {
                "status": "write_failed",
                "reason_code": "requirements_package_store_rejected",
                "error": "Requirements package write was rejected or unavailable.",
                "written": False,
            }
        try:
            readback = await asyncio.to_thread(reader, package.package_id)
        except (StorageUnavailable, ValueError, TypeError):
            return attach_projection(
                {
                    "status": "readback_failed",
                    "reason_code": "requirements_package_readback_unavailable",
                    "written": True,
                    "readback_verified": False,
                    "package_id": package.package_id,
                    "receipt": receipt,
                    "next_action": "retry_package_readback",
                },
                projection,
            )
        if not _requirements_package_readback_matches(
            package_payload,
            readback,
            allow_append_only_enrichment=(
                isinstance(receipt, dict) and receipt.get("created") is False
            ),
        ):
            return attach_projection(
                {
                    "status": "readback_failed",
                    "reason_code": "requirements_package_readback_mismatch",
                    "written": True,
                    "readback_verified": False,
                    "package_id": package.package_id,
                    "receipt": receipt,
                    "package": readback,
                    "next_action": "inspect_package_readback_mismatch",
                },
                projection,
            )
        return attach_projection(
            {
                **receipt,
                "status": "written",
                "written": True,
                "readback_verified": True,
                "package_id": package.package_id,
                "package": readback,
                "report_markdown": render_requirements_report(readback),
            },
            projection,
        )


def _requirements_package_readback_matches(
    expected: dict[str, object],
    readback: dict[str, object] | None,
    *,
    allow_append_only_enrichment: bool = False,
) -> bool:
    """Verify base content and, on an exact replay, additive package enrichment."""
    if not isinstance(readback, dict):
        return False
    try:
        expected_package = RequirementsPackage.model_validate(expected)
        actual_package = RequirementsPackage.model_validate(readback)
    except (TypeError, ValueError):
        return False
    expected_data = expected_package.model_dump(mode="json", exclude={"created_at"})
    actual_data = actual_package.model_dump(mode="json", exclude={"created_at"})
    if expected_data == actual_data:
        return True
    if not allow_append_only_enrichment:
        return False

    # An exact immutable write replay can return the latest package revision,
    # which may contain source-cited details appended after the base write.
    # Accept only the enrichment contract's additive fields; all base fields
    # must still match exactly after projecting those additions away.
    for key in ("procedural_steps", "forms", "required_documents", "deadlines"):
        expected_items = expected_data.get(key)
        actual_items = actual_data.get(key)
        if (
            not isinstance(expected_items, list)
            or not isinstance(actual_items, list)
            or actual_items[: len(expected_items)] != expected_items
        ):
            return False
        actual_data[key] = expected_items

    expected_unavailable = expected_data.get("unavailable_detail_kinds")
    actual_unavailable = actual_data.get("unavailable_detail_kinds")
    if not isinstance(expected_unavailable, list) or not isinstance(
        actual_unavailable, list
    ):
        return False
    remaining = iter(expected_unavailable)
    if not all(
        any(candidate == item for candidate in remaining) for item in actual_unavailable
    ):
        return False
    actual_data["unavailable_detail_kinds"] = expected_unavailable

    expected_evaluation = expected_data.get("evaluation")
    actual_evaluation = actual_data.get("evaluation")
    if not isinstance(expected_evaluation, dict) or not isinstance(
        actual_evaluation, dict
    ):
        return False
    citation_key = "requirements_detail_citations"
    expected_citations = expected_evaluation.get(citation_key, [])
    actual_citations = actual_evaluation.get(citation_key, [])
    if (
        not isinstance(expected_citations, list)
        or not isinstance(actual_citations, list)
        or actual_citations[: len(expected_citations)] != expected_citations
    ):
        return False
    if citation_key in expected_evaluation:
        actual_evaluation[citation_key] = expected_citations
    else:
        actual_evaluation.pop(citation_key, None)
    return expected_data == actual_data
