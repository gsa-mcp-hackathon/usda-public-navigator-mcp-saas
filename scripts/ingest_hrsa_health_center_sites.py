"""Validate HRSA's source snapshot; bulk persistence is disabled by policy."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    REPO_ROOT / "configs/eligibility/catalog-seeds/hrsa-health-center-locations.json"
)
CSV_URL = "https://data.hrsa.gov/DataDownload/DD_Files/Health_Center_Service_Delivery_and_LookAlike_Sites.csv"
ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "mcp-saas-vm-data-api"}
MAX_BATCH_RECORDS = 100
MAX_DATASET_RECORDS = 5_000
SOURCE_ID = "us.hrsa.health-centers"
LISTING_SCHEMA = "public-listing-v3"
RECORD_CONTRACT = "normalized-public-record-v1"


class HrsaImportError(RuntimeError):
    """HRSA source data or a bounded Data API operation failed validation."""


def _api_origin(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.path not in {"", "/"}:
        raise HrsaImportError("Data API URL must be an origin without a path.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in ALLOWED_LOCAL_HOSTS
    ):
        raise HrsaImportError("Data API must use HTTPS or a trusted local origin.")
    return value.rstrip("/")


def _read_manifest(path: Path) -> dict[str, Any]:
    if path.resolve() != MANIFEST_PATH.resolve():
        raise HrsaImportError("Use the checked-in HRSA source manifest.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HrsaImportError("HRSA source manifest is not valid JSON.") from exc
    registration = manifest.get("registration")
    if (
        manifest.get("manifest_version") != "public-catalog-source-seed-v1"
        or not isinstance(registration, dict)
        or registration.get("contract_version") != "public-source-registration-v1"
        or registration.get("source", {}).get("source_id") != SOURCE_ID
    ):
        raise HrsaImportError("HRSA source manifest is incomplete or unsupported.")
    datasets = registration.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise HrsaImportError("HRSA manifest must declare its region datasets.")
    return manifest


def _bulk_snapshot_import_enabled(manifest: dict[str, Any]) -> bool:
    policy = manifest.get("persistence_policy", {})
    return (
        policy.get("mode") == "approved_projection"
        and policy.get("bulk_snapshot_import_enabled") is True
    )


def _clean(value: object, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _jurisdiction(
    state_code: str,
    jurisdiction_classification: dict[str, list[str]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return published geography without treating HRSA's XX as a state."""
    state_codes = set(jurisdiction_classification["state_codes"])
    territory_codes = set(jurisdiction_classification["territory_codes"])
    if state_code in territory_codes:
        return (
            {"level": "territory", "country_code": "US", "state_code": state_code},
            {"core.geography.state_code": state_code},
        )
    if state_code in state_codes:
        return (
            {"level": "state", "country_code": "US", "state_code": state_code},
            {
                "core.geography.jurisdiction_level": "state",
                "core.geography.country_code": "US",
                "core.geography.state_code": state_code,
            },
        )
    return (
        {"level": "federal", "country_code": "US"},
        {
            "core.geography.jurisdiction_level": "federal",
            "core.geography.country_code": "US",
        },
    )


def _summary(row: dict[str, str], county: str, state_code: str) -> str:
    """Compose searchable referral details from the source's public fields."""
    fields = [
        ("Provider", _clean(row.get("Health Center Name"))),
        ("Center type", _clean(row.get("Health Center Type"))),
    ]
    address = _clean(row.get("Site Address"))
    locality = ", ".join(
        value
        for value in (
            _clean(row.get("Site City")),
            state_code if state_code != "XX" else "",
            _clean(row.get("Site Postal Code")),
        )
        if value
    )
    fields.append(
        ("Address", ", ".join(value for value in (address, locality) if value))
    )
    fields.extend(
        [
            ("County", county),
            ("Phone", _clean(row.get("Site Telephone Number"))),
            ("Website listed by HRSA", _clean(row.get("Site Web Address"))),
            (
                "Typical hours per week",
                _clean(row.get("Operating Hours per Week")),
            ),
            (
                "Setting",
                _clean(
                    row.get(
                        "Health Center Service Delivery Site Location Setting "
                        "Description"
                    )
                ),
            ),
            ("HRSA site status", _clean(row.get("Site Status Description"))),
        ]
    )
    parts = [f"{label}: {value}" for label, value in fields if value]
    if state_code == "XX":
        parts.append("HRSA state code: XX (unclassified in the source).")
    return "; ".join(parts)[:1_200]


def _record(
    row: dict[str, str],
    *,
    source_version: str,
    retrieved_at: str,
    jurisdiction_classification: dict[str, list[str]],
) -> tuple[str, dict[str, Any]]:
    site_id = _clean(row.get("BPHC Assigned Number"), 128)
    site_name = _clean(row.get("Site Name"), 500)
    if not site_id or not site_name:
        raise HrsaImportError("HRSA row is missing its stable site ID or name.")

    state_code = _clean(row.get("Site State Abbreviation"), 2).upper()
    county = _clean(
        row.get("Complete County Name") or row.get("County Equivalent Name"), 100
    )
    jurisdiction, attributes = _jurisdiction(state_code, jurisdiction_classification)
    attributes.update(
        {
            "listing.title": site_name,
            "listing.agency": _clean(row.get("Health Center Name"), 500)
            or "HRSA Health Center Program site",
            "listing.summary": _summary(row, county, state_code),
        }
    )
    if county and jurisdiction["level"] in {"state", "territory"}:
        attributes["core.geography.county_name"] = county

    provenance = {
        attribute_id: {
            "source_url": CSV_URL,
            "observed_at": retrieved_at,
            "extraction_method": "operator_review",
        }
        for attribute_id in attributes
    }
    record: dict[str, Any] = {
        "source_record_id": site_id,
        "record_type": "service_referral",
        "canonical_attributes": attributes,
        "semantic_context": {
            "catalog_namespace": "programs",
            "record_role": "referral",
            "criteria_coverage": "not_assessed",
            "service_scope": "primary_health_center_locator",
            "jurisdiction": jurisdiction,
        },
        "source_metadata": {
            "source_id": SOURCE_ID,
            "source_type": "public_source",
            "approval_status": "approved",
            "publisher": (
                "Health Resources and Services Administration (HRSA), "
                "Bureau of Primary Health Care"
            ),
            "source_title": "Health Center Service Delivery and Look-Alike Sites",
            "source_version": source_version,
            "retrieved_at": retrieved_at,
            "effective_at": None,
            "transform_version": "hrsa-hcsd-referrals-v1",
            "criteria_status": "not_assessed",
            "criteria_available": False,
            "raw_payload_stored": False,
            "field_provenance": provenance,
        },
        "source_url": CSV_URL,
        "schema_version": LISTING_SCHEMA,
        "observed_at": retrieved_at,
    }
    record["content_hash"] = hashlib.sha256(
        json.dumps(
            record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    return site_id, record


def _partition_rows(
    rows: list[dict[str, str]], datasets: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    by_region = {
        str(dataset.get("metadata", {}).get("hhs_region_code")): dataset["dataset_id"]
        for dataset in datasets
        if dataset.get("metadata", {}).get("hhs_region_code")
    }
    unreported = next(
        (
            dataset["dataset_id"]
            for dataset in datasets
            if dataset.get("metadata", {}).get("partition") == "hhs_region_not_reported"
        ),
        None,
    )
    if not unreported:
        raise HrsaImportError("Manifest lacks the unreported-region dataset.")
    partitions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        region = _clean(row.get("HHS Region Code"), 2)
        dataset_id = by_region.get(region) if region else unreported
        if dataset_id is None:
            raise HrsaImportError("HRSA CSV contains an undeclared HHS region.")
        partitions[dataset_id].append(row)
    for dataset_id, records in partitions.items():
        if len(records) > MAX_DATASET_RECORDS:
            raise HrsaImportError(
                f"Dataset {dataset_id} exceeds the Data API's 5,000-record cap."
            )
    return dict(partitions)


def _read_csv(path: Path) -> tuple[list[dict[str, str]], str]:
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text, newline="")))
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise HrsaImportError("HRSA CSV could not be read as UTF-8 CSV.") from exc
    required = {
        "BPHC Assigned Number",
        "Site Name",
        "Site State Abbreviation",
        "HHS Region Code",
    }
    if not rows or not required.issubset(rows[0]):
        raise HrsaImportError("HRSA CSV is missing required documented columns.")
    return rows, hashlib.sha256(raw).hexdigest()


def _request_json(
    client: httpx.Client, method: str, path: str, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        response = client.request(method, path, json=payload)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HrsaImportError(
            f"Data API request failed at {path}; response details withheld."
        ) from exc
    if not isinstance(body, dict):
        raise HrsaImportError(f"Data API returned an invalid response at {path}.")
    return body


def _payload(
    records: list[dict[str, Any]], source_version: str, retrieved_at: str
) -> dict[str, Any]:
    return {
        "outcome": "succeeded",
        "source_version": source_version,
        "retrieved_at": retrieved_at,
        "transform_version": "hrsa-hcsd-referrals-v1",
        "validation_outcome": "valid",
        "detail": (
            "Complete HRSA service-delivery referral snapshot partition; "
            "no eligibility criteria or outcome is inferred."
        ),
        "record_contract_version": RECORD_CONTRACT,
        "rejected_count": 0,
        "rejection_reasons": [],
        "records": records,
    }


def run_import(
    *,
    manifest_path: Path,
    csv_path: Path,
    source_last_modified: str,
    retrieved_at: str,
    api_url: str,
    write_token: str,
    apply: bool,
) -> dict[str, Any]:
    """Validate the snapshot; refuse bulk persistence unless policy allows it."""
    manifest = _read_manifest(manifest_path)
    if apply and not _bulk_snapshot_import_enabled(manifest):
        raise HrsaImportError(
            "Bulk HRSA snapshot persistence is disabled; keep source rows upstream "
            "and use a reviewed source-query adapter."
        )
    rows, csv_sha256 = _read_csv(csv_path)
    registration = manifest["registration"]
    datasets = registration["datasets"]
    partitions = _partition_rows(rows, datasets)
    source_version = (
        f"HRSA CSV SHA-256 {csv_sha256}; Last-Modified {source_last_modified.strip()}"
    )[:200]
    classification = manifest["transform"]["jurisdiction_classification"]
    records_by_dataset: dict[str, list[dict[str, Any]]] = {}
    seen_ids: set[str] = set()
    for dataset_id, partition in partitions.items():
        records = []
        for row in partition:
            site_id, record = _record(
                row,
                source_version=source_version,
                retrieved_at=retrieved_at,
                jurisdiction_classification=classification,
            )
            if site_id in seen_ids:
                raise HrsaImportError("HRSA CSV repeats a BPHC Assigned Number.")
            seen_ids.add(site_id)
            records.append(record)
        records_by_dataset[dataset_id] = records

    batches = {
        dataset_id: [
            _payload(
                records[index : index + MAX_BATCH_RECORDS], source_version, retrieved_at
            )
            for index in range(0, len(records), MAX_BATCH_RECORDS)
        ]
        for dataset_id, records in records_by_dataset.items()
    }
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "source_id": SOURCE_ID,
        "csv_sha256": csv_sha256,
        "source_last_modified": source_last_modified,
        "retrieved_at": retrieved_at,
        "record_count": len(seen_ids),
        "dataset_counts": {
            dataset_id: len(records)
            for dataset_id, records in sorted(records_by_dataset.items())
        },
        "batch_count": sum(map(len, batches.values())),
        "eligibility_criteria_created": 0,
    }
    if not apply:
        return report
    if not write_token.strip():
        raise HrsaImportError("PUBLIC_CATALOG_WRITE_TOKEN is not configured.")

    api_origin = _api_origin(api_url)
    headers = {"Authorization": f"Bearer {write_token.strip()}"}
    with httpx.Client(
        base_url=api_origin,
        headers=headers,
        timeout=httpx.Timeout(30.0),
        follow_redirects=False,
    ) as client:
        registration_check = _request_json(
            client,
            "POST",
            "/v1/public-catalog/registrations/validate",
            registration,
        )
        expected_ids = {dataset["dataset_id"] for dataset in datasets}
        if (
            registration_check.get("status") != "valid"
            or set(registration_check.get("dataset_ids", [])) != expected_ids
        ):
            raise HrsaImportError(
                "Data API rejected HRSA source registration validation."
            )

        registration_result = _request_json(
            client, "POST", "/v1/public-catalog/registrations", registration
        )
        registered = {
            item.get("dataset_id")
            for item in registration_result.get("datasets", [])
            if isinstance(item, dict)
            and item.get("status") in {"registered", "updated", "unchanged"}
        }
        if registered != expected_ids:
            raise HrsaImportError(
                "Data API did not confirm every HRSA dataset registration."
            )

        # Validate the whole snapshot before writing any records.
        batch_order = [
            (dataset_id, batch)
            for dataset_id in sorted(batches)
            for batch in batches[dataset_id]
        ]
        for dataset_id, payload in batch_order:
            checked = _request_json(
                client,
                "POST",
                (
                    f"/v1/public-datasets/{quote(dataset_id, safe='')}"
                    "/ingestion-runs/validate"
                ),
                payload,
            )
            if (
                checked.get("status") != "valid"
                or checked.get("accepted_count") != len(payload["records"])
                or checked.get("rejected_count") != 0
            ):
                raise HrsaImportError(
                    "Data API rejected a complete batch for "
                    f"{dataset_id}; no records were applied."
                )

        run_counts: dict[str, dict[str, int]] = defaultdict(
            lambda: {"inserted_count": 0, "updated_count": 0, "duplicate_count": 0}
        )
        for dataset_id, payload in batch_order:
            ingested = _request_json(
                client,
                "POST",
                f"/v1/public-datasets/{quote(dataset_id, safe='')}/ingestion-runs",
                payload,
            )
            expected_count = len(payload["records"])
            keys = ("inserted_count", "updated_count", "duplicate_count")
            counts = {key: ingested.get(key) for key in keys}
            if (
                ingested.get("status") != "succeeded"
                or ingested.get("catalog_update_status") != "applied"
                or ingested.get("rejected_count") != 0
                or any(
                    not isinstance(value, int) or value < 0 for value in counts.values()
                )
                or sum(counts.values()) != expected_count
            ):
                raise HrsaImportError(
                    "Data API did not confirm the complete batch for "
                    f"{dataset_id}; rerun with the same retrieved-at value "
                    "to resume safely."
                )
            for key, value in counts.items():
                run_counts[dataset_id][key] += value
        report["registration_status"] = "confirmed"
        report["ingestion_counts"] = dict(sorted(run_counts.items()))
    return report


def main() -> int:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv", type=Path, required=True, help="HRSA public CSV snapshot."
    )
    parser.add_argument(
        "--source-last-modified",
        required=True,
        help="HRSA HTTP Last-Modified value for this snapshot.",
    )
    parser.add_argument(
        "--retrieved-at",
        help="UTC timestamp pinned for exact replay; required with --apply.",
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
    )
    parser.add_argument(
        "--write-token-file",
        type=Path,
        default=Path.home() / ".config/mcp-saas/public-catalog-write-token",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the full snapshot only if the manifest explicitly permits it.",
    )
    args = parser.parse_args()
    if args.apply:
        try:
            manifest = _read_manifest(args.manifest)
        except HrsaImportError as exc:
            parser.error(str(exc))
        if not _bulk_snapshot_import_enabled(manifest):
            parser.error(
                "Bulk HRSA snapshot persistence is disabled; keep source rows "
                "upstream and use a reviewed source-query adapter."
            )
    retrieved_at = (
        args.retrieved_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    )
    if args.apply and not args.retrieved_at:
        parser.error(
            "--retrieved-at is required with --apply so a retry can be an exact replay."
        )
    write_token = ""
    if args.apply:
        try:
            write_token = args.write_token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(
                "Public catalog writer token file is unavailable."
            ) from exc
    try:
        report = run_import(
            manifest_path=args.manifest,
            csv_path=args.csv,
            source_last_modified=args.source_last_modified,
            retrieved_at=retrieved_at,
            api_url=args.api_url,
            write_token=write_token,
            apply=args.apply,
        )
    except HrsaImportError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
