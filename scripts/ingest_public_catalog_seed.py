"""Validate and apply one approved normalized catalog seed dataset."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from clusters.eligibility_persistence import (
    PersistenceApiUnavailable,
    read_source_dataset,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_ROOT = (REPO_ROOT / "configs/eligibility/catalog-seeds").resolve()
HELD_SEED_ROOT = (REPO_ROOT / "configs/eligibility/held-catalog-seeds").resolve()
UPDATE_PLAN_ROOT = (REPO_ROOT / "configs/eligibility/catalog-seeds").resolve()
MAX_SEED_BYTES = 1_800_000
MAX_UPDATE_PLAN_BYTES = 16_000
ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "mcp-saas-vm-data-api"}
SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CatalogSeedError(RuntimeError):
    """A catalog seed failed validation or its bounded API transaction."""


def _api_origin(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.path not in {"", "/"}:
        raise CatalogSeedError("Data API URL must be an origin without a path.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in ALLOWED_LOCAL_HOSTS
    ):
        raise CatalogSeedError("Data API must use HTTPS or a trusted local origin.")
    return value.rstrip("/")


def _validate_supported_provenance_methods(records: list[object]) -> None:
    """Reject normalized records that use methods outside the catalog contract."""
    schema_path = REPO_ROOT / "registries/public-catalog-v1.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        methods = schema["$defs"]["NormalizedPublicRecord"]["properties"][
            "source_metadata"
        ]["properties"]["field_provenance"]["additionalProperties"]["properties"][
            "extraction_method"
        ]["enum"]
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as exc:
        raise CatalogSeedError("Public catalog record contract is unreadable.") from exc
    if (
        not isinstance(methods, list)
        or not methods
        or not all(isinstance(method, str) for method in methods)
    ):
        raise CatalogSeedError(
            "Public catalog record contract has no method allowlist."
        )

    allowed = set(methods)
    required_fields = {"source_url", "observed_at", "extraction_method"}
    for record in records:
        metadata = record.get("source_metadata") if isinstance(record, dict) else None
        provenance = (
            metadata.get("field_provenance") if isinstance(metadata, dict) else None
        )
        if not isinstance(provenance, dict) or not provenance:
            raise CatalogSeedError("Seed record has no field provenance map.")
        if any(
            not isinstance(item, dict)
            or set(item) != required_fields
            or item.get("extraction_method") not in allowed
            for item in provenance.values()
        ):
            raise CatalogSeedError(
                "Seed record has field provenance outside the public catalog contract."
            )


def _load_selected_dataset(
    seed_path: Path, dataset_id: str, source_record_id: str | None = None
) -> tuple[dict, dict]:
    resolved = seed_path.resolve()
    if (
        not (
            resolved.is_relative_to(SEED_ROOT)
            or resolved.is_relative_to(HELD_SEED_ROOT)
        )
        or not resolved.is_file()
    ):
        raise CatalogSeedError(
            "Seed file must be inside the checked-in catalog-seeds folder."
        )
    if resolved.stat().st_size > MAX_SEED_BYTES:
        raise CatalogSeedError("Catalog seed exceeds the registration body limit.")
    try:
        seed = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogSeedError("Catalog seed is not readable JSON.") from exc

    registration = seed.get("registration")
    if (
        not isinstance(registration, dict)
        or registration.get("contract_version") != "public-source-registration-v1"
    ):
        raise CatalogSeedError("Catalog seed has no supported source registration.")
    datasets = registration.get("datasets")
    selected = [
        item
        for item in datasets or []
        if isinstance(item, dict) and item.get("dataset_id") == dataset_id
    ]
    if len(selected) != 1:
        raise CatalogSeedError(
            "Confirmed dataset is not uniquely declared by the seed."
        )

    ingestion_runs = seed.get("ingestion_runs")
    selected_runs = [
        item
        for item in ingestion_runs or []
        if isinstance(item, dict) and item.get("dataset_id") == dataset_id
    ]
    if len(selected_runs) != 1 or not isinstance(selected_runs[0].get("payload"), dict):
        raise CatalogSeedError(
            "Seed must contain exactly one payload for the confirmed dataset."
        )
    payload = selected_runs[0]["payload"]
    if (
        payload.get("outcome") != "succeeded"
        or payload.get("validation_outcome") != "valid"
    ):
        raise CatalogSeedError(
            "Only complete, locally validated seed snapshots may be applied."
        )
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise CatalogSeedError(
            "Seed dataset must contain at least one normalized record."
        )
    if source_record_id is not None:
        selected_records = [
            record
            for record in records
            if isinstance(record, dict)
            and record.get("source_record_id") == source_record_id
        ]
        if len(selected_records) != 1:
            raise CatalogSeedError(
                "Confirmed source record is not unique in the selected dataset."
            )
        payload = {**payload, "records": selected_records}

    if payload.get("record_contract_version") == "normalized-public-record-v1":
        _validate_supported_provenance_methods(payload["records"])

    registration_for_dataset = {
        **registration,
        "datasets": selected,
    }
    return registration_for_dataset, payload


def load_public_catalog_seed_record(
    seed_path: Path, dataset_id: str, source_record_id: str
) -> dict:
    """Return one validated normalized record from an exact checked-in seed."""
    _registration, payload = _load_selected_dataset(
        seed_path, dataset_id, source_record_id
    )
    return payload["records"][0]


def _request_json(
    client: httpx.Client,
    method: str,
    path: str,
    payload: dict,
) -> dict:
    try:
        response = client.request(method, path, json=payload)
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPStatusError as exc:
        error_code = None
        try:
            response_body = exc.response.json()
        except ValueError:
            response_body = None
        if isinstance(response_body, dict):
            candidate = response_body.get("error")
            if isinstance(candidate, str) and SAFE_ERROR_CODE.fullmatch(candidate):
                error_code = candidate
        status_detail = f"HTTP {exc.response.status_code}"
        if error_code is not None:
            status_detail += f", error={error_code}"
        if isinstance(response_body, dict):
            reasons = response_body.get("rejection_reasons") or response_body.get(
                "reasons"
            )
            if isinstance(reasons, list):
                safe = [
                    str(item)[:160]
                    for item in reasons[:5]
                    if isinstance(item, (str, int))
                    or (isinstance(item, dict) and item.get("code"))
                ]
                if safe:
                    status_detail += f", reasons={safe}"
        raise CatalogSeedError(
            f"Data API {method} request failed at {path} ({status_detail})."
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise CatalogSeedError(
            f"Data API {method} request failed at {path} "
            "(transport or invalid JSON; response details withheld)."
        ) from exc
    if not isinstance(result, dict):
        raise CatalogSeedError(f"Data API returned an invalid response at {path}.")
    return result


def _load_update_plan(
    plan_path: Path,
    seed_path: Path,
    dataset_id: str,
    source_record_id: str | None,
    record: dict,
) -> dict:
    """Validate an exact-record update plan and its expected content digests."""
    resolved = plan_path.resolve()
    if (
        not resolved.is_relative_to(UPDATE_PLAN_ROOT)
        or not resolved.is_file()
        or resolved.stat().st_size > MAX_UPDATE_PLAN_BYTES
    ):
        raise CatalogSeedError("Update plan must be a small checked-in catalog plan.")
    try:
        plan = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogSeedError("Catalog update plan is not readable JSON.") from exc
    expected_keys = {
        "manifest_version",
        "plan_id",
        "target",
        "seed_path",
        "expected_before_content_sha256",
        "expected_after_content_sha256",
        "write_policy",
    }
    if not isinstance(plan, dict) or set(plan) != expected_keys:
        raise CatalogSeedError("Catalog update plan has an unsupported shape.")
    target = plan.get("target")
    policy = plan.get("write_policy")
    if (
        plan.get("manifest_version") != "public-catalog-record-update-plan-v1"
        or not isinstance(plan.get("plan_id"), str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,99}", plan["plan_id"])
        or not isinstance(target, dict)
        or set(target) != {"dataset_id", "source_record_id"}
        or target.get("dataset_id") != dataset_id
        or target.get("source_record_id") != source_record_id
        or source_record_id is None
        or plan.get("seed_path")
        != seed_path.resolve().relative_to(REPO_ROOT).as_posix()
        or not isinstance(policy, dict)
        or policy
        != {
            "mode": "serialized_precondition_readback",
            "server_side_compare_and_swap": False,
            "single_writer_group": "lane-a-hcv-data-write",
        }
    ):
        raise CatalogSeedError("Catalog update plan target or write policy is invalid.")
    before_digest = plan.get("expected_before_content_sha256")
    after_digest = plan.get("expected_after_content_sha256")
    if (
        not isinstance(before_digest, str)
        or SHA256.fullmatch(before_digest) is None
        or not isinstance(after_digest, str)
        or SHA256.fullmatch(after_digest) is None
        or record.get("content_hash") != after_digest
    ):
        raise CatalogSeedError(
            "Catalog update plan digests do not match the exact seed record."
        )
    return plan


def _read_exact_catalog_record(
    api_url: str,
    reader_token_file: Path,
    dataset_id: str,
    source_record_id: str,
    expected_source_id: str,
) -> dict:
    """Read one active record and fail unless all target identities are exact."""
    try:
        result = read_source_dataset(
            api_url,
            reader_token_file,
            dataset_id,
            source_record_id=source_record_id,
        )
    except PersistenceApiUnavailable as exc:
        raise CatalogSeedError("Catalog record readback is unavailable.") from exc
    dataset = result.get("dataset")
    records = result.get("records")
    pagination = result.get("pagination")
    if (
        not isinstance(dataset, dict)
        or dataset.get("dataset_id") != dataset_id
        or dataset.get("source_id") != expected_source_id
        or not isinstance(records, list)
        or len(records) != 1
        or not isinstance(pagination, dict)
        or pagination.get("total_count") != 1
        or not isinstance(records[0], dict)
        or records[0].get("source_record_id") != source_record_id
    ):
        raise CatalogSeedError("Catalog read did not return the exact target record.")
    return records[0]


def _utc_instant(value: object) -> datetime | None:
    """Parse a timezone-aware ISO timestamp as a UTC instant."""
    if not isinstance(value, str):
        return None
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def catalog_record_matches_seed(readback: dict, expected: dict) -> bool:
    """Compare seed-controlled fields, treating equivalent UTC timestamps equally."""
    fields = (
        "source_record_id",
        "record_type",
        "canonical_attributes",
        "semantic_context",
        "source_metadata",
        "source_url",
        "content_hash",
        "schema_version",
    )
    actual_time = _utc_instant(readback.get("observed_at"))
    expected_time = _utc_instant(expected.get("observed_at"))
    return (
        all(readback.get(field) == expected.get(field) for field in fields)
        and actual_time is not None
        and actual_time == expected_time
    )


def apply_catalog_seed(
    seed_path: Path,
    dataset_id: str,
    *,
    api_url: str,
    write_token: str,
    apply: bool,
    source_record_id: str | None = None,
    update_plan_path: Path | None = None,
    reader_token_file: Path | None = None,
) -> dict[str, object]:
    """Dry-run or apply one source registration and its matching dataset snapshot."""
    registration, ingestion = _load_selected_dataset(
        seed_path, dataset_id, source_record_id
    )
    expected_record = ingestion["records"][0] if update_plan_path is not None else None
    update_plan = (
        _load_update_plan(
            update_plan_path,
            seed_path,
            dataset_id,
            source_record_id,
            expected_record,
        )
        if update_plan_path is not None
        else None
    )
    summary: dict[str, object] = {
        "dataset_id": dataset_id,
        "record_count": len(ingestion["records"]),
        "mode": "apply" if apply else "dry_run",
    }
    if source_record_id is not None:
        summary["source_record_id"] = source_record_id
    if update_plan is not None:
        summary["plan_id"] = update_plan["plan_id"]
        summary["before_content_sha256"] = update_plan["expected_before_content_sha256"]
        summary["after_content_sha256"] = update_plan["expected_after_content_sha256"]
    if not apply:
        return summary
    if not write_token.strip():
        raise CatalogSeedError("PUBLIC_CATALOG_WRITE_TOKEN is not configured.")
    if update_plan is not None and reader_token_file is None:
        raise CatalogSeedError("Exact catalog updates require a separate reader token.")

    api_origin = _api_origin(api_url)
    before_record = None
    if update_plan is not None:
        before_record = _read_exact_catalog_record(
            api_origin,
            reader_token_file,
            dataset_id,
            source_record_id,
            registration["source"]["source_id"],
        )
        if (
            before_record.get("content_hash")
            != update_plan["expected_before_content_sha256"]
        ):
            raise CatalogSeedError(
                "Catalog before-digest changed; no update was submitted."
            )

    headers = {"Authorization": f"Bearer {write_token.strip()}"}
    registration_path = "/v1/public-catalog/registrations"
    ingestion_path = f"/v1/public-datasets/{quote(dataset_id, safe='')}/ingestion-runs"
    with httpx.Client(
        base_url=api_origin,
        headers=headers,
        timeout=httpx.Timeout(15.0),
        follow_redirects=False,
    ) as client:
        registration_check = _request_json(
            client,
            "POST",
            registration_path + "/validate",
            registration,
        )
        if registration_check.get(
            "status"
        ) != "valid" or dataset_id not in registration_check.get("dataset_ids", []):
            raise CatalogSeedError(
                "Data API rejected the source registration validation."
            )

        if update_plan is None:
            registration_result = _request_json(
                client, "POST", registration_path, registration
            )
            registration_items = registration_result.get("datasets")
            if not isinstance(registration_items, list) or not any(
                item.get("dataset_id") == dataset_id
                and item.get("status") in {"registered", "updated", "unchanged"}
                for item in registration_items
                if isinstance(item, dict)
            ):
                raise CatalogSeedError(
                    "Data API did not confirm the selected dataset registration."
                )

        ingestion_check = _request_json(
            client, "POST", ingestion_path + "/validate", ingestion
        )
        if (
            ingestion_check.get("status") != "valid"
            or ingestion_check.get("accepted_count") != len(ingestion["records"])
            or ingestion_check.get("rejected_count") != 0
        ):
            raise CatalogSeedError(
                "Data API rejected the normalized dataset validation."
            )

        ingestion_result = _request_json(client, "POST", ingestion_path, ingestion)
    counts = {
        key: ingestion_result.get(key)
        for key in (
            "inserted_count",
            "updated_count",
            "duplicate_count",
            "rejected_count",
        )
    }
    applied_count = sum(
        counts[key]
        for key in ("inserted_count", "updated_count", "duplicate_count")
        if isinstance(counts[key], int) and not isinstance(counts[key], bool)
    )
    if (
        ingestion_result.get("status") != "succeeded"
        or ingestion_result.get("catalog_update_status") != "applied"
        or counts["rejected_count"] != 0
        or applied_count != len(ingestion["records"])
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in counts.values()
        )
    ):
        raise CatalogSeedError(
            "Data API ingestion did not confirm a complete applied batch."
        )
    if update_plan is not None:
        readback = _read_exact_catalog_record(
            api_origin,
            reader_token_file,
            dataset_id,
            source_record_id,
            registration["source"]["source_id"],
        )
        if not catalog_record_matches_seed(readback, expected_record):
            raise CatalogSeedError(
                "Data API catalog readback did not match the exact seed record."
            )
    return {
        **summary,
        "registration_status": "confirmed",
        "ingestion_run_id": ingestion_result.get("run_id"),
        "readback_verified": update_plan is not None,
        **counts,
    }


def main() -> int:
    """Run the catalog seed ingest from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seed",
        type=Path,
        required=True,
        help="Checked-in catalog seed JSON path.",
    )
    parser.add_argument(
        "--dataset-id", required=True, help="Exact dataset ID selected for this run."
    )
    parser.add_argument(
        "--confirm-dataset-id",
        required=True,
        help="Must exactly match --dataset-id before any write is allowed.",
    )
    parser.add_argument(
        "--source-record-id",
        help="Optional exact record ID for a bounded single-record update.",
    )
    parser.add_argument(
        "--update-plan",
        type=Path,
        help=(
            "Checked-in expected-before/after digests for one serial exact-record "
            "update."
        ),
    )
    parser.add_argument(
        "--reader-token-file",
        type=Path,
        help="Protected catalog-reader token file required by --update-plan.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Register and ingest through the Data API.",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
        help="Data API origin; defaults to PERSISTENCE_API_URL or the VM-local API.",
    )
    parser.add_argument(
        "--write-token-file",
        type=Path,
        default=Path.home() / ".config/usda-mcp-saas/public-catalog-write-token",
        help="Protected local file holding the dedicated public catalog writer token.",
    )
    args = parser.parse_args()
    if args.dataset_id != args.confirm_dataset_id:
        raise SystemExit("Confirmation must exactly match the dataset ID.")
    write_token = ""
    reader_token_file = args.reader_token_file
    if args.apply:
        try:
            write_token = args.write_token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SystemExit(
                "Public catalog writer token file is unavailable."
            ) from exc
        if not write_token:
            raise SystemExit("Public catalog writer token file is empty.")
        if args.update_plan is not None and reader_token_file is None:
            raise SystemExit("Exact catalog updates require --reader-token-file.")
    try:
        result = apply_catalog_seed(
            args.seed,
            args.dataset_id,
            api_url=args.api_url,
            write_token=write_token,
            apply=args.apply,
            source_record_id=args.source_record_id,
            update_plan_path=args.update_plan,
            reader_token_file=reader_token_file,
        )
    except CatalogSeedError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
