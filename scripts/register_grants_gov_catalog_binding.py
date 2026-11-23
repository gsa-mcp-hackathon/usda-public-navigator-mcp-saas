"""Dry-run or register the reviewed Grants.gov catalog binding."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from pydantic import ValidationError

from example_server.registry import CatalogBindingDefinition

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "configs/registry/grants-gov-catalog-binding.json"


def catalog_binding_is_visible(snapshot: object, expected: dict[str, object]) -> bool:
    """Confirm a registered binding appears in a fresh public catalog read."""
    if not isinstance(snapshot, dict):
        return False
    if (
        snapshot.get("status") != "ok"
        or snapshot.get("cluster_id") != expected.get("cluster_id")
        or snapshot.get("catalog_namespace") != "programs"
        or not isinstance(snapshot.get("bindings"), list)
    ):
        return False
    keys = (
        "tenant_id",
        "cluster_id",
        "pattern_version",
        "dataset_id",
        "binding_context",
    )
    return any(
        isinstance(binding, dict)
        and all(binding.get(key) == expected.get(key) for key in keys)
        for binding in snapshot["bindings"]
    )


def main() -> int:
    """Run the catalog binding registration from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--api-url",
        default=os.environ.get("PERSISTENCE_API_URL", "http://127.0.0.1:18100"),
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=Path.home() / ".config/usda-mcp-saas/registry-write-token",
        help="Registry writer credential used for POST registration.",
    )
    parser.add_argument(
        "--catalog-reader-token-file",
        type=Path,
        help="Catalog reader credential used for fresh GET readback.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Register and read back the exact binding through the Data API.",
    )
    args = parser.parse_args()
    parsed = urlsplit(args.api_url)
    if not parsed.hostname or parsed.path not in {"", "/"}:
        parser.error("--api-url must be an API origin without a path.")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}
    ):
        parser.error("Use HTTPS or the VM loopback API origin.")
    stage = "manifest_validation"
    try:
        binding = CatalogBindingDefinition.model_validate_json(
            args.manifest.read_text(encoding="utf-8")
        )
        payload = binding.model_dump(mode="json")
        payload["cluster_id"] = payload.pop("pattern_id")
        result: dict[str, object] = {
            "status": "ready",
            "mode": "apply" if args.apply else "dry_run",
            "tenant_id": payload["tenant_id"],
            "cluster_id": payload["cluster_id"],
            "pattern_version": payload["pattern_version"],
            "dataset_id": payload["dataset_id"],
        }
        if args.apply:
            token = args.token_file.read_text(encoding="utf-8").strip()
            if not token:
                raise ValueError("Registry writer token file is empty.")
            stage = "registration"
            response = httpx.post(
                args.api_url.rstrip("/") + "/v1/registry/pattern-catalog-bindings",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            response.raise_for_status()
            readback = response.json()
            if not isinstance(readback, dict) or any(
                readback.get(key) != payload[key]
                for key in (
                    "tenant_id",
                    "cluster_id",
                    "pattern_version",
                    "dataset_id",
                    "binding_context",
                )
            ):
                raise ValueError("Data API registration response differed.")
            reader_token = (
                args.catalog_reader_token_file.read_text(encoding="utf-8").strip()
                if args.catalog_reader_token_file
                else token
            )
            if not reader_token:
                raise ValueError("Catalog reader token file is empty.")
            stage = "catalog_readback"
            catalog_response = httpx.get(
                args.api_url.rstrip("/")
                + f"/v1/patterns/{quote(payload['cluster_id'], safe='')}"
                + "/catalog-bindings/programs",
                headers={"Authorization": f"Bearer {reader_token}"},
                timeout=10.0,
            )
            catalog_response.raise_for_status()
            if not catalog_binding_is_visible(catalog_response.json(), payload):
                raise ValueError(
                    "Fresh Data API catalog read did not contain the binding."
                )
            result["status"] = "registered_and_read_back"
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except httpx.HTTPStatusError as exc:
        print(
            f"Catalog binding failed (stage={stage}, "
            f"http_status={exc.response.status_code}).",
            file=sys.stderr,
        )
        return 1
    except (
        OSError,
        ValueError,
        ValidationError,
        httpx.HTTPError,
        json.JSONDecodeError,
    ) as exc:
        print(
            f"Catalog binding failed (stage={stage}, error={type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
