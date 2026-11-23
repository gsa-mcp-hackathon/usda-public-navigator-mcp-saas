"""Bounded, source-aware ingestion for public catalog metadata and references.

The import path is a one-shot operator function, never an MCP tool. Runtime MCP
applications keep their existing read-only database credentials. Only allowlisted
fields are normalized; raw source payloads and person/contact data are discarded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

MAX_DATASET_RECORDS = 1000
MAX_RUN_RECORDS = 100
USER_AGENT = "MCP-SaaS-Hackathon/0.1 (public catalog metadata prototype)"


@dataclass(frozen=True)
class PublicRecord:
    """A normalized source record with stable source identity and context."""

    dataset_id: str
    source_id: str
    source_dataset_id: str
    source_record_id: str
    title: str
    landing_page: str | None
    canonical_attributes: dict[str, Any]
    semantic_context: dict[str, Any]
    source_metadata: dict[str, Any]
    content_hash: str
    schema_version: str = "public-data-v1"
    record_type: str = "catalog_metadata"


def _hash(value: Any) -> str:
    """Hash canonical JSON for stable, payload-safe idempotency checks."""
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value: Any, limit: int = 1200) -> str:
    """Normalize public text fields and bound their stored size."""
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def normalize_datagov_result(result: dict[str, Any]) -> PublicRecord:
    """Allowlist catalog fields; do not copy contact points or unreviewed raw JSON."""
    identifier = _text(result.get("identifier") or result.get("id"), 500)
    if not identifier:
        raise ValueError("Data.gov record is missing a stable identifier.")
    title = _text(result.get("title"), 500) or identifier
    organization = result.get("organization") or {}
    publisher = _text(
        result.get("publisher")
        or (organization.get("title") if isinstance(organization, dict) else ""),
        300,
    )
    description = _text(result.get("description"), 1200)
    keywords = result.get("keyword") or result.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = [keywords]
    keywords = [_text(item, 100) for item in keywords[:20] if _text(item, 100)]
    landing_page = _text(result.get("landingPage") or result.get("url"), 1000) or None
    attributes = {
        "core.dataset.title": title,
        "core.dataset.source_identifier": identifier,
        "core.dataset.content_type": "catalog_metadata",
    }
    if publisher:
        attributes["core.dataset.publisher"] = publisher
    if description:
        attributes["core.dataset.description"] = description
    if keywords:
        attributes["core.dataset.keywords"] = keywords
    safe_metadata = {"catalog": "Data.gov", "record_shape": "dataset_metadata"}
    return PublicRecord(
        dataset_id="datagov-catalog",
        source_id="datagov.catalog",
        source_dataset_id="datagov-catalog-v4",
        source_record_id=identifier,
        title=title,
        landing_page=landing_page,
        canonical_attributes=attributes,
        semantic_context={
            "interpretation": "dataset_discovery_metadata",
            "publisher": publisher,
        },
        source_metadata=safe_metadata,
        content_hash=_hash({"attributes": attributes, "landing_page": landing_page}),
    )


def normalize_farmers_page(
    html: str, url: str = "https://www.farmers.gov/loans"
) -> PublicRecord:
    """Store public page title and URL only; discard body and contact data."""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title = _text(
        re.sub(r"<[^>]+>", " ", title_match.group(1) if title_match else ""), 500
    )
    if not title:
        title = "Farm Loans for Farmers and Ranchers"
    attributes = {
        "core.dataset.title": title,
        "core.dataset.publisher": "USDA Farm Service Agency",
        "core.dataset.content_type": "public_web_reference",
    }
    metadata = {"source_type": "web_page_metadata", "body_persisted": False}
    source_id = "farmers.gov/loans"
    return PublicRecord(
        dataset_id="farmers-fsa-loans-reference",
        source_id="farmers.gov.loans",
        source_dataset_id="farmers-fsa-loans-reference",
        source_record_id=source_id,
        title=title,
        landing_page=url,
        canonical_attributes=attributes,
        semantic_context={
            "interpretation": "public_program_reference",
            "agency": "USDA FSA",
        },
        source_metadata=metadata,
        content_hash=_hash({"attributes": attributes, "url": url}),
        record_type="public_web_reference",
    )


def normalize_perdiem_payload(
    payload: dict[str, Any], fiscal_year: int
) -> list[PublicRecord]:
    """Normalize the GSA API's state/month rates while retaining fiscal-year context."""
    records: list[PublicRecord] = []
    for state in payload.get("rates", []):
        state_code = _text(state.get("state"), 2).upper()
        for location in state.get("rate", []):
            location_name = _text(location.get("city"), 200) or _text(
                location.get("county"), 200
            )
            months = location.get("months", [])
            if isinstance(months, dict):
                months = [
                    dict(value, month=key)
                    for key, value in months.items()
                    if isinstance(value, dict)
                ]
            for month in months:
                if not isinstance(month, dict):
                    continue
                month_name = _text(month.get("month"), 20)
                rate_value = month.get("value", month.get("rate"))
                if not state_code or not month_name or rate_value is None:
                    continue
                try:
                    rate = float(rate_value)
                except (TypeError, ValueError):
                    continue
                source_record_id = (
                    f"{state_code}:{location_name}:{fiscal_year}:{month_name}".lower()
                )
                attributes = {
                    "core.geography.state_code": state_code,
                    "core.time.fiscal_year": fiscal_year,
                    "core.time.month": month_name,
                    "gsa.per_diem.daily_rate": rate,
                }
                context = {
                    "rate_type": "federal_per_diem",
                    "location": location_name,
                    "unit": "USD/day",
                }
                records.append(
                    PublicRecord(
                        dataset_id="gsa-perdiem-rates",
                        source_id="gsa.perdiem",
                        source_dataset_id="gsa-perdiem-v2",
                        source_record_id=source_record_id,
                        title=(
                            f"GSA Per Diem {location_name} {month_name} FY{fiscal_year}"
                        ),
                        landing_page="https://open.gsa.gov/api/perdiem/",
                        canonical_attributes=attributes,
                        semantic_context=context,
                        source_metadata={
                            "api": "GSA Per Diem API",
                            "response_shape": "state_location_month",
                        },
                        content_hash=_hash(
                            {"attributes": attributes, "context": context}
                        ),
                        record_type="rate_observation",
                    )
                )
    return records


class _DataGovLinkParser(HTMLParser):
    """Collect stable dataset slugs from the public Data.gov search page."""

    def __init__(self) -> None:
        super().__init__()
        self.slugs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        match = re.fullmatch(r"/dataset/([a-z0-9][a-z0-9-]*)/?", href)
        if match and match.group(1) not in self.slugs:
            self.slugs.append(match.group(1))


def normalize_datagov_search_slug(slug: str) -> PublicRecord:
    """Represent a public catalog search hit using its stable URL slug."""
    slug = _text(slug, 500).strip("/")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
        raise ValueError("Data.gov search result has an invalid dataset slug.")
    title = slug.replace("-", " ").title()
    attributes = {
        "core.dataset.title": title,
        "core.dataset.source_identifier": slug,
        "core.dataset.content_type": "catalog_metadata",
    }
    landing_page = f"https://catalog.data.gov/dataset/{slug}"
    return PublicRecord(
        dataset_id="datagov-catalog",
        source_id="datagov.catalog",
        source_dataset_id="datagov-catalog-v4",
        source_record_id=slug,
        title=title,
        landing_page=landing_page,
        canonical_attributes=attributes,
        semantic_context={
            "interpretation": "dataset_discovery_metadata",
            "title_source": "url_slug",
        },
        source_metadata={
            "catalog": "Data.gov",
            "record_shape": "public_search_listing",
        },
        content_hash=_hash({"attributes": attributes, "landing_page": landing_page}),
    )


def fetch_datagov_search_page(query: str, limit: int) -> list[PublicRecord]:
    """Fallback to Data.gov's public search page when the API gateway throttles."""
    response = httpx.get(
        "https://catalog.data.gov/dataset",
        params={"q": query},
        headers={"User-Agent": USER_AGENT},
        timeout=25,
        follow_redirects=True,
    )
    response.raise_for_status()
    parser = _DataGovLinkParser()
    parser.feed(response.text)
    return [normalize_datagov_search_slug(slug) for slug in parser.slugs[:limit]]


def fetch_datagov(
    query: str, limit: int = 20, *, api_key: str = "DEMO_KEY"
) -> list[PublicRecord]:
    """Fetch a bounded Data.gov catalog search using GSA's public API gateway."""
    if not 1 <= limit <= MAX_RUN_RECORDS:
        raise ValueError(f"Per-query limit must be between 1 and {MAX_RUN_RECORDS}.")
    response = httpx.get(
        "https://api.gsa.gov/technology/datagov/v4/search",
        params={"q": query, "per_page": limit},
        headers={"X-Api-Key": api_key, "User-Agent": USER_AGENT},
        timeout=20,
        follow_redirects=True,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403, 429}:
            return fetch_datagov_search_page(query, limit)
        raise
    payload = response.json()
    results = payload.get("results", payload.get("result", []))
    if isinstance(results, dict):
        results = results.get("results", [])
    return [
        normalize_datagov_result(item)
        for item in results[:limit]
        if isinstance(item, dict)
    ]


def fetch_farmers_reference() -> PublicRecord:
    """Fetch one Farmers.gov page, retaining only safe title and source URL metadata."""
    response = httpx.get(
        "https://www.farmers.gov/loans",
        headers={"User-Agent": USER_AGENT},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    return normalize_farmers_page(response.text, str(response.url))


def normalize_maryland_market(result: dict[str, Any]) -> PublicRecord:
    """Normalize Maryland rows and omit contacts or precise locations."""
    identifier = _text(result.get("objectid") or result.get("id"), 200)
    if not identifier:
        raise ValueError("Maryland market record is missing its source identifier.")
    title = _text(result.get("market_name"), 300) or identifier
    attributes = {
        "core.geography.state_code": "MD",
        "md.agriculture.market_name": title,
    }
    county = _text(result.get("farmers_market_county"), 100)
    if county:
        attributes["md.geography.county"] = county
    for source_field, canonical_id in (
        ("market_start_date", "md.agriculture.market_start_date"),
        ("market_end_date", "md.agriculture.market_end_date"),
        ("federal_benefits", "md.agriculture.federal_benefits_label"),
    ):
        value = _text(result.get(source_field), 300)
        if value:
            attributes[canonical_id] = value
    context = {
        "jurisdiction": "state:MD",
        "source_semantics": "farmers_market_directory",
    }
    return PublicRecord(
        dataset_id="maryland-farmers-markets",
        source_id="maryland.farmers_markets",
        source_dataset_id="maryland-farmers-market-directory",
        source_record_id=identifier,
        title=title,
        landing_page="https://opendata.maryland.gov/d/sw6s-2vmw",
        canonical_attributes=attributes,
        semantic_context=context,
        source_metadata={"portal": "Maryland Open Data", "source_shape": "market_row"},
        content_hash=_hash({"attributes": attributes, "context": context}),
        record_type="state_dataset_row",
    )


def fetch_maryland_markets(limit: int = 10) -> list[PublicRecord]:
    """Fetch at most 100 public rows from Maryland's official open-data API."""
    if not 1 <= limit <= MAX_RUN_RECORDS:
        raise ValueError(f"Per-source limit must be between 1 and {MAX_RUN_RECORDS}.")
    response = httpx.get(
        "https://opendata.maryland.gov/resource/sw6s-2vmw.json",
        params={"$limit": limit},
        headers={"User-Agent": USER_AGENT},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    return [
        normalize_maryland_market(item)
        for item in response.json()
        if isinstance(item, dict)
    ]


def fetch_gsa_perdiem(
    state: str = "KS", year: int = 2026, *, api_key: str = "DEMO_KEY"
) -> list[PublicRecord]:
    """Fetch one state and fiscal year from the public GSA Per Diem API."""
    state_code = state.strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", state_code) or not 2000 <= year <= 2100:
        raise ValueError("Use a two-letter state code and a valid fiscal year.")
    response = httpx.get(
        f"https://api.gsa.gov/travel/perdiem/v2/rates/state/{state_code}/year/{year}",
        headers={"X-Api-Key": api_key, "User-Agent": USER_AGENT},
        timeout=20,
        follow_redirects=True,
    )
    response.raise_for_status()
    return normalize_perdiem_payload(response.json(), year)


def bounded_records(
    records: list[PublicRecord], limit: int = MAX_RUN_RECORDS
) -> list[PublicRecord]:
    """Deduplicate by source-scoped key and enforce run/dataset hard caps."""
    if not 1 <= limit <= MAX_RUN_RECORDS:
        raise ValueError(f"Run limit must be between 1 and {MAX_RUN_RECORDS}.")
    if len(records) > limit:
        raise ValueError(f"Fetched source records exceed run cap of {limit}.")
    unique: dict[tuple[str, str, str], PublicRecord] = {}
    for record in records:
        key = (record.source_id, record.source_dataset_id, record.source_record_id)
        unique.setdefault(key, record)
    if len(unique) > limit:
        raise ValueError(f"Run has {len(unique)} unique records; cap is {limit}.")
    by_dataset: dict[str, int] = {}
    for record in unique.values():
        by_dataset[record.dataset_id] = by_dataset.get(record.dataset_id, 0) + 1
    if any(count > MAX_DATASET_RECORDS for count in by_dataset.values()):
        raise ValueError(f"Dataset record cap is {MAX_DATASET_RECORDS}.")
    return list(unique.values())


SOURCE_INFO = {
    "datagov.catalog": (
        "Data.gov Catalog API",
        "https://api.gsa.gov/technology/datagov/v4/",
        "available",
    ),
    "farmers.gov.loans": (
        "Farmers.gov farm loans page",
        "https://www.farmers.gov/loans",
        "metadata_only",
    ),
    "gsa.perdiem": (
        "GSA Per Diem API",
        "https://open.gsa.gov/api/perdiem/",
        "available",
    ),
    "maryland.farmers_markets": (
        "Maryland Farmers Markets API",
        "https://opendata.maryland.gov/resource/sw6s-2vmw.json",
        "available",
    ),
}
PATTERN_BINDINGS: dict[str, list[tuple[str, str | None, str]]] = {
    "datagov-catalog": [("eligibility", "0.1.0", "federal-public-data")],
    "farmers-fsa-loans-reference": [("eligibility", "0.1.0", "federal-agency")],
    "gsa-perdiem-rates": [("travel-rates-v0", None, "federal-gsa")],
    "maryland-farmers-markets": [("data-discovery-v0", None, "state-maryland")],
}


def persist_records(
    records: list[PublicRecord],
    database_url: str,
    run_id: str,
    limit: int,
    source_diagnostics: dict[str, str] | None = None,
    candidate_count: int | None = None,
) -> dict[str, int]:
    """Idempotently upsert allowlisted records and shared-pattern bindings."""
    records = bounded_records(records, limit)
    source_ids = sorted({record.source_id for record in records})
    counters = {"inserted": 0, "updated": 0, "duplicates": 0}
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for source_id in source_ids:
                source_name, source_url, access_status = SOURCE_INFO[source_id]
                cursor.execute(
                    "INSERT INTO mcp_saas.public_data_sources "
                    "(source_id, source_name, source_url, access_status) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (source_id) DO UPDATE SET "
                    "source_name = EXCLUDED.source_name, "
                    "source_url = EXCLUDED.source_url, "
                    "access_status = EXCLUDED.access_status, updated_at = now()",
                    (source_id, source_name, source_url, access_status),
                )
            cursor.execute(
                "INSERT INTO mcp_saas.public_dataset_ingestion_runs "
                "(run_id, source_id, requested_limit, fetched_count, status, details) "
                "VALUES (%s, %s, %s, %s, 'running', %s)",
                (
                    run_id,
                    source_ids[0],
                    limit,
                    len(records),
                    Jsonb(
                        {
                            "source_ids": source_ids,
                            "source_diagnostics": source_diagnostics or {},
                        }
                    ),
                ),
            )
            for record in records:
                cursor.execute(
                    "INSERT INTO mcp_saas.public_datasets "
                    "(dataset_id, source_id, source_dataset_id, title, landing_page, "
                    "schema_version, namespace_ids, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (dataset_id) DO UPDATE SET "
                    "title = EXCLUDED.title, landing_page = EXCLUDED.landing_page, "
                    "schema_version = EXCLUDED.schema_version, "
                    "namespace_ids = EXCLUDED.namespace_ids, "
                    "metadata = EXCLUDED.metadata, updated_at = now()",
                    (
                        record.dataset_id,
                        record.source_id,
                        record.source_dataset_id,
                        record.title,
                        record.landing_page,
                        record.schema_version,
                        sorted(
                            {key.split(".")[0] for key in record.canonical_attributes}
                        ),
                        Jsonb(
                            {
                                "content_type": record.canonical_attributes.get(
                                    "core.dataset.content_type", record.record_type
                                )
                            }
                        ),
                    ),
                )
                cursor.execute(
                    "SELECT content_hash FROM mcp_saas.public_dataset_records "
                    "WHERE dataset_id = %s AND source_record_id = %s",
                    (record.dataset_id, record.source_record_id),
                )
                previous = cursor.fetchone()
                if previous and previous[0] == record.content_hash:
                    counters["duplicates"] += 1
                else:
                    cursor.execute(
                        "INSERT INTO mcp_saas.public_dataset_records "
                        "(dataset_id, source_record_id, record_type, "
                        "canonical_attributes, "
                        "semantic_context, "
                        "source_metadata, source_url, content_hash, "
                        "schema_version, observed_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (dataset_id, source_record_id) DO UPDATE SET "
                        "record_type = EXCLUDED.record_type, "
                        "canonical_attributes = EXCLUDED.canonical_attributes, "
                        "semantic_context = EXCLUDED.semantic_context, "
                        "source_metadata = EXCLUDED.source_metadata, "
                        "source_url = EXCLUDED.source_url, "
                        "content_hash = EXCLUDED.content_hash, "
                        "schema_version = EXCLUDED.schema_version, "
                        "observed_at = EXCLUDED.observed_at",
                        (
                            record.dataset_id,
                            record.source_record_id,
                            record.record_type,
                            Jsonb(record.canonical_attributes),
                            Jsonb(record.semantic_context),
                            Jsonb(record.source_metadata),
                            record.landing_page,
                            record.content_hash,
                            record.schema_version,
                            datetime.now(UTC),
                        ),
                    )
                    counters["updated" if previous else "inserted"] += 1
                for cluster_id, pattern_version, tenant_id in PATTERN_BINDINGS.get(
                    record.dataset_id, []
                ):
                    cursor.execute(
                        "INSERT INTO mcp_saas.pattern_catalog_bindings "
                        "(dataset_id, cluster_id, pattern_version, tenant_id, "
                        "binding_context) "
                        "VALUES (%s, %s, %s, %s, %s) "
                        "ON CONFLICT (dataset_id, cluster_id, tenant_id, "
                        "(COALESCE(pattern_version, ''))) DO UPDATE SET "
                        "binding_context = EXCLUDED.binding_context",
                        (
                            record.dataset_id,
                            cluster_id,
                            pattern_version,
                            tenant_id,
                            Jsonb(
                                {
                                    "source_id": record.source_id,
                                    "interpretation": record.semantic_context.get(
                                        "interpretation", record.record_type
                                    ),
                                }
                            ),
                        ),
                    )
            cursor.execute(
                "UPDATE mcp_saas.public_dataset_ingestion_runs "
                "SET inserted_count = %s, updated_count = %s, "
                "duplicate_count = %s, status = 'succeeded', "
                "completed_at = now() WHERE run_id = %s",
                (
                    counters["inserted"],
                    counters["updated"],
                    counters["duplicates"],
                    run_id,
                ),
            )
    return counters


def gather_demo_records(
    limit: int, *, api_key: str = "DEMO_KEY"
) -> tuple[list[PublicRecord], dict[str, str], dict[str, int]]:
    """Fetch within a strict raw-record budget and report source overlap counts."""
    per_query = min(20, max(1, limit // 5))
    records: list[PublicRecord] = []
    diagnostics: dict[str, str] = {}
    candidate_counts: dict[str, int] = {}
    for query in ("farmers market USDA", "OASDI beneficiaries", "GSA per diem"):
        try:
            found = fetch_datagov(query, per_query, api_key=api_key)
            records.extend(found)
            candidate_counts["datagov.catalog"] = candidate_counts.get(
                "datagov.catalog", 0
            ) + len(found)
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            diagnostics[f"datagov.catalog:{query}"] = (
                f"HTTP {status}" if status else type(exc).__name__
            )
    try:
        record = fetch_farmers_reference()
        records.append(record)
        candidate_counts[record.source_id] = 1
    except httpx.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        diagnostics["farmers.gov.loans"] = (
            f"HTTP {status}" if status else type(exc).__name__
        )
    remaining = max(0, limit - len(records))
    if remaining:
        try:
            found = fetch_maryland_markets(min(remaining, 79))
            records.extend(found)
            candidate_counts["maryland.farmers_markets"] = len(found)
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            diagnostics["maryland.farmers_markets"] = (
                f"HTTP {status}" if status else type(exc).__name__
            )
    remaining = max(0, limit - len(records))
    if remaining:
        try:
            found = fetch_gsa_perdiem("KS", 2026, api_key=api_key)[:remaining]
            records.extend(found)
            candidate_counts["gsa.perdiem"] = len(found)
        except httpx.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            diagnostics["gsa.perdiem"] = (
                f"HTTP {status}" if status else type(exc).__name__
            )
    candidates = len(records)
    unique_records = bounded_records(records, limit)
    candidate_counts["source_query_duplicates"] = candidates - len(unique_records)
    return unique_records, diagnostics, candidate_counts


def main() -> None:
    """Run a bounded dry-run by default; require --apply before database writes."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=100, help="Hard run ceiling (maximum 100)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist to the dedicated public-catalog writer schema.",
    )
    parser.add_argument(
        "--database-url-file",
        type=Path,
        help="Mode-0600 writer DSN file, required with --apply.",
    )
    parser.add_argument(
        "--api-key",
        default="DEMO_KEY",
        help="GSA/Data.gov demo key; never logged or persisted.",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_RUN_RECORDS:
        parser.error(f"--limit must be between 1 and {MAX_RUN_RECORDS}")
    records, diagnostics, candidate_counts = gather_demo_records(
        args.limit, api_key=args.api_key
    )
    if args.apply:
        if not args.database_url_file or not args.database_url_file.is_file():
            parser.error("--apply requires an existing protected --database-url-file")
        result = persist_records(
            records,
            args.database_url_file.read_text(encoding="utf-8").strip(),
            "public-demo-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ"),
            args.limit,
            diagnostics,
            sum(
                value
                for key, value in candidate_counts.items()
                if key != "source_query_duplicates"
            ),
        )
    else:
        result = {"persisted": 0}
    print(
        json.dumps(
            {
                "fetched_candidates": sum(
                    value
                    for key, value in candidate_counts.items()
                    if key != "source_query_duplicates"
                ),
                "unique_records": len(records),
                "source_query_duplicates": candidate_counts.get(
                    "source_query_duplicates", 0
                ),
                "candidate_counts_by_source": {
                    key: value
                    for key, value in candidate_counts.items()
                    if key != "source_query_duplicates"
                },
                "limit": args.limit,
                "persisted": result,
                "datasets": len({record.dataset_id for record in records}),
                "source_record_counts": {
                    source_id: sum(record.source_id == source_id for record in records)
                    for source_id in sorted({record.source_id for record in records})
                },
                "source_diagnostics": diagnostics,
                "source_ids": sorted({record.source_id for record in records}),
                "deduplication": (
                    "source keys only; cross-source records remain separate"
                ),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
