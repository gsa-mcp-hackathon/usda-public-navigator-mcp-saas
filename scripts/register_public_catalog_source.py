"""Register source and dataset metadata without importing source records."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SEED_ROOT = (REPO_ROOT / "configs/eligibility/catalog-seeds").resolve()
MAX_SEED_BYTES = 1_800_000
ALLOWED_LOCAL_HOSTS = {"localhost", "127.0.0.1", "mcp-saas-vm-data-api"}
REGISTRATION_PATH = "/v1/public-catalog/registrations"
SAFE_API_ERRORS = {
    "invalid_public_source_registration",
    "public_dataset_identity_conflict",
    "public_source_registry_unavailable",
    "source_schema_not_active",
    "unauthorized",
}


class SourceRegistrationError(RuntimeError):
    """A source metadata registration or exact replay failed validation."""


def _api_origin(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.hostname or parsed.path not in {"", "/"}:
        raise SourceRegistrationError("Data API URL must be an origin without a path.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in ALLOWED_LOCAL_HOSTS
    ):
        raise SourceRegistrationError(
            "Data API must use HTTPS or a trusted local origin."
        )
    return value.rstrip("/")


def _load_registration(seed_path: Path) -> dict[str, object]:
    resolved = seed_path.resolve()
    if not resolved.is_relative_to(SEED_ROOT) or not resolved.is_file():
        raise SourceRegistrationError(
            "Seed file must be inside the checked-in catalog-seeds folder."
        )
    if resolved.stat().st_size > MAX_SEED_BYTES:
        raise SourceRegistrationError("Catalog seed exceeds the metadata size limit.")
    try:
        seed = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceRegistrationError("Catalog seed is not readable JSON.") from exc
    registration = seed.get("registration") if isinstance(seed, dict) else None
    if (
        not isinstance(registration, dict)
        or registration.get("contract_version") != "public-source-registration-v1"
    ):
        raise SourceRegistrationError("Seed has no supported source registration.")
    if not isinstance(registration.get("source"), dict) or not isinstance(
        registration.get("datasets"), list
    ):
        raise SourceRegistrationError("Seed source registration is incomplete.")
    return registration


def _request_json(
    client: httpx.Client, method: str, path: str, payload: dict[str, object]
) -> dict[str, object]:
    try:
        response = client.request(method, path, json=payload)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        try:
            error = exc.response.json().get("error")
        except (ValueError, AttributeError):
            error = None
        safe_error = error if error in SAFE_API_ERRORS else "http_error"
        message = (
            f"Data API returned HTTP {exc.response.status_code} "
            f"({safe_error}) at {path}."
        )
        raise SourceRegistrationError(message) from exc
    except httpx.TimeoutException as exc:
        raise SourceRegistrationError(f"Data API request timed out at {path}.") from exc
    except httpx.RequestError as exc:
        raise SourceRegistrationError(
            f"Data API transport failed at {path} ({type(exc).__name__})."
        ) from exc
    try:
        result = response.json()
    except ValueError as exc:
        raise SourceRegistrationError(
            f"Data API returned invalid JSON at {path}."
        ) from exc
    if not isinstance(result, dict):
        raise SourceRegistrationError("Data API returned an invalid response.")
    return result


def _dataset_statuses(
    result: dict[str, object], expected_ids: set[str]
) -> dict[str, str]:
    items = result.get("datasets")
    if not isinstance(items, list):
        raise SourceRegistrationError("Data API did not confirm dataset registrations.")
    statuses = {
        item.get("dataset_id"): item.get("status")
        for item in items
        if isinstance(item, dict)
    }
    if set(statuses) != expected_ids or any(
        status not in {"registered", "updated", "unchanged"}
        for status in statuses.values()
    ):
        raise SourceRegistrationError(
            "Data API registration readback did not match the seed."
        )
    return {
        dataset_id: str(statuses[dataset_id]) for dataset_id in sorted(expected_ids)
    }


def register_metadata(
    seed_path: Path,
    *,
    api_url: str,
    write_token: str,
    apply: bool,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, object]:
    """Validate, register, and exact-replay only the seed's metadata contract."""
    registration = _load_registration(seed_path)
    source = registration["source"]
    datasets = registration["datasets"]
    assert isinstance(source, dict) and isinstance(datasets, list)
    expected_ids = {
        item["dataset_id"]
        for item in datasets
        if isinstance(item, dict) and isinstance(item.get("dataset_id"), str)
    }
    if len(expected_ids) != len(datasets) or not expected_ids:
        raise SourceRegistrationError("Seed must declare unique dataset IDs.")
    summary: dict[str, object] = {
        "mode": "apply" if apply else "dry_run",
        "source_id": source.get("source_id"),
        "dataset_ids": sorted(expected_ids),
        "dataset_count": len(expected_ids),
        "records_sent": 0,
    }
    if not apply:
        return summary
    if not write_token.strip():
        raise SourceRegistrationError("PUBLIC_CATALOG_WRITE_TOKEN is not configured.")

    headers = {"Authorization": f"Bearer {write_token.strip()}"}
    try:
        with httpx.Client(
            base_url=_api_origin(api_url),
            headers=headers,
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
            transport=transport,
        ) as client:
            validation = _request_json(
                client, "POST", REGISTRATION_PATH + "/validate", registration
            )
            if (
                validation.get("status") != "valid"
                or set(validation.get("dataset_ids", [])) != expected_ids
                or validation.get("write_performed") is not False
            ):
                raise SourceRegistrationError("Data API rejected metadata validation.")

            first = _request_json(client, "POST", REGISTRATION_PATH, registration)
            first_statuses = _dataset_statuses(first, expected_ids)
            replay = _request_json(client, "POST", REGISTRATION_PATH, registration)
            replay_statuses = _dataset_statuses(replay, expected_ids)
            if replay.get("status") != "unchanged" or any(
                status != "unchanged" for status in replay_statuses.values()
            ):
                raise SourceRegistrationError(
                    "Exact metadata replay changed registered dataset state."
                )
    except httpx.HTTPError as exc:
        raise SourceRegistrationError(
            "Data API connection failed; details withheld."
        ) from exc

    return {
        **summary,
        "registration_status": first.get("status"),
        "registration_sha256": validation.get("registration_sha256"),
        "dataset_statuses": first_statuses,
        "exact_replay_status": replay.get("status"),
        "exact_replay_dataset_statuses": replay_statuses,
    }


def main() -> int:
    """Parse command-line arguments and run this tool."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Write metadata and verify exact replay."
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
    )
    args = parser.parse_args()
    token = os.environ.get("PUBLIC_CATALOG_WRITE_TOKEN", "") if args.apply else ""
    try:
        result = register_metadata(
            args.seed,
            api_url=args.api_url,
            write_token=token,
            apply=args.apply,
        )
    except SourceRegistrationError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
