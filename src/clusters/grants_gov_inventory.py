"""Fetch and normalize a bounded, criteria-free Grants.gov opportunity seed."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import Any

import httpx

SEARCH_URL = "https://api.grants.gov/v1/api/search2"
DETAIL_URL = "https://api.grants.gov/v1/api/fetchOpportunity"
SEARCH_PAGE_URL = "https://www.grants.gov/search-grants.html"
AGENCY_DIRECTORY_URL = "https://www.grants.gov/learn-grants/grant-making-agencies.html"
NON_ENDORSEMENT_NOTICE = (
    "This product uses the Grants.gov API but is not endorsed or certified by "
    "the U.S. Department of Health and Human Services. Grants.gov funding "
    "opportunities are generally for organizations, not personal benefits. "
    "Agency information: "
    f"{AGENCY_DIRECTORY_URL}"
)
SOURCE_ID = "grants.gov.opportunities"
DATASET_ID = "grants-gov-current-opportunities"
SOURCE_VERSION = "search2-fetchOpportunity"
TRANSFORM_VERSION = "grantsgov-listing-v1"
MAX_ROWS = 25
MAX_SUMMARY_CHARS = 1200


class _TextExtractor(HTMLParser):
    """Extract readable text from publisher HTML without retaining markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.parts.append(value)


def _plain_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    return " ".join(parser.parts)[:MAX_SUMMARY_CHARS]


def _date(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    for pattern in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            pass
    return None


def _agency_name(detail: dict[str, Any], hit: dict[str, Any]) -> str:
    synopsis = detail.get("synopsis")
    if isinstance(synopsis, dict):
        name = synopsis.get("agencyName")
        if isinstance(name, str) and name.strip():
            return name.strip()[:300]
    name = hit.get("agency") or detail.get("owningAgencyCode")
    return name.strip()[:300] if isinstance(name, str) and name.strip() else "Unknown"


def _applicant_types(detail: dict[str, Any]) -> list[str]:
    synopsis = detail.get("synopsis")
    types = synopsis.get("applicantTypes") if isinstance(synopsis, dict) else None
    if not isinstance(types, list):
        return []
    return [
        item["description"].strip()[:120]
        for item in types
        if isinstance(item, dict)
        and isinstance(item.get("description"), str)
        and item["description"].strip()
    ][:40]


def normalize_opportunity(
    hit: dict[str, Any], detail: dict[str, Any], *, retrieved_at: str, query: str
) -> dict[str, Any]:
    """Project only the approved discovery fields; never infer criteria."""
    identifier = hit.get("id")
    if not isinstance(identifier, (str, int)) or not str(identifier).isdigit():
        raise ValueError("Grants.gov result has no numeric opportunity identifier.")
    title = detail.get("opportunityTitle") or hit.get("title")
    agency = _agency_name(detail, hit)
    if agency == "Unknown":
        raise ValueError("Grants.gov result is missing publisher agency identity.")
    synopsis = detail.get("synopsis")
    summary = (
        _plain_text(synopsis.get("synopsisDesc")) if isinstance(synopsis, dict) else ""
    )
    opportunity_number = detail.get("opportunityNumber") or hit.get("number")
    if isinstance(opportunity_number, str) and opportunity_number.strip():
        summary = f"Opportunity number: {opportunity_number.strip()}. {summary}"[
            :MAX_SUMMARY_CHARS
        ]
    status = hit.get("oppStatus")
    if not isinstance(title, str) or not title.strip() or not isinstance(status, str):
        raise ValueError("Grants.gov result is missing listing fields.")

    attributes: dict[str, object] = {
        "listing.title": title.strip()[:500],
        "listing.summary": summary,
        "listing.agency": agency,
        "listing.applicant_types": _applicant_types(detail),
        "listing.status": status.strip().lower()[:100],
        "listing.opportunity_url": f"https://www.grants.gov/search-results-detail/{identifier}",
        "core.geography.jurisdiction_level": "federal",
        "core.geography.country_code": "US",
    }
    open_date = _date(hit.get("openDate"))
    close_date = _date(hit.get("closeDate"))
    if open_date:
        attributes["listing.open_date"] = open_date
    if close_date:
        attributes["listing.close_date"] = close_date

    source_metadata = {
        "source_id": SOURCE_ID,
        "source_type": "public_source",
        "approval_status": "approved",
        "publisher": "Grants.gov",
        "source_title": "Federal funding opportunities",
        "source_version": SOURCE_VERSION,
        "opportunity_number": opportunity_number
        if isinstance(opportunity_number, str)
        else None,
        "publisher_revision": detail.get("revision"),
        "transform_version": TRANSFORM_VERSION,
        "retrieved_at": retrieved_at,
        "query": query,
        "agency_directory_url": AGENCY_DIRECTORY_URL,
        "criteria_status": "not_assessed",
        "criteria_available": False,
        "source_notice": NON_ENDORSEMENT_NOTICE,
        "raw_payload_stored": False,
    }
    semantic_context = {
        "catalog_namespace": "programs",
        "jurisdiction": {"level": "federal", "country_code": "US"},
        "listing_kind": "funding_opportunity",
    }
    stable_id = f"grants.gov.{identifier}"
    canonical = json.dumps(
        {
            "attributes": attributes,
            "source_metadata": source_metadata,
            "semantic_context": semantic_context,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return {
        "source_record_id": stable_id,
        "record_type": "public_listing",
        "canonical_attributes": attributes,
        "semantic_context": semantic_context,
        "source_metadata": source_metadata,
        "source_url": SEARCH_PAGE_URL,
        "content_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "schema_version": "public-listing-v1",
        "observed_at": retrieved_at,
    }


def fetch_current_opportunities(*, limit: int = MAX_ROWS) -> dict[str, Any]:
    """Fetch a capped page of posted opportunities and their bounded details."""
    if not 1 <= limit <= MAX_ROWS:
        raise ValueError(f"limit must be between 1 and {MAX_ROWS}.")
    query = f"oppStatuses=posted;rows={limit};keyword=omitted"
    retrieved_at = datetime.now(UTC).isoformat()
    with httpx.Client(
        timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=False
    ) as client:
        response = client.post(
            SEARCH_URL, json={"rows": limit, "oppStatuses": "posted"}
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        hits = data.get("oppHits") if isinstance(data, dict) else None
        if payload.get("errorcode") != 0 or not isinstance(hits, list):
            raise ValueError("Grants.gov search returned an invalid response.")

        records = []
        today = datetime.now(UTC).date()
        for hit in hits:
            if not isinstance(hit, dict):
                raise ValueError("Grants.gov search returned a malformed result.")
            close_date = _date(hit.get("closeDate"))
            open_date = _date(hit.get("openDate"))
            if (close_date and date.fromisoformat(close_date) < today) or (
                open_date and date.fromisoformat(open_date) > today
            ):
                continue
            identifier = hit.get("id")
            if not isinstance(identifier, (str, int)) or not str(identifier).isdigit():
                raise ValueError(
                    "Grants.gov result has an invalid opportunity identifier."
                )
            detail_response = client.post(
                DETAIL_URL, json={"opportunityId": int(identifier)}
            )
            detail_response.raise_for_status()
            detail_payload = detail_response.json()
            detail = (
                detail_payload.get("data") if isinstance(detail_payload, dict) else None
            )
            if detail_payload.get("errorcode") != 0 or not isinstance(detail, dict):
                raise ValueError("Grants.gov detail response was incomplete.")
            records.append(
                normalize_opportunity(
                    hit, detail, retrieved_at=retrieved_at, query=query
                )
            )
            time.sleep(0.05)

    if not records:
        raise ValueError("No current posted opportunities passed the date filter.")
    return {
        "query": query,
        "retrieved_at": retrieved_at,
        "records": records,
        "hit_count": data.get("hitCount"),
        "requested_limit": limit,
    }
