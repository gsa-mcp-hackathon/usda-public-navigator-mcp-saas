"""Query HRSA's public locator CSV transiently for shared-cluster discovery."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from clusters.eligibility_contracts import (
    Jurisdiction,
    SourceHealth,
    SourceReference,
)
from clusters.eligibility_discovery import (
    DatasetCatalog,
    PublicListingRecord,
    _search_terms,
)

SOURCE_ID = "us.hrsa.health-centers"
MANIFEST_RELATIVE_PATH = Path(
    "configs/eligibility/catalog-seeds/hrsa-health-center-locations.json"
)


def _resolve_manifest_path(module_file: Path = Path(__file__)) -> Path:
    """Find the runtime config from either source checkout or installed package."""
    resolved_file = module_file.resolve()
    for parent in resolved_file.parents:
        candidate = parent / MANIFEST_RELATIVE_PATH
        if candidate.is_file():
            return candidate
    return resolved_file.parents[2] / MANIFEST_RELATIVE_PATH


MANIFEST_PATH = _resolve_manifest_path()
MAX_CSV_BYTES = 20_000_000
MAX_MATCHED_ROWS = 500
HTTP_TIMEOUT = httpx.Timeout(connect=5.0, read=25.0, write=5.0, pool=5.0)
USER_AGENT = "MCP-SaaS-Hackathon/0.1 (public source query)"


class HrsaSourceUnavailable(RuntimeError):
    """HRSA's public locator source could not be validated or read."""

    def __init__(
        self, message: str, *, reason_code: str = "source_unavailable"
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def query_hrsa_locator(
    need_query: str,
    state_code: str | None,
    registered_datasets: list[dict[str, Any]],
    *,
    manifest_path: Path = MANIFEST_PATH,
) -> tuple[list[DatasetCatalog], bool]:
    """Fetch, filter, and normalize bounded HRSA matches without persistence.

    Only datasets already registered as cluster-public HRSA catalog entries are
    returned. Raw CSV bytes and nonmatching rows live only for this request.
    """
    manifest = _load_manifest(manifest_path)
    csv_url = _csv_url(manifest)
    dataset_by_region, unreported_dataset_id = _registered_partitions(
        manifest, registered_datasets
    )
    raw, headers = _fetch_csv(csv_url)
    rows = _parse_csv(raw)
    source_version = _source_version(raw, headers)
    retrieved_at = datetime.now(UTC)
    query_terms = _search_terms(need_query)
    duplicates: dict[str, dict[str, str]] = {}
    duplicate_counts = {"exact_duplicate_skipped": 0, "identity_conflict_skipped": 0}
    seen: dict[str, str] = {}
    matched: list[tuple[int, str, str, dict[str, str]]] = []
    valid_states = set(
        manifest["transform"]["jurisdiction_classification"]["state_codes"]
    )

    for row in rows:
        site_id = _clean(row.get("BPHC Assigned Number"), 128)
        site_name = _clean(row.get("Site Name"), 500)
        if not site_id or not site_name:
            continue
        fingerprint = hashlib.sha256(
            json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if site_id in seen:
            disposition = (
                "exact_duplicate_skipped"
                if seen[site_id] == fingerprint
                else "duplicate_source_identity_conflict_skipped"
            )
            duplicate_counts[
                "exact_duplicate_skipped"
                if disposition == "exact_duplicate_skipped"
                else "identity_conflict_skipped"
            ] += 1
            duplicates.setdefault(
                site_id,
                {
                    "disposition": disposition,
                    "reason": _duplicate_reason(disposition),
                    "existing_reference": f"{SOURCE_ID}:{site_id}",
                },
            )
            continue
        seen[site_id] = fingerprint

        state = _clean(row.get("Site State Abbreviation"), 2).upper()
        if state_code is not None and state != state_code.upper():
            continue
        searchable = " ".join(
            _clean(row.get(field), 800)
            for field in (
                "Site Name",
                "Health Center Name",
                "Health Center Type",
                "Site Address",
                "Site City",
                "Site State Abbreviation",
                "Site Postal Code",
                "Complete County Name",
                "County Equivalent Name",
                "Health Center Service Delivery Site Location Setting Description",
            )
        )
        terms = query_terms & _search_terms(searchable)
        if not terms:
            continue
        region = _clean(row.get("HHS Region Code"), 2)
        dataset_id = dataset_by_region.get(region) if region else unreported_dataset_id
        if dataset_id is None:
            raise HrsaSourceUnavailable(
                "HRSA row geography does not map to a registered dataset partition.",
                reason_code="unmapped_source_geography",
            )
        matched.append((len(terms), site_id, dataset_id, row))

    matched.sort(key=lambda item: (-item[0], item[1]))
    matching_rows_before_cap = len(matched)
    was_truncated = matching_rows_before_cap > MAX_MATCHED_ROWS
    matched = matched[:MAX_MATCHED_ROWS]
    duplicate_detail = {
        "duplicate_rows_skipped": sum(duplicate_counts.values()),
        "duplicate_counts": duplicate_counts,
        "dispositions": [duplicates[key] for key in sorted(duplicates)[:100]],
        "dispositions_truncated": len(duplicates) > 100,
    }
    diagnostics = json.dumps(
        {
            "source_version": source_version,
            "retrieved_at": retrieved_at.isoformat(),
            "upstream_rows_scanned": len(rows),
            "matching_rows_before_cap": matching_rows_before_cap,
            "result_cap": MAX_MATCHED_ROWS,
            "result_cap_reached": was_truncated,
            **duplicate_detail,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    grouped: dict[str, list[PublicListingRecord]] = defaultdict(list)
    for _, site_id, dataset_id, row in matched:
        grouped[dataset_id].append(
            _normalize_row(
                row,
                source_id=SOURCE_ID,
                source_url=csv_url,
                source_version=source_version,
                retrieved_at=retrieved_at,
                valid_states=valid_states,
            )
        )

    health = SourceHealth(
        source_id=SOURCE_ID,
        status="current",
        checked_at=retrieved_at,
        last_successful_retrieval=retrieved_at,
        detail=diagnostics,
    )
    dataset_ids = set(dataset_by_region.values())
    if unreported_dataset_id is not None:
        dataset_ids.add(unreported_dataset_id)
    catalogs = [
        DatasetCatalog(
            dataset_id=dataset_id,
            source_id=SOURCE_ID,
            programs=[],
            listings=grouped.get(dataset_id, []),
            criteria_references=[],
            source_health=health,
        )
        for dataset_id in sorted(dataset_ids)
    ]
    return catalogs, was_truncated


def _duplicate_reason(disposition: str) -> str:
    if disposition == "exact_duplicate_skipped":
        return (
            "Repeated identical BPHC Assigned Number row in the same HRSA CSV snapshot."
        )
    return (
        "Repeated BPHC Assigned Number has different row content in the same HRSA "
        "CSV snapshot; skipped pending source reconciliation."
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HrsaSourceUnavailable(
            "HRSA source manifest is unavailable.", reason_code="manifest_unavailable"
        ) from exc
    if (
        manifest.get("manifest_version") != "public-catalog-source-seed-v1"
        or manifest.get("registration", {}).get("source", {}).get("source_id")
        != SOURCE_ID
        or manifest.get("persistence_policy", {}).get("mode") != "source_query_only"
        or manifest.get("persistence_policy", {}).get("bulk_snapshot_import_enabled")
        is not False
    ):
        raise HrsaSourceUnavailable(
            "HRSA source manifest failed policy validation.",
            reason_code="manifest_policy_invalid",
        )
    return manifest


def _csv_url(manifest: dict[str, Any]) -> str:
    pages = manifest.get("source_snapshot", {}).get("pages", [])
    for page in pages:
        url = page.get("url") if isinstance(page, dict) else None
        if isinstance(url, str):
            parsed = urlsplit(url)
            if (
                parsed.scheme == "https"
                and parsed.hostname == "data.hrsa.gov"
                and parsed.path.endswith(".csv")
            ):
                return url
    raise HrsaSourceUnavailable(
        "HRSA public CSV endpoint is not declared.",
        reason_code="source_endpoint_missing",
    )


def _registered_partitions(
    manifest: dict[str, Any], registered: list[dict[str, Any]]
) -> tuple[dict[str, str], str | None]:
    inventory = {
        item.get("dataset_id"): item
        for item in registered
        if isinstance(item, dict)
        and item.get("source_id") == SOURCE_ID
        and item.get("metadata", {}).get("visibility_scope") == "cluster_public"
    }
    region_map: dict[str, str] = {}
    unreported: str | None = None
    for dataset in manifest["registration"]["datasets"]:
        dataset_id = dataset.get("dataset_id")
        if dataset_id not in inventory:
            continue
        metadata = dataset.get("metadata", {})
        region = metadata.get("hhs_region_code")
        if isinstance(region, str):
            region_map[region] = dataset_id
        elif metadata.get("partition") == "hhs_region_not_reported":
            unreported = dataset_id
    if not region_map:
        raise HrsaSourceUnavailable(
            "No registered HRSA source partitions are active.",
            reason_code="no_registered_partitions",
        )
    return region_map, unreported


def _fetch_csv(url: str) -> tuple[bytes, httpx.Headers]:
    try:
        with httpx.Client(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                final = urlsplit(str(response.url))
                if final.scheme != "https" or final.hostname != "data.hrsa.gov":
                    raise HrsaSourceUnavailable(
                        "HRSA CSV redirected outside its host.",
                        reason_code="redirect_outside_approved_host",
                    )
                content_length = response.headers.get("Content-Length")
                if content_length and int(content_length) > MAX_CSV_BYTES:
                    raise HrsaSourceUnavailable(
                        "HRSA CSV exceeded the byte limit.",
                        reason_code="source_size_limit_exceeded",
                    )
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_CSV_BYTES:
                        raise HrsaSourceUnavailable(
                            "HRSA CSV exceeded the byte limit.",
                            reason_code="source_size_limit_exceeded",
                        )
                return bytes(chunks), response.headers
    except (httpx.HTTPError, ValueError) as exc:
        raise HrsaSourceUnavailable(
            "HRSA public CSV request failed.", reason_code="upstream_fetch_failed"
        ) from exc


def _parse_csv(raw: bytes) -> list[dict[str, str]]:
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HrsaSourceUnavailable(
            "HRSA CSV could not be parsed.", reason_code="source_csv_parse_failed"
        ) from exc
    required = {
        "BPHC Assigned Number",
        "Site Name",
        "Site State Abbreviation",
        "HHS Region Code",
    }
    if not rows or not required.issubset(rows[0]):
        raise HrsaSourceUnavailable(
            "HRSA CSV does not match its declared columns.",
            reason_code="source_schema_changed",
        )
    return rows


def _source_version(raw: bytes, headers: httpx.Headers) -> str:
    return (
        headers.get("ETag")
        or headers.get("Last-Modified")
        or f"sha256:{hashlib.sha256(raw).hexdigest()}"
    )[:200]


def _clean(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _normalize_row(
    row: dict[str, str],
    *,
    source_id: str,
    source_url: str,
    source_version: str,
    retrieved_at: datetime,
    valid_states: set[str],
) -> PublicListingRecord:
    site_id = _clean(row.get("BPHC Assigned Number"), 128)
    site_name = _clean(row.get("Site Name"), 500)
    state = _clean(row.get("Site State Abbreviation"), 2).upper()
    county = _clean(
        row.get("Complete County Name") or row.get("County Equivalent Name"), 100
    )
    jurisdiction = (
        Jurisdiction(level="state", state_code=state)
        if state in valid_states
        else Jurisdiction(level="federal")
    )
    details = [
        ("Provider", _clean(row.get("Health Center Name"))),
        ("Center type", _clean(row.get("Health Center Type"))),
        (
            "Address",
            ", ".join(
                part
                for part in (
                    _clean(row.get("Site Address")),
                    _clean(row.get("Site City")),
                    state if state != "XX" else "",
                    _clean(row.get("Site Postal Code")),
                )
                if part
            ),
        ),
        ("County", county),
        ("Phone", _clean(row.get("Site Telephone Number"))),
        ("Website listed by HRSA", _clean(row.get("Site Web Address"))),
        (
            "Setting",
            _clean(
                row.get(
                    "Health Center Service Delivery Site Location Setting Description"
                )
            ),
        ),
        ("HRSA site status", _clean(row.get("Site Status Description"))),
    ]
    if state == "XX":
        details.append(("Source geography", "XX (unclassified by HRSA)"))
    summary = "; ".join(f"{key}: {value}" for key, value in details if value)[:1200]
    source = SourceReference(
        source_id=source_id,
        source_type="public_source",
        approval_status="approved",
        agency="Health Resources and Services Administration (HRSA)",
        title="Health Center Service Delivery and Look-Alike Sites",
        url=source_url,
        jurisdiction=jurisdiction,
        retrieved_at=retrieved_at,
        publisher_version=source_version,
    )
    website = _clean(row.get("Site Web Address"), 2048)
    if not website.startswith("https://"):
        website = None
    return PublicListingRecord(
        listing_id=f"hrsa:{site_id}",
        record_role="referral",
        schema_version="public-listing-v3",
        record_kind="referral_resource",
        title=site_name,
        summary=summary,
        agency=_clean(row.get("Health Center Name"), 300)
        or "HRSA-supported health center",
        program_url=source_url,
        website_url=website,
        jurisdiction=jurisdiction,
        source=source,
        criteria_status="not_assessed",
        source_notice=(
            "HRSA locator result for referral only. This does not establish "
            "provider eligibility, services, appointment availability, or coverage."
        ),
    )
