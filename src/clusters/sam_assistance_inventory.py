"""Normalize an operator-provided SAM.gov Assistance Listings CSV snapshot."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

SOURCE_ID = "sam.gov.assistance_listings"
DATASET_ID = "sam-assistance-listings-current"
SOURCE_URL = (
    "https://s3.amazonaws.com/falextracts/Assistance%20Listings/datagov/"
    "AssistanceListings_DataGov_PUBLIC_CURRENT.csv"
)
SCHEMA_VERSION = "public-listing-v4"
SOURCE_NAME = "SAM.gov Assistance Listings Data Services"
TRANSFORM_VERSION = "sam-assistance-listing-import-v2"
MAX_RECORDS_PER_BATCH = 25
MAX_BATCH_BYTES = 60_000
MAX_SOURCE_BYTES = 100_000_000
MAX_SUMMARY_CHARS = 1_200
MAX_NARRATIVE_CHARS = 600
# SAM 10.539 describes benefits only for CNMI residents; MP is its USPS code.
TERRITORY_PROGRAM_CODES = {"10.539": "MP"}

REQUIRED_HEADERS = (
    "Program Number",
    "Program Title",
    "Federal Agency (030)",
    "Objectives (050)",
    "Types of Assistance (060)",
    "Applicant Eligibility (081)",
    "Beneficiary Eligibility (082)",
    "Website Address (153)",
    "Published Date",
    "URL",
)


def _clean_text(value: object, limit: int) -> tuple[str, bool]:
    if not isinstance(value, str):
        return "", False
    normalized = " ".join(value.split())
    return normalized[:limit], len(normalized) > limit


def _https_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or any(char.isspace() for char in candidate)
    ):
        return None
    return candidate[:2048]


def _published_date(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    for pattern in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _encode_csv(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1252"), "cp1252"


def _record(
    row: Mapping[str, str], *, snapshot_hash: str, retrieved_at: str
) -> dict[str, object]:
    program_number, _ = _clean_text(row.get("Program Number"), 64)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,63}", program_number):
        raise ValueError("invalid_program_number")

    title, title_truncated = _clean_text(row.get("Program Title"), 500)
    if not title:
        raise ValueError("missing_program_title")
    agency, agency_truncated = _clean_text(row.get("Federal Agency (030)"), 300)
    if not agency:
        raise ValueError("missing_federal_agency")

    summary, summary_truncated = _clean_text(
        row.get("Objectives (050)"), MAX_SUMMARY_CHARS
    )
    assistance_type, assistance_truncated = _clean_text(
        row.get("Types of Assistance (060)"), MAX_NARRATIVE_CHARS
    )
    applicant_description, applicant_truncated = _clean_text(
        row.get("Applicant Eligibility (081)"), MAX_NARRATIVE_CHARS
    )
    beneficiary_description, beneficiary_truncated = _clean_text(
        row.get("Beneficiary Eligibility (082)"), MAX_NARRATIVE_CHARS
    )
    program_url = _https_url(row.get("URL"))
    website_url = _https_url(row.get("Website Address (153)"))
    published_date = _published_date(row.get("Published Date"))

    jurisdiction_level = (
        "territory" if program_number in TERRITORY_PROGRAM_CODES else "federal"
    )
    jurisdiction_code = TERRITORY_PROGRAM_CODES.get(program_number)
    attributes: dict[str, object] = {
        "listing.record_kind": "assistance_program",
        "listing.program_number": program_number,
        "listing.title": title,
        "listing.agency": agency,
        "listing.status": "unknown",
        "core.geography.jurisdiction_level": jurisdiction_level,
        "core.geography.country_code": "US",
    }
    if jurisdiction_code:
        attributes["core.geography.state_code"] = jurisdiction_code
    for attribute_id, value in (
        ("listing.summary", summary),
        ("listing.assistance_type", assistance_type),
        ("listing.applicant_description", applicant_description),
        ("listing.beneficiary_description", beneficiary_description),
    ):
        if value:
            attributes[attribute_id] = value
    if program_url:
        attributes["listing.program_url"] = program_url
    if website_url:
        attributes["listing.website_url"] = website_url
    if published_date:
        attributes["listing.published_date"] = published_date

    record_id = f"sam.gov.assistance_listings.{program_number}"
    source_metadata: dict[str, object] = {
        "source_id": SOURCE_ID,
        "source_type": "public_source",
        "approval_status": "approved",
        "publisher": "SAM.gov",
        "source_title": SOURCE_NAME,
        "source_version": f"sha256:{snapshot_hash}",
        "source_snapshot_sha256": snapshot_hash,
        "transform_version": TRANSFORM_VERSION,
        "schema_version": SCHEMA_VERSION,
        "retrieved_at": retrieved_at,
        "criteria_status": "not_assessed",
        "criteria_available": False,
        "status_source_field_present": False,
        "raw_payload_stored": False,
        "truncated_fields": [
            field
            for field, truncated in (
                ("title", title_truncated),
                ("agency", agency_truncated),
                ("summary", summary_truncated),
                ("assistance_type", assistance_truncated),
                ("applicant_description", applicant_truncated),
                ("beneficiary_description", beneficiary_truncated),
            )
            if truncated
        ],
    }
    semantic_context = {
        "catalog_namespace": "programs",
        "jurisdiction": {
            "level": jurisdiction_level,
            "country_code": "US",
            **({"state_code": jurisdiction_code} if jurisdiction_code else {}),
        },
        "listing_kind": "assistance_program",
    }
    stable_content = {
        "attributes": attributes,
        "semantic_context": semantic_context,
        "source_metadata": {
            key: value
            for key, value in source_metadata.items()
            if key != "retrieved_at"
        },
    }
    content_hash = hashlib.sha256(
        json.dumps(
            stable_content, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    return {
        "source_record_id": record_id,
        "record_type": "public_listing",
        "canonical_attributes": attributes,
        "semantic_context": semantic_context,
        "source_metadata": source_metadata,
        "source_url": SOURCE_URL,
        "content_hash": content_hash,
        "schema_version": SCHEMA_VERSION,
        "observed_at": retrieved_at,
    }


def build_snapshot(
    csv_path: Path, *, retrieved_at: str | None = None
) -> dict[str, object]:
    """Read and normalize one official CSV without persisting its raw contents."""
    raw = csv_path.read_bytes()
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise ValueError("CSV snapshot is empty or exceeds the 100 MB safety limit.")
    text, encoding = _encode_csv(raw)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = reader.fieldnames
    if not headers or len(headers) != len(set(headers)):
        raise ValueError("CSV snapshot has missing or duplicate column headers.")
    missing_headers = sorted(set(REQUIRED_HEADERS) - set(headers))
    if missing_headers:
        raise ValueError(
            "CSV snapshot is missing required headers: " + ", ".join(missing_headers)
        )

    snapshot_hash = hashlib.sha256(raw).hexdigest()
    fetched_at = retrieved_at or datetime.now(UTC).isoformat()
    accepted: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    seen_numbers: set[str] = set()
    warning_count = 0
    for row_number, row in enumerate(reader, start=2):
        if None in row:
            rejected.append({"row": row_number, "reason": "unexpected_extra_columns"})
            continue
        if any(value is None for value in row.values()):
            rejected.append({"row": row_number, "reason": "incomplete_csv_row"})
            continue
        try:
            item = _record(row, snapshot_hash=snapshot_hash, retrieved_at=fetched_at)
        except ValueError as exc:
            rejected.append({"row": row_number, "reason": str(exc)})
            continue
        program_number = str(item["canonical_attributes"]["listing.program_number"])
        if program_number in seen_numbers:
            rejected.append({"row": row_number, "reason": "duplicate_program_number"})
            continue
        seen_numbers.add(program_number)
        metadata = item["source_metadata"]
        if metadata["truncated_fields"]:
            warning_count += 1
        attributes = item["canonical_attributes"]
        if (
            "URL" in row
            and row.get("URL", "").strip()
            and "listing.program_url" not in attributes
        ) or (
            row.get("Website Address (153)", "").strip()
            and "listing.website_url" not in attributes
        ):
            warning_count += 1
        if (
            row.get("Published Date", "").strip()
            and "listing.published_date" not in attributes
        ):
            warning_count += 1
        accepted.append(item)

    if not accepted:
        raise ValueError("CSV snapshot contains no valid Assistance Listings records.")
    batches = payload_batches(
        accepted, fetched_at=fetched_at, snapshot_hash=snapshot_hash
    )
    return {
        "dataset_id": DATASET_ID,
        "source_id": SOURCE_ID,
        "source_url": SOURCE_URL,
        "schema_version": SCHEMA_VERSION,
        "source_version": f"sha256:{snapshot_hash}",
        "snapshot_sha256": snapshot_hash,
        "encoding": encoding,
        "retrieved_at": fetched_at,
        "input_row_count": len(accepted) + len(rejected),
        "accepted_count": len(accepted),
        "rejected_rows": rejected,
        "rejected_count": len(rejected),
        "warning_count": warning_count,
        "records": accepted,
        "batches": batches,
    }


def _encoded_payload(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def payload_batches(
    records: list[dict[str, object]], *, fetched_at: str, snapshot_hash: str
) -> list[dict[str, object]]:
    """Pack normalized records within both public-catalog request bounds."""
    batches: list[dict[str, object]] = []
    current: list[dict[str, object]] = []

    def payload(items: list[dict[str, object]]) -> dict[str, object]:
        return {
            "outcome": "succeeded",
            "source_version": f"sha256:{snapshot_hash}",
            "retrieved_at": fetched_at,
            "transform_version": TRANSFORM_VERSION,
            "validation_outcome": "valid",
            "detail": (
                "Operator-provided SAM Assistance Listings snapshot; "
                "criteria not assessed."
            ),
            "records": items,
        }

    for record in records:
        candidate = payload([*current, record])
        if (
            len(current) < MAX_RECORDS_PER_BATCH
            and len(_encoded_payload(candidate)) <= MAX_BATCH_BYTES
        ):
            current.append(record)
            continue
        if not current:
            raise ValueError(
                "One Assistance Listing exceeds the ingestion batch byte limit."
            )
        batches.append(payload(current))
        current = [record]
        if len(_encoded_payload(payload(current))) > MAX_BATCH_BYTES:
            raise ValueError(
                "One Assistance Listing exceeds the ingestion batch byte limit."
            )
    if current:
        batches.append(payload(current))
    return batches
