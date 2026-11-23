"""Dry-run or import an operator-provided SAM Assistance Listings CSV snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from clusters.eligibility_discovery import dataset_catalog_from_payload
from clusters.sam_assistance_inventory import (
    DATASET_ID,
    MAX_BATCH_BYTES,
    MAX_RECORDS_PER_BATCH,
    SOURCE_ID,
    build_snapshot,
)

READBACK_PAGE_SIZE = 1000
GRANTS_GOV_DATASET_ID = "grants-gov-current-opportunities"
GRANTS_GOV_SOURCE_ID = "grants.gov.opportunities"


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("--api-url must be an API origin without a path.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        raise ValueError("Use HTTPS or a trusted local API origin.")
    return value.rstrip("/")


def _token(path: Path) -> str:
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("Public catalog token file is empty.")
    return value


def _read_all(
    client: httpx.Client, origin: str, dataset_id: str, token: str
) -> dict[str, object]:
    path = f"/v1/public-datasets/{quote(dataset_id, safe='')}/records"
    offset = 0
    all_records: list[dict[str, object]] = []
    first: dict[str, object] | None = None
    expected_total: int | None = None
    while True:
        response = client.get(
            origin + path,
            params={"limit": READBACK_PAGE_SIZE, "offset": offset},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        page = response.json()
        if not isinstance(page, dict) or page.get("status") != "ok":
            raise ValueError("Data API returned an invalid public dataset readback.")
        dataset = page.get("dataset")
        rows = page.get("records")
        pagination = page.get("pagination")
        if (
            not isinstance(dataset, dict)
            or dataset.get("dataset_id") != dataset_id
            or not isinstance(rows, list)
            or not isinstance(pagination, dict)
            or pagination.get("offset") != offset
            or pagination.get("limit") != READBACK_PAGE_SIZE
            or not isinstance(pagination.get("total_count"), int)
        ):
            raise ValueError("Data API readback has invalid pagination metadata.")
        if first is None:
            first = page
            expected_total = pagination["total_count"]
        elif (
            dataset != first.get("dataset")
            or pagination["total_count"] != expected_total
        ):
            raise ValueError("Data API dataset changed during paginated readback.")
        if any(not isinstance(item, dict) for item in rows):
            raise ValueError("Data API readback contains a malformed record.")
        all_records.extend(rows)
        if len(all_records) > pagination["total_count"]:
            raise ValueError("Data API pagination returned more rows than its total.")
        next_offset = pagination.get("next_offset")
        if next_offset is None:
            if len(all_records) != pagination["total_count"]:
                raise ValueError("Data API pagination ended before full readback.")
            break
        if next_offset != len(all_records) or next_offset <= offset:
            raise ValueError("Data API pagination did not advance monotonically.")
        offset = next_offset
    assert first is not None
    first["records"] = all_records
    return first


def _readback_snapshot(
    client: httpx.Client, origin: str, reader_token: str, snapshot: dict[str, object]
) -> dict[str, object]:
    readback = _read_all(client, origin, DATASET_ID, reader_token)
    metadata = readback.get("dataset")
    records = readback.get("records")
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema_version") != "public-listing-v4"
        or not isinstance(records, list)
    ):
        raise ValueError("SAM dataset readback has the wrong version or record shape.")
    expected = {
        (item["source_record_id"], item["content_hash"]) for item in snapshot["records"]
    }
    actual = {
        (item.get("source_record_id"), item.get("content_hash"))
        for item in records
        if isinstance(item, dict)
    }
    if actual != expected or len(actual) != len(records):
        raise ValueError("Full SAM dataset readback does not equal the input snapshot.")
    for item in records:
        source_metadata = item.get("source_metadata")
        if (
            not isinstance(source_metadata, dict)
            or source_metadata.get("source_id") != SOURCE_ID
            or source_metadata.get("source_snapshot_sha256")
            != snapshot["snapshot_sha256"]
            or source_metadata.get("criteria_status") != "not_assessed"
        ):
            raise ValueError("SAM readback provenance or criteria boundary is invalid.")
    freshness = readback.get("freshness")
    if (
        not isinstance(freshness, dict)
        or freshness.get("health_status") != "healthy"
        or freshness.get("freshness_status") != "fresh_as_of_last_check"
        or freshness.get("source_version") != snapshot["source_version"]
    ):
        raise ValueError(
            "SAM source health does not show the imported snapshot as current."
        )
    return {
        "readback_count": len(actual),
        "schema_version": metadata["schema_version"],
        "source_health": freshness["health_status"],
        "freshness": freshness["freshness_status"],
        "source_version": freshness["source_version"],
    }


def _verify_grants_gov(
    client: httpx.Client, origin: str, reader_token: str
) -> dict[str, object]:
    readback = _read_all(client, origin, GRANTS_GOV_DATASET_ID, reader_token)
    dataset = readback.get("dataset")
    records = readback.get("records")
    if (
        not isinstance(dataset, dict)
        or dataset.get("schema_version") != "public-listing-v1"
        or not isinstance(records, list)
        or not records
    ):
        raise ValueError(
            "Existing Grants.gov v1 records are unavailable after v2 rollout."
        )
    projected = dataset_catalog_from_payload(
        readback,
        expected_dataset_id=GRANTS_GOV_DATASET_ID,
        expected_source_id=GRANTS_GOV_SOURCE_ID,
    )
    if len(projected.listings) != len(records):
        raise ValueError("Grants.gov v1 compatibility projection lost records.")
    return {"schema_version": dataset["schema_version"], "readback_count": len(records)}


def _apply(
    api_url: str, writer_file: Path, reader_file: Path, snapshot: dict[str, object]
) -> dict[str, object]:
    if snapshot["rejected_count"]:
        raise ValueError("Refusing import while the source snapshot has rejected rows.")
    origin = _origin(api_url)
    writer_token = _token(writer_file)
    reader_token = _token(reader_file)
    run_ids: list[str] = []
    totals = {"inserted_count": 0, "updated_count": 0, "duplicate_count": 0}
    batch_reports: list[dict[str, int]] = []
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=8.0)) as client:
        for index, payload in enumerate(snapshot["batches"]):
            encoded_size = len(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if (
                len(payload["records"]) > MAX_RECORDS_PER_BATCH
                or encoded_size > MAX_BATCH_BYTES
            ):
                raise ValueError("Prepared batch exceeds its configured safety bound.")
            response = client.post(
                origin
                + f"/v1/public-datasets/{quote(DATASET_ID, safe='')}/ingestion-runs",
                json=payload,
                headers={"Authorization": f"Bearer {writer_token}"},
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("status") != "succeeded":
                raise ValueError(f"SAM batch {index + 1} did not succeed.")
            run_id = result.get("run_id")
            if isinstance(run_id, str):
                run_ids.append(run_id)
            for name in totals:
                count = result.get(name, 0)
                if not isinstance(count, int) or count < 0:
                    raise ValueError("Data API returned an invalid write count.")
                totals[name] += count
            batch_reports.append(
                {"records": len(payload["records"]), "bytes": encoded_size}
            )

            # Replay one bounded batch as a live idempotency proof before continuing.
            if index == 0:
                replay = client.post(
                    origin
                    + "/v1/public-datasets/"
                    + quote(DATASET_ID, safe="")
                    + "/ingestion-runs",
                    json=payload,
                    headers={"Authorization": f"Bearer {writer_token}"},
                )
                replay.raise_for_status()
                replay_result = replay.json()
                if (
                    not isinstance(replay_result, dict)
                    or replay_result.get("status") != "succeeded"
                    or replay_result.get("inserted_count") != 0
                    or replay_result.get("updated_count") != 0
                    or replay_result.get("duplicate_count") != len(payload["records"])
                ):
                    raise ValueError("SAM batch replay did not prove idempotency.")
                replay_id = replay_result.get("run_id")
                if isinstance(replay_id, str):
                    run_ids.append(replay_id)

        sam_readback = _readback_snapshot(client, origin, reader_token, snapshot)
        grants_readback = _verify_grants_gov(client, origin, reader_token)
    return {
        "status": "succeeded",
        "run_ids": run_ids,
        "batch_count": len(batch_reports),
        "batch_reports": batch_reports,
        "write_totals": totals,
        "idempotency_replay": "passed",
        "sam": sam_readback,
        "grants_gov_compatibility": grants_readback,
    }


def main() -> int:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        required=True,
        help="Operator-provided official CSV snapshot.",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
    )
    parser.add_argument(
        "--writer-token-file",
        type=Path,
        default=Path.home() / ".config/mcp-saas/public-catalog-write-token",
    )
    parser.add_argument(
        "--reader-token-file",
        type=Path,
        default=Path.home() / ".config/mcp-saas/catalog-read-token",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write bounded batches and verify full readback.",
    )
    args = parser.parse_args()
    try:
        snapshot = build_snapshot(args.csv)
        manifest: dict[str, object] = {
            "status": "ready" if snapshot["rejected_count"] == 0 else "needs_review",
            "mode": "apply" if args.apply else "dry_run",
            "dataset_id": DATASET_ID,
            "source_id": SOURCE_ID,
            "schema_version": snapshot["schema_version"],
            "snapshot_sha256": snapshot["snapshot_sha256"],
            "encoding": snapshot["encoding"],
            "input_row_count": snapshot["input_row_count"],
            "accepted_count": snapshot["accepted_count"],
            "rejected_count": snapshot["rejected_count"],
            "rejected_rows": snapshot["rejected_rows"],
            "warning_count": snapshot["warning_count"],
            "batch_count": len(snapshot["batches"]),
            "batch_reports": [
                {
                    "records": len(batch["records"]),
                    "bytes": len(
                        json.dumps(
                            batch, ensure_ascii=False, separators=(",", ":")
                        ).encode("utf-8")
                    ),
                }
                for batch in snapshot["batches"]
            ],
            "criteria_status": "not_assessed",
            "raw_source_file_persisted": False,
        }
        if args.apply:
            manifest.update(
                _apply(
                    args.api_url,
                    args.writer_token_file,
                    args.reader_token_file,
                    snapshot,
                )
            )
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(
            f"SAM Assistance Listings import failed ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
