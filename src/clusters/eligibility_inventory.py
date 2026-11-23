"""Read-only Eligibility cluster inventory and external search tools."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

from clusters.eligibility_criteria_persistence import read_active_criteria_readiness
from clusters.eligibility_discovery import (
    CatalogReadFailure,
    DatasetCatalog,
    approved_source_specs,
    dataset_catalog_from_payload,
    search_cluster_program_inventory,
)
from clusters.eligibility_hrsa_source import (
    SOURCE_ID as HRSA_SOURCE_ID,
)
from clusters.eligibility_hrsa_source import (
    HrsaSourceUnavailable,
    query_hrsa_locator,
)
from clusters.eligibility_ingest import SourceSpec
from clusters.eligibility_persistence import HttpCriteriaRulesetRepository
from clusters.eligibility_public_search import (
    PublicSearchPolicy,
    PublicSearchProvider,
    register_external_search_tool,
)
from clusters.eligibility_source_reader import register_public_source_page_reader
from clusters.eligibility_versions import (
    ACTIVE_RULESET_READINESS_VERSION,
    INVENTORY_RECOVERY_VERSION,
    supports_eligibility_feature,
)
from example_server.request_context import MCP_CORRELATION_ID
from example_server.storage import CatalogRepository, StorageUnavailable

logger = logging.getLogger(__name__)

BOUNDED_INVENTORY_VERSION = "0.1.8"
PROGRAM_CANDIDATE_VERSION = "0.1.9"
PUBLIC_CATALOG_INVENTORY_VERSION = "0.1.10"
CRITERIA_EXTRACTION_VERSION = "0.1.11"
CRITERIA_COMPOUND_VERSION = "0.1.12"
CRITERIA_NORMALIZED_V1_VERSION = "0.1.13"
CRITERIA_NORMALIZED_VERSION = "0.1.14"
CRITERIA_REFERENCE_VERSION = "0.1.15"
CRITERIA_PROFILE_SCHEMA_V2_VERSION = "0.1.16"
CRITERIA_PROFILE_READINESS_VERSION = "0.1.17"
PUBLIC_SOURCE_PAGE_READ_VERSION = "0.1.21"
COMPACT_INVENTORY_VERSION = "0.1.22"
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 25


@contextmanager
def _mcp_correlation_context() -> Iterator[str | None]:
    """Propagate only a valid UUID correlation header into Data API reads."""
    try:
        raw_correlation_id = get_http_request().headers.get("X-MCP-Correlation-Id")
    except RuntimeError:
        raw_correlation_id = None
    try:
        correlation_id = (
            str(uuid.UUID(raw_correlation_id)) if raw_correlation_id else None
        )
    except (ValueError, AttributeError):
        correlation_id = None
    token = MCP_CORRELATION_ID.set(correlation_id)
    try:
        yield correlation_id
    finally:
        MCP_CORRELATION_ID.reset(token)


def register_search_program_inventory(
    app: FastMCP,
    *,
    cluster_id: str,
    pattern_version: str,
    catalog_repository: CatalogRepository | None,
    public_search_provider: PublicSearchProvider | None = None,
    public_search_policy: PublicSearchPolicy | None = None,
    criteria_ruleset_repository: HttpCriteriaRulesetRepository | None = None,
) -> None:
    """Expose shared catalog search; latest contract also provides paging/leads."""
    if supports_eligibility_feature(pattern_version, BOUNDED_INVENTORY_VERSION):

        @app.tool(name="search_program_inventory")
        async def search_program_inventory_bounded_tool(
            need_query: str = "",
            state_code: str | None = None,
            page_size: int = DEFAULT_PAGE_SIZE,
            cursor: str | None = None,
        ) -> dict[str, object]:
            """Search or browse shared public programs with bounded pagination.

            Search first-party shared public listings before invoking external
            search. If a result includes criteria_references, use each dataset_id
            and source_record_id with read_public_catalog_dataset to inspect the
            cited requirement records. References are explicitly linked by stable
            program ID or exact Assistance Listing number and remain review-only.
            `active_ruleset_status` separately reports whether a source-matched
            persisted rule exists; `criteria_status: not_assessed` means no profile
            evaluation occurred in search.
            Leave need_query empty to browse all available candidates, optionally
            narrowed to a state. The page cursor is bound to the query and filters.
            """
            result = await _search_inventory(
                need_query,
                state_code,
                cluster_id=cluster_id,
                pattern_version=pattern_version,
                catalog_repository=catalog_repository,
            )
            response = _paginate_inventory_result(
                result,
                need_query=need_query,
                state_code=state_code,
                page_size=page_size,
                cursor=cursor,
                compact=supports_eligibility_feature(
                    pattern_version, COMPACT_INVENTORY_VERSION
                ),
            )
            if supports_eligibility_feature(
                pattern_version, ACTIVE_RULESET_READINESS_VERSION
            ):
                await _enrich_active_ruleset_readiness(
                    response, criteria_ruleset_repository
                )
            return response

        policy_reader = getattr(
            catalog_repository, "read_active_public_search_policy", None
        )
        register_external_search_tool(
            app,
            provider=public_search_provider,
            policy=public_search_policy,
            policy_reader=(policy_reader if callable(policy_reader) else None),
        )
        return

    @app.tool(name="search_program_inventory")
    async def search_program_inventory_tool(
        need_query: str = "",
        state_code: str | None = None,
    ) -> dict[str, object]:
        """Search or browse active public program listings in shared inventory.

        This uses the cluster's shared catalog of public records and does not
        read a profile, compare criteria, or determine eligibility. No match
        means the current inventory did not find a listing; coverage is partial.
        Results may include criteria-free public listings. Their criteria status
        is explicit and they cannot be treated as an eligibility result. Leave
        need_query empty to browse all candidates, optionally filtering by state.
        """
        result = await _search_inventory(
            need_query,
            state_code,
            cluster_id=cluster_id,
            pattern_version=pattern_version,
            catalog_repository=catalog_repository,
        )
        if supports_eligibility_feature(
            pattern_version, ACTIVE_RULESET_READINESS_VERSION
        ):
            await _enrich_active_ruleset_readiness(result, criteria_ruleset_repository)
        return result


def register_public_catalog_inventory_tools(
    app: FastMCP,
    *,
    catalog_repository: CatalogRepository | None,
    pattern_version: str,
) -> None:
    """Expose paged source inventory and per-dataset record reads to MCP clients."""
    list_inventory = getattr(catalog_repository, "list_public_catalog_inventory", None)
    read_page = getattr(catalog_repository, "read_cluster_public_dataset_page", None)

    @app.tool(name="list_public_catalog_inventory")
    async def list_public_catalog_inventory_tool(
        page_size: int = DEFAULT_PAGE_SIZE,
        cursor: str | None = None,
    ) -> dict[str, object]:
        """List shared public sources and datasets in bounded pages.

        Shows source access, whether normalized records are loaded/searchable,
        criteria coverage, freshness, and a continuation cursor. It does not
        read profiles, change bindings, or ingest data.
        """
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= MAX_PAGE_SIZE
            or (
                cursor is not None
                and (not isinstance(cursor, str) or len(cursor) > 2048)
            )
        ):
            return {"status": "invalid_request"}
        if not callable(list_inventory):
            return {
                "status": "unavailable",
                "reason": "catalog_inventory_reader_unconfigured",
            }
        try:
            result = await asyncio.to_thread(
                list_inventory, limit=page_size, cursor=cursor
            )
        except StorageUnavailable:
            return {"status": "unavailable", "reason": "catalog_inventory_unavailable"}
        return {"status": "ok", **result}

    async def read_public_catalog_dataset_impl(
        dataset_id: str,
        page_size: int = 100,
        offset: int = 0,
        source_record_id: str | None = None,
    ) -> dict[str, object]:
        """Read one page of normalized records after listing the shared inventory.

        Each record carries source URL, schema version, jurisdiction/context,
        provenance, and freshness. Use the returned next_offset to continue.
        """
        if (
            not isinstance(dataset_id, str)
            or not dataset_id
            or len(dataset_id) > 64
            or isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 1000
            or isinstance(offset, bool)
            or not isinstance(offset, int)
            or not 0 <= offset <= 10_000_000
            or (
                source_record_id is not None
                and (
                    not isinstance(source_record_id, str)
                    or not source_record_id
                    or len(source_record_id) > 160
                )
            )
        ):
            return {"status": "invalid_request"}
        if not callable(read_page):
            return {
                "status": "unavailable",
                "reason": "catalog_record_reader_unconfigured",
            }
        try:
            read_started_at = asyncio.get_running_loop().time()
            with _mcp_correlation_context() as correlation_id:
                result = await asyncio.to_thread(
                    read_page,
                    dataset_id,
                    limit=page_size,
                    offset=offset,
                    **(
                        {"source_record_id": source_record_id}
                        if supports_eligibility_feature(
                            pattern_version, COMPACT_INVENTORY_VERSION
                        )
                        and source_record_id is not None
                        else {}
                    ),
                )
        except StorageUnavailable:
            logger.info(
                "eligibility_search_timing correlation_id=%s "
                "stage=dataset_record_read outcome=unavailable elapsed_ms=%.1f",
                correlation_id or "unavailable",
                (asyncio.get_running_loop().time() - read_started_at) * 1000,
            )
            return {
                "status": "unavailable",
                "reason": "catalog_record_page_unavailable",
            }
        logger.info(
            "eligibility_search_timing correlation_id=%s "
            "stage=dataset_record_read outcome=complete elapsed_ms=%.1f",
            correlation_id or "unavailable",
            (asyncio.get_running_loop().time() - read_started_at) * 1000,
        )
        if source_record_id is not None:
            records = result.get("records")
            if (
                not isinstance(records, list)
                or len(records) > 1
                or any(
                    not isinstance(record, Mapping)
                    or record.get("source_record_id") != source_record_id
                    for record in records
                )
            ):
                return {
                    "status": "unavailable",
                    "reason": "catalog_exact_record_read_invalid",
                }
        return {"status": "ok", **result}

    if supports_eligibility_feature(pattern_version, COMPACT_INVENTORY_VERSION):

        @app.tool(name="read_public_catalog_dataset")
        async def read_public_catalog_dataset_tool(
            dataset_id: str,
            page_size: int = 100,
            offset: int = 0,
            source_record_id: str | None = None,
        ) -> dict[str, object]:
            """Read a full record by ID or one bounded normalized dataset page."""
            return await read_public_catalog_dataset_impl(
                dataset_id, page_size, offset, source_record_id
            )

    else:

        @app.tool(name="read_public_catalog_dataset")
        async def read_public_catalog_dataset_tool(
            dataset_id: str,
            page_size: int = 100,
            offset: int = 0,
        ) -> dict[str, object]:
            """Read one bounded normalized record page from a listed dataset."""
            return await read_public_catalog_dataset_impl(
                dataset_id, page_size, offset, None
            )

    if supports_eligibility_feature(pattern_version, PUBLIC_SOURCE_PAGE_READ_VERSION):
        policy_reader = getattr(
            catalog_repository, "read_active_public_search_policy", None
        )
        register_public_source_page_reader(
            app, policy_reader=policy_reader if callable(policy_reader) else None
        )


async def _search_inventory(
    need_query: str,
    state_code: str | None,
    *,
    cluster_id: str,
    pattern_version: str,
    catalog_repository: CatalogRepository | None,
) -> dict[str, object]:
    started_at = asyncio.get_running_loop().time()
    with _mcp_correlation_context() as correlation_id:
        result = await _search_inventory_impl(
            need_query,
            state_code,
            cluster_id=cluster_id,
            pattern_version=pattern_version,
            catalog_repository=catalog_repository,
            correlation_id=correlation_id,
        )
    logger.info(
        "eligibility_search_timing correlation_id=%s stage=inventory_pipeline "
        "outcome=%s elapsed_ms=%.1f",
        correlation_id or "unavailable",
        str(result.get("status", "complete")),
        (asyncio.get_running_loop().time() - started_at) * 1000,
    )
    return result


async def _search_inventory_impl(
    need_query: str,
    state_code: str | None,
    *,
    cluster_id: str,
    pattern_version: str,
    catalog_repository: CatalogRepository | None,
    correlation_id: str | None,
) -> dict[str, object]:
    list_bindings = getattr(catalog_repository, "list_pattern_cluster_bindings", None)
    read_shared = getattr(catalog_repository, "read_cluster_public_dataset", None)
    if not callable(list_bindings) or not callable(read_shared):
        return _unavailable_inventory(
            cluster_id,
            pattern_version,
            "The shared pattern catalog reader is unavailable.",
        )
    try:
        catalog_snapshot = await asyncio.to_thread(list_bindings)
    except StorageUnavailable:
        return _unavailable_inventory(
            cluster_id,
            pattern_version,
            "The shared pattern catalog could not be read.",
        )

    read_from_snapshot = getattr(
        catalog_repository, "read_cluster_public_dataset_from_catalog_snapshot", None
    )

    specs = approved_source_specs()
    if (
        not isinstance(catalog_snapshot, dict)
        or not isinstance(catalog_snapshot.get("bindings"), list)
        or not isinstance(catalog_snapshot.get("truncated"), bool)
        or not isinstance(catalog_snapshot.get("inventory_datasets", []), list)
        or not isinstance(catalog_snapshot.get("inventory_truncated", False), bool)
    ):
        return _unavailable_inventory(
            cluster_id,
            pattern_version,
            "The shared pattern catalog response was invalid.",
        )
    bindings = catalog_snapshot["bindings"]
    inventory_datasets = catalog_snapshot.get("inventory_datasets", [])
    dataset_ids = sorted(
        {
            binding["dataset_id"]
            for binding in bindings
            if isinstance(binding, Mapping)
            and isinstance(binding.get("dataset_id"), str)
        }
    )
    bindings_by_dataset: dict[str, list[dict[str, object]]] = {}
    for binding in bindings:
        if isinstance(binding, dict) and isinstance(binding.get("dataset_id"), str):
            bindings_by_dataset.setdefault(binding["dataset_id"], []).append(binding)

    inventory_by_dataset: dict[str, dict[str, object]] = {}
    for dataset in inventory_datasets:
        if (
            isinstance(dataset, Mapping)
            and isinstance(dataset.get("dataset_id"), str)
            and isinstance(dataset.get("source_id"), str)
        ):
            inventory_by_dataset[dataset["dataset_id"]] = dict(dataset)
    dataset_ids = sorted(set(dataset_ids) | set(inventory_by_dataset))

    catalogs = []
    unavailable_sources: list[str] = []
    catalog_read_failures: list[CatalogReadFailure] = []
    hrsa_failure_code: str | None = None
    hrsa_registered_datasets: list[dict[str, object]] = []
    dataset_reads: list[tuple[str, str, SourceSpec | None]] = []
    for dataset_id in dataset_ids:
        spec = specs.get(dataset_id)
        source_ids = {
            binding.get("source_id")
            for binding in bindings_by_dataset.get(dataset_id, [])
            if isinstance(binding.get("source_id"), str)
        }
        inventory_item = inventory_by_dataset.get(dataset_id)
        if not source_ids and inventory_item is not None:
            source_id = inventory_item.get("source_id")
            if isinstance(source_id, str):
                source_ids = {source_id}
        if len(source_ids) != 1 or (
            spec is not None and source_ids != {spec.source_id}
        ):
            catalog_read_failures.append(
                CatalogReadFailure(
                    dataset_id=dataset_id,
                    source_id=next(iter(source_ids), "unknown"),
                    reason_code="inconsistent_catalog_binding",
                )
            )
            continue
        if source_ids == {HRSA_SOURCE_ID}:
            if inventory_item is not None:
                hrsa_registered_datasets.append(inventory_item)
            continue
        dataset_reads.append((dataset_id, next(iter(source_ids)), spec))

    dataset_read_durations_ms: list[float] = []

    async def read_dataset_catalog(
        dataset_id: str, source_id: str, spec: SourceSpec | None
    ) -> tuple[DatasetCatalog | None, CatalogReadFailure | None]:
        """Read and validate one independent shared dataset."""
        started_at = asyncio.get_running_loop().time()
        try:
            if callable(read_from_snapshot):
                payload = await asyncio.to_thread(
                    read_from_snapshot,
                    dataset_id,
                    catalog_snapshot=catalog_snapshot,
                )
            else:
                payload = await asyncio.to_thread(read_shared, dataset_id)
            if spec is not None:
                return dataset_catalog_from_payload(payload, spec=spec), None
            return (
                dataset_catalog_from_payload(
                    payload,
                    expected_dataset_id=dataset_id,
                    expected_source_id=source_id,
                ),
                None,
            )
        except StorageUnavailable:
            return (
                None,
                CatalogReadFailure(
                    dataset_id=dataset_id,
                    source_id=source_id,
                    reason_code="data_api_read_failed",
                ),
            )
        except (ValueError, TypeError):
            return (
                None,
                CatalogReadFailure(
                    dataset_id=dataset_id,
                    source_id=source_id,
                    reason_code="invalid_catalog_response",
                ),
            )
        finally:
            dataset_read_durations_ms.append(
                max(
                    0.0,
                    (asyncio.get_running_loop().time() - started_at) * 1000,
                )
            )

    dataset_reads_started_at = asyncio.get_running_loop().time()
    dataset_results = []
    for dataset_id, source_id, spec in dataset_reads:
        dataset_results.append(await read_dataset_catalog(dataset_id, source_id, spec))
    for catalog, catalog_read_failure in dataset_results:
        if catalog is not None:
            catalogs.append(catalog)
        if catalog_read_failure is not None:
            catalog_read_failures.append(catalog_read_failure)
    if dataset_reads:
        logger.info(
            "eligibility_search_timing correlation_id=%s "
            "stage=data_api_dataset_reads count=%d "
            "succeeded=%d failed=%d wall_ms=%.1f summed_ms=%.1f max_ms=%.1f",
            correlation_id or "unavailable",
            len(dataset_reads),
            len(catalogs),
            sum(catalog is None for catalog, _ in dataset_results),
            (asyncio.get_running_loop().time() - dataset_reads_started_at) * 1000,
            sum(dataset_read_durations_ms),
            max(dataset_read_durations_ms, default=0.0),
        )

    if hrsa_registered_datasets:
        try:
            hrsa_catalogs, _ = await asyncio.to_thread(
                query_hrsa_locator,
                need_query,
                state_code,
                hrsa_registered_datasets,
            )
            catalogs.extend(hrsa_catalogs)
        except HrsaSourceUnavailable as exc:
            unavailable_sources.append(HRSA_SOURCE_ID)
            hrsa_failure_code = exc.reason_code

    ranking_started_at = asyncio.get_running_loop().time()
    result = search_cluster_program_inventory(
        need_query,
        state_code,
        cluster_id=cluster_id,
        pattern_version=pattern_version,
        catalogs=catalogs,
        bindings=bindings,
        unavailable_source_ids=unavailable_sources,
        catalog_read_failures=catalog_read_failures,
        catalog_truncated=(
            catalog_snapshot["truncated"]
            or catalog_snapshot.get("inventory_truncated", False)
        ),
    )
    logger.info(
        "eligibility_search_timing correlation_id=%s stage=ranking "
        "outcome=complete elapsed_ms=%.1f",
        correlation_id or "unavailable",
        (asyncio.get_running_loop().time() - ranking_started_at) * 1000,
    )
    response = result.model_dump(mode="json")
    if not supports_eligibility_feature(pattern_version, INVENTORY_RECOVERY_VERSION):
        response.pop("relevance_status", None)
        response.pop("weak_match_count", None)
        response.pop("next_action", None)
    if not supports_eligibility_feature(
        pattern_version, ACTIVE_RULESET_READINESS_VERSION
    ):
        for candidate in response.get("candidates", []):
            if isinstance(candidate, dict):
                candidate.pop("active_ruleset_status", None)
                candidate.pop("evaluation_available", None)
    if not supports_eligibility_feature(pattern_version, COMPACT_INVENTORY_VERSION):
        for candidate in response.get("candidates", []):
            if isinstance(candidate, dict):
                candidate.pop("dataset_id", None)
                candidate.pop("source_record_id", None)
    if hrsa_failure_code is not None:
        for source_health in response.get("source_health", []):
            if source_health.get("source_id") == HRSA_SOURCE_ID:
                source_health["detail"] = json.dumps(
                    {
                        "failure_code": hrsa_failure_code,
                        "action": (
                            "Check HRSA registration, partitions, "
                            "and public-source access."
                        ),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
    return response


def _paginate_inventory_result(
    result: dict[str, object],
    *,
    need_query: str,
    state_code: str | None,
    page_size: int,
    cursor: str | None,
    compact: bool = False,
) -> dict[str, object]:
    """Page a deterministic result snapshot with a query-bound opaque cursor."""
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        return result
    if (
        isinstance(page_size, bool)
        or not isinstance(page_size, int)
        or not 1 <= page_size <= MAX_PAGE_SIZE
    ):
        return _invalid_page(result, "Page size must be between 1 and 25.")
    fingerprint = _inventory_fingerprint(need_query, state_code, candidates)
    offset = 0
    if cursor is not None:
        decoded = _decode_inventory_cursor(cursor, fingerprint, page_size)
        if decoded is None:
            return _invalid_page(
                result,
                "The continuation cursor is invalid or belongs to another search.",
            )
        offset = decoded
    page = candidates[offset : offset + page_size]
    has_more = offset + page_size < len(candidates)
    pattern_version = result.get("pattern_version")
    result["contract_version"] = (
        "program-inventory-search-v7"
        if isinstance(pattern_version, str)
        and supports_eligibility_feature(pattern_version, INVENTORY_RECOVERY_VERSION)
        else "program-inventory-search-v6"
        if isinstance(pattern_version, str)
        and supports_eligibility_feature(
            pattern_version, ACTIVE_RULESET_READINESS_VERSION
        )
        else "program-inventory-search-v5"
        if isinstance(pattern_version, str)
        and supports_eligibility_feature(pattern_version, "0.1.29")
        else "program-inventory-search-v4"
        if compact
        else "program-inventory-search-v3"
    )
    result["candidates"] = page
    result["truncated"] = has_more or bool(result.get("catalog_truncated"))
    result["next_cursor"] = (
        _encode_inventory_cursor(fingerprint, page_size, offset + page_size)
        if has_more
        else None
    )
    if compact:
        _compact_inventory_result(result)
    return result


async def _enrich_active_ruleset_readiness(
    result: dict[str, object],
    repository: HttpCriteriaRulesetRepository | None,
) -> None:
    """Attach bounded read-only active-rule status to the returned search page."""
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        return

    semaphore = asyncio.Semaphore(4)

    async def read_one(
        program_id: str, sources: list[dict[str, object]]
    ) -> dict[str, object]:
        if repository is None:
            return {
                "active_ruleset_status": "unavailable",
                "evaluation_available": False,
            }
        async with semaphore:
            state: dict[str, object] = {}
            # "unavailable" is a transient Persistence API failure, not proof
            # that evaluation is impossible; retry once before reporting it.
            for attempt in range(2):
                state = await asyncio.to_thread(
                    read_active_criteria_readiness,
                    program_id,
                    sources,
                    repository,
                )
                if state.get("active_ruleset_status") != "unavailable":
                    break
                if attempt == 0:
                    await asyncio.sleep(0.25)
            return state

    async def enrich(candidate: object) -> None:
        if not isinstance(candidate, dict):
            return
        catalog_criteria_evaluable = (
            candidate.get("record_type") == "eligibility_program"
            and candidate.get("criteria_available") is True
        )
        references = candidate.get("criteria_references")
        references = references if isinstance(references, list) else []
        program_ids = {
            reference["program_id"]
            for reference in references
            if isinstance(reference, dict)
            and isinstance(reference.get("program_id"), str)
        }
        related_program_ids = candidate.get("related_program_ids")
        if isinstance(related_program_ids, list):
            program_ids.update(
                item for item in related_program_ids if isinstance(item, str)
            )
        candidate_program_id = candidate.get("program_id")
        if isinstance(candidate_program_id, str):
            program_ids.add(candidate_program_id)
        if not program_ids:
            candidate["active_ruleset_status"] = "not_linked"
            candidate["evaluation_available"] = catalog_criteria_evaluable
            return

        sources: list[dict[str, object]] = []
        source_items = candidate.get("sources")
        if isinstance(source_items, list):
            sources.extend(item for item in source_items if isinstance(item, dict))
        source = candidate.get("source")
        if isinstance(source, dict):
            sources.append(source)
        sources.extend(
            {
                key: reference[key]
                for key in ("source_id", "source_url")
                if key in reference
            }
            for reference in references
            if isinstance(reference, dict)
        )
        sources.extend(
            link["source"]
            for reference in references
            if isinstance(reference, dict)
            for link in reference.get("criteria_source_links", [])
            if isinstance(link, dict) and isinstance(link.get("source"), dict)
        )
        # Source-reference contracts use `url`; compact criteria references use
        # `source_url`. Normalize them before exact lineage comparison.
        normalized_sources = [
            {
                "source_id": item.get("source_id"),
                "url": item.get("url", item.get("source_url")),
            }
            for item in sources
        ]
        states = await asyncio.gather(
            *(
                read_one(program_id, normalized_sources)
                for program_id in sorted(program_ids)
            )
        )
        statuses = {str(state.get("active_ruleset_status")) for state in states}
        if "active" in statuses:
            status = "active"
        elif "unavailable" in statuses:
            status = "unavailable"
        elif "source_mismatch" in statuses:
            status = "source_mismatch"
        elif "invalid" in statuses:
            status = "invalid"
        else:
            status = "not_found"
        available = any(
            state.get("active_ruleset_status") == "active"
            and state.get("evaluation_available") is True
            for state in states
        )
        candidate["active_ruleset_status"] = status
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
        if len(identities) == 1:
            package_id, content_sha256 = identities.pop()
            candidate["active_ruleset_package_id"] = package_id
            candidate["active_ruleset_content_sha256"] = content_sha256
        candidate["evaluation_available"] = catalog_criteria_evaluable or available
        candidate["criteria_available"] = bool(
            candidate.get("criteria_available") or available
        )

    await asyncio.gather(*(enrich(candidate) for candidate in candidates))


def _compact_inventory_result(result: dict[str, object]) -> None:
    """Return source-linked search summaries and leave full records on demand."""
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        return
    compact_candidates: list[dict[str, object]] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        sources = candidate.get("sources")
        source = sources[0] if isinstance(sources, list) and sources else {}
        source = source if isinstance(source, Mapping) else {}
        references = candidate.get("criteria_references")
        compact_references = []
        if isinstance(references, list):
            for reference in references:
                if isinstance(reference, Mapping):
                    compact_references.append(
                        {
                            key: reference[key]
                            for key in (
                                "program_id",
                                "dataset_id",
                                "source_record_id",
                                "title",
                                "source_id",
                                "source_url",
                                "retrieved_at",
                                "evaluation_available",
                            )
                            if key in reference
                        }
                    )
        row = {
            key: candidate[key]
            for key in (
                "record_type",
                "dataset_id",
                "source_record_id",
                "program_id",
                "listing_id",
                "linked_listing_ids",
                "record_role",
                "listing_kind",
                "program_number",
                "related_program_ids",
                "title",
                "jurisdiction",
                "jurisdiction_match",
                "matching_terms",
                "criteria_available",
                "criteria_status",
                "criteria_reference_status",
                "active_ruleset_status",
                "active_ruleset_package_id",
                "active_ruleset_content_sha256",
                "evaluation_available",
                "listing_status",
            )
            if key in candidate and candidate[key] is not None
        }
        row["description"] = str(candidate.get("description", ""))[:200]
        row["source"] = {
            key: source[key]
            for key in ("source_id", "title", "url", "retrieved_at")
            if key in source
        }
        row["criteria_references"] = compact_references
        compact_candidates.append(row)
    result["candidates"] = compact_candidates
    health = result.get("source_health")
    if isinstance(health, list):
        counts: dict[str, int] = {}
        exceptions = []
        for item in health:
            if not isinstance(item, Mapping):
                continue
            status = str(item.get("status", "unknown"))
            counts[status] = counts.get(status, 0) + 1
            if status in {"stale", "unavailable", "unknown"}:
                exceptions.append(item)
        result["source_health"] = {"counts": counts, "exceptions": exceptions}


def _inventory_fingerprint(
    query: str, state_code: str | None, candidates: list[object]
) -> str:
    material = json.dumps(
        {
            "query": " ".join(query.lower().split()),
            "state_code": state_code.upper() if isinstance(state_code, str) else None,
            "candidates": candidates,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _encode_inventory_cursor(fingerprint: str, page_size: int, offset: int) -> str:
    payload = {"fingerprint": fingerprint, "page_size": page_size, "offset": offset}
    return (
        base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def _decode_inventory_cursor(
    cursor: str, fingerprint: str, page_size: int
) -> int | None:
    if not isinstance(cursor, str) or len(cursor) > 512:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        payload = json.loads(raw)
        offset = payload.get("offset")
        if (
            payload.get("fingerprint") != fingerprint
            or payload.get("page_size") != page_size
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
        ):
            return None
        return offset
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _invalid_page(result: dict[str, object], explanation: str) -> dict[str, object]:
    result.update(
        {
            "contract_version": "program-inventory-search-v3",
            "status": "invalid_request",
            "candidates": [],
            "truncated": False,
            "next_cursor": None,
            "explanation": explanation,
        }
    )
    return result


def _unavailable_inventory(
    cluster_id: str, pattern_version: str, explanation: str
) -> dict[str, object]:
    return {
        "status": "unavailable",
        "cluster_id": cluster_id,
        "pattern_version": pattern_version,
        "candidates": [],
        "source_health": [],
        "unavailable_sources": [],
        "coverage": "unavailable",
        "catalog_truncated": False,
        "evaluation_performed": False,
        "explanation": explanation,
    }
