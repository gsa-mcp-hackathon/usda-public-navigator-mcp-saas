"""Dry-run or explicitly ingest a bounded Grants.gov listing snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from clusters.grants_gov_inventory import (
    DATASET_ID,
    MAX_ROWS,
    SOURCE_VERSION,
    TRANSFORM_VERSION,
    fetch_current_opportunities,
)

MAX_INGESTION_BATCH_BYTES = 60_000


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.path not in {"", "/"}:
        raise ValueError("--api-url must be an API origin without a path.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        raise ValueError("Use HTTPS or a trusted local API origin.")
    return value.rstrip("/")


def _read_token(path: Path) -> str:
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("Public catalog writer token file is empty.")
    return token


def _payload(
    snapshot: dict[str, object], records: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "outcome": "succeeded",
        "source_version": SOURCE_VERSION,
        "retrieved_at": snapshot["retrieved_at"],
        "transform_version": TRANSFORM_VERSION,
        "validation_outcome": "valid",
        "detail": (
            "Bounded posted Grants.gov listings normalized; criteria not assessed."
        ),
        "records": records,
    }


def _encoded_payload(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _payload_batches(snapshot: dict[str, object]) -> list[dict[str, object]]:
    """Pack records into API-sized chunks while preserving their order."""
    records = snapshot["records"]
    if not isinstance(records, list) or not records:
        raise ValueError("Grants.gov snapshot must contain a non-empty record list.")
    batches: list[dict[str, object]] = []
    batch_records: list[dict[str, object]] = []
    for record in records:
        candidate = _payload(snapshot, [*batch_records, record])
        if len(_encoded_payload(candidate)) <= MAX_INGESTION_BATCH_BYTES:
            batch_records.append(record)
            continue
        if not batch_records:
            raise ValueError("A Grants.gov record exceeds the ingestion batch limit.")
        batches.append(_payload(snapshot, batch_records))
        batch_records = [record]
        if (
            len(_encoded_payload(_payload(snapshot, batch_records)))
            > MAX_INGESTION_BATCH_BYTES
        ):
            raise ValueError("A Grants.gov record exceeds the ingestion batch limit.")
    if batch_records:
        batches.append(_payload(snapshot, batch_records))
    return batches


def _ingest(
    api_url: str,
    writer_token_file: Path,
    reader_token_file: Path,
    snapshot: dict[str, object],
) -> dict[str, object]:
    records = snapshot["records"]
    batches = _payload_batches(snapshot)
    origin = _origin(api_url)
    path = f"/v1/public-datasets/{quote(DATASET_ID, safe='')}/ingestion-runs"
    try:
        writer_token = _read_token(writer_token_file)
        run_ids: list[str] = []
        totals = {"inserted_count": 0, "updated_count": 0, "duplicate_count": 0}
        for payload in batches:
            response = httpx.post(
                origin + path,
                json=payload,
                headers={"Authorization": f"Bearer {writer_token}"},
                timeout=15.0,
            )
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("status") != "succeeded":
                raise ValueError("Data API returned an unexpected ingestion status.")
            run_id = result.get("run_id")
            if isinstance(run_id, str):
                run_ids.append(run_id)
            for count_name in totals:
                count = result.get(count_name, 0)
                if not isinstance(count, int) or count < 0:
                    raise ValueError("Data API returned an invalid ingestion count.")
                totals[count_name] += count

        readback_response = httpx.get(
            origin + f"/v1/public-datasets/{quote(DATASET_ID, safe='')}/records",
            headers={"Authorization": f"Bearer {_read_token(reader_token_file)}"},
            timeout=15.0,
        )
        readback_response.raise_for_status()
        readback = readback_response.json()
        if (
            not isinstance(readback, dict)
            or readback.get("status") != "ok"
            or readback.get("dataset", {}).get("dataset_id") != DATASET_ID
            or not isinstance(readback.get("records"), list)
        ):
            raise ValueError("Data API returned an unexpected dataset readback.")
        actual = {
            (item.get("source_record_id"), item.get("content_hash"))
            for item in readback["records"]
            if isinstance(item, dict)
        }
        expected = {
            (item["source_record_id"], item["content_hash"]) for item in records
        }
        if not expected <= actual:
            raise ValueError("Data API readback did not contain the ingested snapshot.")
        return {
            "status": "succeeded",
            "run_ids": run_ids,
            "batch_count": len(batches),
            **totals,
            "readback_count": len(expected),
        }
    except (OSError, ValueError, TypeError, httpx.HTTPError) as exc:
        raise RuntimeError("Grants.gov Data API ingestion/readback failed.") from exc


def main() -> int:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
    )
    parser.add_argument("--limit", type=int, default=MAX_ROWS)
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
        help="Write through the scoped public-catalog Data API and verify readback.",
    )
    args = parser.parse_args()
    if not 1 <= args.limit <= MAX_ROWS:
        parser.error(f"--limit must be between 1 and {MAX_ROWS}.")
    try:
        snapshot = fetch_current_opportunities(limit=args.limit)
        result: dict[str, object] = {
            "status": "ready",
            "mode": "apply" if args.apply else "dry_run",
            "dataset_id": DATASET_ID,
            "query": snapshot["query"],
            "hit_count": snapshot["hit_count"],
            "record_count": len(snapshot["records"]),
            "criteria_available": False,
            "non_endorsement_notice_included": True,
        }
        if args.apply:
            result.update(
                _ingest(
                    args.api_url,
                    args.writer_token_file,
                    args.reader_token_file,
                    snapshot,
                )
            )
    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
        print(f"Grants.gov seed failed ({type(exc).__name__}).", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
