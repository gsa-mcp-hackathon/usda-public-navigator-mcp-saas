"""Fetch approved public pages and send normalized records through the Data API."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from clusters.eligibility_ingest import (
    SourceBlocked,
    SourceInvalid,
    SourceUnavailable,
    fetch_source,
    load_source_specs,
)
from clusters.eligibility_persistence import (
    PersistenceApiUnavailable,
    post_source_outcome,
    read_source_dataset,
)


def main() -> int:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
        help=(
            "Persistence Access API origin; no credentials are accepted on "
            "the command line."
        ),
    )
    parser.add_argument(
        "--write-token-file",
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
        help=(
            "Write complete records and explicit failed outcomes through the Data API."
        ),
    )
    args = parser.parse_args()

    results: list[dict[str, object]] = []
    for spec in load_source_specs():
        try:
            source = fetch_source(spec)
        except SourceBlocked:
            outcome = (
                "blocked",
                "unavailable",
                "Source access was denied or redirected.",
            )
            source = None
        except SourceInvalid:
            outcome = (
                "failed",
                "invalid",
                "Source content changed or failed the reviewed contract.",
            )
            source = None
        except SourceUnavailable:
            outcome = (
                "failed",
                "unavailable",
                "Source could not be fetched completely.",
            )
            source = None
        else:
            outcome = (
                "succeeded",
                "valid",
                "Complete source page validated and normalized.",
            )

        result: dict[str, object] = {
            "source_id": spec.source_id,
            "dataset_id": spec.dataset_id,
            "status": outcome[0] if source is None else "ready",
            "record_count": 0 if source is None else 1,
            "source_version": None if source is None else source.source_version,
            "retrieved_at": None if source is None else source.retrieved_at.isoformat(),
            "mode": "apply" if args.apply else "dry_run",
        }
        if args.apply:
            try:
                write_result = post_source_outcome(
                    args.api_url,
                    args.write_token_file,
                    spec.dataset_id,
                    outcome=outcome[0],
                    validation_outcome=outcome[1],
                    detail=outcome[2],
                    source=source,
                )
                result["status"] = write_result["status"]
                result["ingestion_run_id"] = write_result["run_id"]
                result["inserted_count"] = write_result["inserted_count"]
                result["updated_count"] = write_result["updated_count"]
                result["duplicate_count"] = write_result["duplicate_count"]
                if source is not None:
                    readback = read_source_dataset(
                        args.api_url, args.reader_token_file, spec.dataset_id
                    )
                    records = readback["records"]
                    if not any(
                        record.get("content_hash") == source.content_hash
                        for record in records
                    ):
                        result["status"] = "readback_mismatch"
                    else:
                        result["readback_count"] = len(records)
            except PersistenceApiUnavailable:
                result["status"] = "persistence_api_unavailable"
        results.append(result)

    print(json.dumps({"results": results}, indent=2, sort_keys=True))
    accepted = {"ready"} if not args.apply else {"succeeded"}
    return 0 if all(item["status"] in accepted for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
